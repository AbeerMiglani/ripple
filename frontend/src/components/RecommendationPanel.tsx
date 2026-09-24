/**
 * RecommendationPanel — the rank-1 candidate gets the mockup's staged flow:
 * "Review intervention" shows a draft compare (baseline vs. an *estimated*
 * projection from the recommendation's own fields) with nothing sent to the
 * API yet; "Run verified rerun" is what actually creates the scenario, runs
 * the simulation, and polls it — the same request sequence the previous
 * one-click "Apply" used, just deferred behind an explicit review step so
 * the projected and measured numbers are never shown as the same thing.
 *
 * Lower-ranked candidates keep the original one-click apply — the mockup
 * itself only stages the top candidate; alternatives are informational.
 */
import { useState } from "react";
import { useSimulationStore, StoredScenario } from "../stores/simulationStore";
import { useUIStore } from "../stores/uiStore";
import { createScenario, useMitigations, useNetworkTopology, pollSimulationUntilSettled } from "../api/hooks";
import { apiPost } from "../api/client";
import type { InfraNode, MitigationRecommendation, Modification, SimulationResult } from "../types";
import { comparablePopulation } from "../types";
import { failedNodeIdsForResult } from "../utils/derive";
import ProvenanceTag from "./shared/ProvenanceTag";
import Banner from "./shared/Banner";
import Section from "./shared/Section";
import { useDemoStore } from "../demo/demoStore";
import { useNodeLookup } from "../hooks/useNodeLookup";

function interventionLabel(rec: MitigationRecommendation, nodeLookup: Map<string, InfraNode>) {
  const isAddEdge = rec.intervention_type === "add_edge";
  const srcName = rec.display_name || rec.node_name || nodeLookup.get(rec.node_id)?.display_name || rec.node_id.slice(0, 8);
  const tgtName = rec.target_display_name || rec.target_node_name || (rec.target_node_id ? `Node ${rec.target_node_id.slice(0, 8)}` : "");
  return {
    isAddEdge,
    title: isAddEdge ? "Add redundancy link" : "Upgrade node capacity",
    subject: isAddEdge ? `${srcName} ➔ ${tgtName}` : srcName,
  };
}

/**
 * Plain-language description of what the scenario actually changed.
 *
 * The panel used to run a real scenario and a real rerun without ever telling
 * the reader which asset was modified or how, so an intervention that changed
 * nothing visible was indistinguishable from one that had not run.
 */
function describeModifications(
  rec: MitigationRecommendation,
  nodeLookup: Map<string, InfraNode>
): string[] {
  const nameOf = (id: string) =>
    nodeLookup.get(id)?.display_name || nodeLookup.get(id)?.name || id.slice(0, 8);

  return (rec.scenario_payload?.modifications ?? []).map((mod: Modification) => {
    if (mod.type === "upgrade_node") {
      const from = nodeLookup.get(mod.node_id)?.capacity;
      const to = mod.capacity ?? rec.proposed_capacity;
      const range = from != null && to != null ? `${from.toFixed(0)} → ${Number(to).toFixed(0)}` : `${to ?? "?"}`;
      return `Capacity at ${nameOf(mod.node_id)}: ${range}`;
    }
    if (mod.type === "add_edge") {
      // edge_type values are already noun phrases ("power_supply", "road_link"),
      // so appending "link" unconditionally produced "New road link link".
      const kind = String(mod.edge_type ?? "power_supply").replace(/_/g, " ");
      const phrase = kind.endsWith("link") ? kind : `${kind} link`;
      return `New ${phrase}: ${nameOf(mod.source)} → ${nameOf(mod.target)}`;
    }
    // Defensive fallback only: the wire payload is trusted JSON, not something
    // the type system verifies, so an unrecognized `type` is still possible.
    return `Modification: ${(mod as { type?: string }).type ?? "unknown"}`;
  });
}

/**
 * What the rerun changed, asset by asset.
 *
 * `saved` are assets that failed in the baseline and survive under the
 * intervention. `newlyAffected` is the other direction, and it is not
 * hypothetical: the engine can return a candidate whose failure *count* is
 * unchanged while the identity of the failed assets differs, which is why a
 * population figure can move with no change in the headline number.
 */
function interventionOutcome(baseline: SimulationResult, scenario: SimulationResult) {
  const before = failedNodeIdsForResult(baseline);
  const after = failedNodeIdsForResult(scenario);
  const saved = [...before].filter((id) => !after.has(id));
  const newlyAffected = [...after].filter((id) => !before.has(id));
  return { saved, newlyAffected };
}

/** The top-ranked candidate plus this many minus one alternatives. */
const PANEL_LIMIT = 5;

export default function RecommendationPanel() {
  const result = useSimulationStore((s) => s.result);
  const isRunning = useSimulationStore((s) => s.isRunning);
  const setSimulationResult = useSimulationStore((s) => s.setSimulationResult);
  const baselineSimulationId = useSimulationStore((s) => s.baselineSimulationId);
  const setBaselineSimulationId = useSimulationStore((s) => s.setBaselineSimulationId);
  const registerScenario = useSimulationStore((s) => s.registerScenario);
  const setLastAppliedScenarioId = useSimulationStore((s) => s.setLastAppliedScenarioId);
  const addSimulation = useSimulationStore((s) => s.addSimulation);
  const stopDemo = useDemoStore((s) => s.stop);
  const setSavedNodes = useSimulationStore((s) => s.setSavedNodes);
  const networkId = useUIStore((s) => s.networkId);
  const { data: topology } = useNetworkTopology(networkId);

  const nodeLookup = useNodeLookup(topology?.nodes);

  // Rank-1 staged review state. Snapshotting the recommendation + baseline at
  // the moment review starts (rather than re-reading the live `result` /
  // `recommendations` throughout) matters: once the verified rerun completes,
  // setSimulationResult swaps the store's active `result` over to the
  // scenario's own result, and the recommendations query then refetches for
  // *that* id — both would otherwise pull the rug out from under a compare
  // table that's supposed to keep showing the same before/after the whole
  // time.
  const [reviewSnapshot, setReviewSnapshot] = useState<{ rec: MitigationRecommendation; baseline: SimulationResult } | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [verified, setVerified] = useState<{ baseline: SimulationResult; scenario: SimulationResult } | null>(null);
  // Which intervention was last actually applied, and what it did. Separate
  // from `verified`, which drives the rank-1 compare table only: a one-click
  // apply on an alternative is just as much an applied intervention and needs
  // the same explanation, so both paths write here.
  const [appliedOutcome, setAppliedOutcome] = useState<{
    rec: MitigationRecommendation;
    baseline: SimulationResult;
    scenario: SimulationResult;
  } | null>(null);
  const [showAlts, setShowAlts] = useState(false);
  const reviewing = !!reviewSnapshot;

  // Lower-rank one-click apply state (unchanged behaviour).
  const [applied, setApplied] = useState<Set<number>>(new Set());
  const [applying, setApplying] = useState<Set<number>>(new Set());
  const [applyErrors, setApplyErrors] = useState<Map<number, string>>(new Map());

  // Shared with ImpactSummary and ExplainPanel: one request per run, sliced here.
  const { data: mitigations, isLoading, isError, error } = useMitigations(
    result?.status === "completed" ? result.id : null
  );
  const recommendations = mitigations?.slice(0, PANEL_LIMIT);

  const runInterventionSequence = async (rec: MitigationRecommendation, baseline: SimulationResult) => {
    // Ensure baselineSimulationId is saved before running the what-if scenario.
    if (!baselineSimulationId) setBaselineSimulationId(baseline.id);

    const scenario = await createScenario(rec.scenario_payload);

    const entry: StoredScenario = {
      id: scenario.id,
      name: scenario.name,
      network_id: scenario.network_id,
      initial_failures: scenario.initial_failures,
      intervention_type: rec.intervention_type,
      created_at: scenario.created_at ?? new Date().toISOString(),
    };
    registerScenario(entry);
    setLastAppliedScenarioId(scenario.id);

    const simData = await apiPost<SimulationResult>(
      "/api/simulations",
      {
        network_id: rec.scenario_payload.network_id,
        initial_failures: rec.scenario_payload.initial_failures,
        scenario_id: scenario.id,
      },
      "Failed to start simulation"
    );

    const pollData = await pollSimulationUntilSettled(simData.id);
    addSimulation(
      {
        id: pollData.id,
        network_id: pollData.network_id,
        initial_failures: pollData.initial_failures,
        total_failed: pollData.total_failed,
        is_baseline: false,
        scenario_id: scenario.id,
        created_at: new Date().toISOString(),
      },
      false
    );
    return pollData;
  };

  const handleRunVerifiedRerun = async () => {
    if (!reviewSnapshot) return;
    setVerifying(true);
    setVerifyError(null);
    try {
      const pollData = await runInterventionSequence(reviewSnapshot.rec, reviewSnapshot.baseline);
      setVerified({ baseline: reviewSnapshot.baseline, scenario: pollData });
      // The rerun replaces the shared result; stop any demo still applying beats.
      stopDemo();
      setSimulationResult(pollData);
      // After adoption, not before: setSimulationResult clears the highlight.
      setSavedNodes(new Set(interventionOutcome(reviewSnapshot.baseline, pollData).saved));
      setAppliedOutcome({ rec: reviewSnapshot.rec, baseline: reviewSnapshot.baseline, scenario: pollData });
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Unknown error running the verified rerun.");
    } finally {
      setVerifying(false);
    }
  };

  const handleApply = async (rec: MitigationRecommendation) => {
    if (!result) return;
    setApplying((prev) => new Set(prev).add(rec.rank));
    setApplyErrors((prev) => {
      const m = new Map(prev);
      m.delete(rec.rank);
      return m;
    });
    try {
      const baselineBefore = result;
      const pollData = await runInterventionSequence(rec, baselineBefore);
      stopDemo();
      setSimulationResult(pollData);
      setSavedNodes(new Set(interventionOutcome(baselineBefore, pollData).saved));
      setAppliedOutcome({ rec, baseline: baselineBefore, scenario: pollData });
      setApplied((prev) => new Set(prev).add(rec.rank));
    } catch (err) {
      setApplyErrors((prev) => new Map(prev).set(rec.rank, err instanceof Error ? err.message : "Unknown error."));
    } finally {
      setApplying((prev) => {
        const next = new Set(prev);
        next.delete(rec.rank);
        return next;
      });
    }
  };

  if (!result || result.status !== "completed") {
    return (
      <Section title="Recommendation" state={isRunning ? "Generating" : "Unavailable"}>
        <div style={{ padding: 16, border: "1px dashed var(--rp-divider-strong)", display: "flex", flexDirection: "column", gap: 6, textAlign: "center" }}>
          <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 14, color: "var(--rp-mute)" }}>
            {isRunning ? "Generating recommendations" : "No recommendation yet"}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--rp-dim)", lineHeight: 1.5 }}>
            {isRunning
              ? "Candidates are resimulated once the baseline cascade settles."
              : "Run a simulation to generate ranked, resimulated interventions."}
          </span>
        </div>
      </Section>
    );
  }

  const top = recommendations?.[0];
  const alts = recommendations?.slice(1) ?? [];

  return (
    <Section
      title="Recommendation"
      state={recommendations ? (reviewing ? "In review" : `Rank 1 of ${recommendations.length}`) : isLoading ? "Loading" : "Unavailable"}
    >
      {isLoading && <div style={{ color: "var(--rp-mute)", fontSize: 13 }}>Loading recommendations…</div>}
      {isError && (
        <div style={{ color: "var(--rp-wave-0)", fontSize: 13 }}>
          Failed to load recommendations{error instanceof Error ? `: ${error.message}` : "."}
        </div>
      )}
      {recommendations && recommendations.length === 0 && <div style={{ color: "var(--rp-mute)", fontSize: 13 }}>No recommendations available.</div>}

      {(top || reviewSnapshot) && (() => {
        // Frozen once review starts — see the reviewSnapshot state comment.
        // The outer guard checks `top || reviewSnapshot`, not just `top`,
        // because verifying changes the active result's id, which re-keys
        // the recommendations query — `top` can go briefly (or permanently,
        // if the improved network has nothing left to recommend) undefined
        // while a review/verification is in progress. Falling back to the
        // snapshot keeps the card (and a completed "Verified" state) mounted
        // through that refetch instead of vanishing mid-review.
        const activeRec = reviewSnapshot?.rec ?? top;
        const activeBaseline = reviewSnapshot?.baseline ?? result;
        if (!activeRec || !activeBaseline) return null;
        const { title, subject } = interventionLabel(activeRec, nodeLookup);
        const baselinePop = comparablePopulation(activeBaseline);
        const projectedFailed = activeBaseline.total_failed - activeRec.failures_prevented;
        const projectedPop = baselinePop - activeRec.raw_population_saved;
        // Only meaningful once a rerun exists; the empty default keeps the
        // outcome block's reads total without a null check at every use.
        const outcome = appliedOutcome
          ? interventionOutcome(appliedOutcome.baseline, appliedOutcome.scenario)
          : { saved: [] as string[], newlyAffected: [] as string[] };

        return (
          <div className="rp-blueprint" style={{ padding: 13, display: "flex", flexDirection: "column", gap: 10, background: "var(--rp-surface-3)" }}>
            <i className="rp-corner tl" />
            <i className="rp-corner tr" />
            <i className="rp-corner bl" />
            <i className="rp-corner br" />
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", padding: "2px 7px", background: "var(--rp-accent)", color: "#0b0f14", fontWeight: 500 }}>
                Rank 1
              </span>
              {/* `verified` is local state, set only once a rerun has actually
                  completed -- deliberately not the API's `verified` field, which
                  is hardcoded true on every candidate. Until then these figures
                  are an in-memory projection, and calling them verified
                  contradicted both the "Pending" column below and
                  buildExplanation's own wording in utils/derive. */}
              {verified ? (
                <ProvenanceTag kind="verified" label="Verified by resimulation" />
              ) : (
                <ProvenanceTag kind="derived" label="Projected · not yet rerun" />
              )}
              {activeRec.protects_critical_services && <ProvenanceTag kind="estimated" label="Protects hospital" />}
            </div>
            <div>
              <h4 style={{ fontSize: 19, marginBottom: 3 }}>{title}</h4>
              <p style={{ margin: 0, fontSize: 12.5, color: "var(--rp-text-dim)", lineHeight: 1.5 }}>
                {activeRec.intervention_type === "add_edge" ? "Add a redundancy link: " : "Harden "}
                <strong style={{ color: "var(--rp-text)" }}>{subject}</strong>
                {activeRec.proposed_capacity != null && (
                  <>
                    {" "}
                    — capacity <span style={{ fontVariantNumeric: "tabular-nums" }}>→ {activeRec.proposed_capacity.toFixed(1)}</span>
                  </>
                )}
                .
              </p>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, background: "var(--rp-divider)" }}>
              <div style={{ padding: "9px 10px", background: "var(--rp-surface-3)", display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--rp-mute)" }}>Failures prevented</span>
                <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 24, color: "var(--rp-teal-bright)", fontVariantNumeric: "tabular-nums" }}>
                  −{activeRec.failures_prevented}
                </span>
                <ProvenanceTag kind={verified ? "verified" : "derived"} label={verified ? undefined : "Projected"} />
              </div>
              <div style={{ padding: "9px 10px", background: "var(--rp-surface-3)", display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--rp-mute)" }}>Population saved</span>
                <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 24, color: "var(--rp-teal-bright)", fontVariantNumeric: "tabular-nums" }}>
                  {activeRec.raw_population_saved.toLocaleString()}
                </span>
                <ProvenanceTag kind="estimated" label="Estimated · uncapped" />
              </div>
            </div>
            <Banner tone="success">
              Evaluated by in-memory Motter–Lai resimulation, not a heuristic estimate. Efficiency gain{" "}
              {(activeRec.efficiency_gain * 100).toFixed(1)}%.
            </Banner>

            {!reviewing && !verified && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="rp-btn rp-btn-primary" style={{ flex: "1 1 150px" }} onClick={() => setReviewSnapshot({ rec: activeRec, baseline: activeBaseline })}>
                  Review intervention
                </button>
                {alts.length > 0 && (
                  <button className="rp-btn rp-btn-secondary" onClick={() => setShowAlts((v) => !v)}>
                    View alternatives ({alts.length})
                  </button>
                )}
              </div>
            )}

            {(reviewing || verified) && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 2 }}>
                {/* Header cells need minWidth: 0. A grid item defaults to
                    min-width: auto, so it refuses to shrink below its content
                    and any wide child pushes past the cell edge into the column
                    beside it. The provenance pills were exactly that: at the
                    372px sidebar these cells are 74px, and each pill overflowed
                    by 16px. They now live on one line under the table instead of
                    three pills competing across four narrow columns. */}
                <div style={{ display: "grid", gridTemplateColumns: "minmax(90px,1.2fr) repeat(3, minmax(60px,1fr))", gap: 1, background: "var(--rp-divider)" }}>
                  <div style={{ background: "var(--rp-surface-2)", padding: "7px 9px", minWidth: 0, fontSize: 9.5, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--rp-dim)" }}>
                    Metric
                  </div>
                  <div style={{ background: "var(--rp-surface-2)", padding: "7px 9px", minWidth: 0 }}>
                    <div style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 12 }}>Baseline</div>
                  </div>
                  <div style={{ background: "var(--rp-surface-2)", padding: "7px 9px", minWidth: 0 }}>
                    <div style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 12 }}>Proposed</div>
                  </div>
                  <div
                    style={{
                      background: verified ? "rgba(79,174,131,.12)" : "var(--rp-surface-2)",
                      padding: "7px 9px",
                      minWidth: 0,
                      borderLeft: `1px solid ${verified ? "rgba(79,174,131,.4)" : "var(--rp-divider)"}`,
                    }}
                  >
                    <div style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 12, color: verified ? "var(--rp-teal-bright)" : "var(--rp-mute)" }}>
                      Verified
                    </div>
                  </div>

                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontSize: 11.5, color: "var(--rp-text-dim)" }}>Failed assets</div>
                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 15, fontVariantNumeric: "tabular-nums" }}>
                    {activeBaseline.total_failed}
                  </div>
                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 15, fontVariantNumeric: "tabular-nums", color: "var(--rp-wave-2)" }}>
                    {projectedFailed}
                  </div>
                  <div
                    style={{
                      background: verified ? "rgba(79,174,131,.09)" : "var(--rp-surface-3)",
                      padding: "8px 9px",
                      fontFamily: "var(--rp-font-heading)",
                      fontWeight: 600,
                      fontSize: 15,
                      fontVariantNumeric: "tabular-nums",
                      color: verified ? "var(--rp-teal-bright)" : "var(--rp-mute)",
                      borderLeft: `1px solid ${verified ? "rgba(79,174,131,.4)" : "var(--rp-divider)"}`,
                    }}
                  >
                    {verified ? verified.scenario.total_failed : "pending"}
                  </div>

                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontSize: 11.5, color: "var(--rp-text-dim)" }}>Population (uncapped)</div>
                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 15, fontVariantNumeric: "tabular-nums" }}>
                    {baselinePop.toLocaleString()}
                  </div>
                  <div style={{ background: "var(--rp-surface-3)", padding: "8px 9px", fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 15, fontVariantNumeric: "tabular-nums", color: "var(--rp-wave-2)" }}>
                    {projectedPop.toLocaleString()}
                  </div>
                  <div
                    style={{
                      background: verified ? "rgba(79,174,131,.09)" : "var(--rp-surface-3)",
                      padding: "8px 9px",
                      fontFamily: "var(--rp-font-heading)",
                      fontWeight: 600,
                      fontSize: 15,
                      fontVariantNumeric: "tabular-nums",
                      color: verified ? "var(--rp-teal-bright)" : "var(--rp-mute)",
                      borderLeft: `1px solid ${verified ? "rgba(79,174,131,.4)" : "var(--rp-divider)"}`,
                    }}
                  >
                    {verified ? comparablePopulation(verified.scenario).toLocaleString() : "pending"}
                  </div>
                </div>

                {/* One provenance line for the whole table, replacing a pill per
                    column. Says the same thing in the space the columns cannot
                    spare, and tracks whether the rerun has actually happened. */}
                <div style={{ fontSize: 10.5, color: "var(--rp-faint)", lineHeight: 1.5 }}>
                  <strong style={{ color: "var(--rp-mute)", fontWeight: 600 }}>Baseline</strong> simulated ·{" "}
                  <strong style={{ color: "var(--rp-wave-2)", fontWeight: 600 }}>Proposed</strong> estimated ·{" "}
                  <strong style={{ color: verified ? "var(--rp-teal-bright)" : "var(--rp-mute)", fontWeight: 600 }}>Verified</strong>{" "}
                  {verified ? "measured by rerun" : "pending — not yet rerun"}
                </div>

                {!verified && (
                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "9px 10px", border: "1px solid var(--rp-divider)", background: "var(--rp-surface-3)" }}>
                    <span style={{ fontSize: 11.5, color: "var(--rp-text-dim)", lineHeight: 1.5, flex: "1 1 220px" }}>
                      The proposed column is a projection. Nothing is claimed as fact until the deterministic engine
                      reruns the cascade on the modified network.
                    </span>
                    <button className="rp-btn rp-btn-primary" onClick={handleRunVerifiedRerun} disabled={verifying}>
                      {verifying ? "Running…" : "Run verified rerun"}
                    </button>
                    <button className="rp-btn rp-btn-secondary" onClick={() => setReviewSnapshot(null)} disabled={verifying}>
                      Discard draft
                    </button>
                  </div>
                )}
                {verified && (
                  <Banner tone="success">
                    <strong style={{ color: "var(--rp-teal-bright)" }}>Measured, not projected.</strong> The verified column
                    is the engine's own result for the modified graph — same seed, same initial failure,{" "}
                    {verified.scenario.total_failed} assets failed against a baseline of {verified.baseline.total_failed}.
                  </Banner>
                )}
                {verifyError && (
                  <Banner tone="error" action={<button className="rp-btn rp-btn-secondary" onClick={handleRunVerifiedRerun}>Retry</button>}>
                    {verifyError}
                  </Banner>
                )}
              </div>
            )}

            {appliedOutcome && (
              <div
                className="rp-blueprint"
                style={{ padding: 11, display: "flex", flexDirection: "column", gap: 9, background: "var(--rp-surface-2)" }}
              >
                <i className="rp-corner tl" />
                <i className="rp-corner br" />
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-dim)" }}>
                    What this intervention did
                  </span>
                  <ProvenanceTag kind="verified" label="Measured by rerun" />
                </div>

                {/* What was actually changed. Without this the reader sees two
                    numbers move and has to infer the edit from the card title. */}
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {describeModifications(appliedOutcome.rec, nodeLookup).map((line, i) => (
                    <span key={i} style={{ fontSize: 12, color: "var(--rp-text)", fontVariantNumeric: "tabular-nums" }}>
                      {line}
                    </span>
                  ))}
                </div>

                {outcome.saved.length > 0 ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    <span style={{ fontSize: 11.5, color: "var(--rp-text-dim)" }}>
                      <strong style={{ color: "var(--rp-teal-bright)" }}>
                        {outcome.saved.length} asset{outcome.saved.length === 1 ? "" : "s"} stayed online
                      </strong>{" "}
                      that failed in the baseline — ringed on the map.
                    </span>
                    <span style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {outcome.saved.map((id) => (
                        <span
                          key={id}
                          style={{
                            fontSize: 10.5,
                            padding: "2px 6px",
                            background: "rgba(127,208,168,.12)",
                            border: "1px solid var(--rp-teal)",
                            color: "var(--rp-teal-bright)",
                          }}
                        >
                          {nodeLookup.get(id)?.display_name || nodeLookup.get(id)?.name || id.slice(0, 8)}
                        </span>
                      ))}
                    </span>
                  </div>
                ) : (
                  /* A finding, not a failure to render. Capacity cannot help an
                     asset that lost every supplier, so a zero here is the
                     engine being honest about this cascade. */
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ fontSize: 11.5, color: "var(--rp-warn-soft)" }}>
                      <strong>No assets were saved.</strong> The same {appliedOutcome.scenario.total_failed} assets went
                      offline with this change in place.
                    </span>
                    {appliedOutcome.rec.root_cause_detail && (
                      <span style={{ fontSize: 11.5, color: "var(--rp-mute)", lineHeight: 1.5 }}>
                        {appliedOutcome.rec.root_cause_detail}
                      </span>
                    )}
                    <div>
                      <ProvenanceTag kind="derived" label="Measured · no change" />
                    </div>
                  </div>
                )}

                {outcome.newlyAffected.length > 0 && (
                  <span style={{ fontSize: 11.5, color: "var(--rp-wave-2)" }}>
                    {outcome.newlyAffected.length} asset{outcome.newlyAffected.length === 1 ? "" : "s"} failed that
                    had not before:{" "}
                    {outcome.newlyAffected
                      .map((id) => nodeLookup.get(id)?.display_name || nodeLookup.get(id)?.name || id.slice(0, 8))
                      .join(", ")}
                    .
                  </span>
                )}
              </div>
            )}

            {showAlts && alts.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.12)", marginTop: 2 }}>
                {alts.map((a) => {
                  const isApplied = applied.has(a.rank);
                  const isApplying = applying.has(a.rank);
                  const label = interventionLabel(a, nodeLookup);
                  return (
                    <div key={a.rank} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px", background: "var(--rp-surface-3)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 12, color: "var(--rp-faint)" }}>#{a.rank}</span>
                        <span style={{ display: "flex", flexDirection: "column", gap: 1, flex: 1, minWidth: 0 }}>
                          <span style={{ fontSize: 12, color: "var(--rp-text)" }}>{label.title}</span>
                          <span style={{ fontSize: 10.5, color: "var(--rp-mute)" }}>{label.subject}</span>
                        </span>
                        <span style={{ textAlign: "right", fontSize: 10.5, color: "var(--rp-mute)", fontVariantNumeric: "tabular-nums", lineHeight: 1.4 }}>
                          −{a.failures_prevented} failures
                          <br />
                          {a.raw_population_saved.toLocaleString()} pop
                        </span>
                      </div>
                      {/* Some add_edge candidates carry no failures/population
                          saved at all — they reconnect an asset that survived
                          the baseline in isolation, so the only real effect is
                          structural: a small global-efficiency gain. Without a
                          label a "0 / 0" candidate reads as a broken
                          recommendation rather than the honest "nothing
                          concrete failed here" it actually is. */}
                      {a.failures_prevented === 0 && a.raw_population_saved === 0 && !a.protects_critical_services && (
                        <div>
                          <ProvenanceTag kind="derived" label="Structural resilience only · no immediate failures" />
                        </div>
                      )}
                      <div style={{ display: "flex", justifyContent: "flex-end" }}>
                        <button
                          className="rp-btn rp-btn-secondary"
                          style={{ fontSize: 11 }}
                          onClick={() => handleApply(a)}
                          disabled={isApplied || isApplying}
                        >
                          {isApplied ? "✓ Applied" : isApplying ? "…" : "Apply"}
                        </button>
                      </div>
                      {applyErrors.get(a.rank) && (
                        <div style={{ fontSize: 11, color: "var(--rp-wave-0)" }}>⚠ {applyErrors.get(a.rank)}</div>
                      )}
                    </div>
                  );
                })}
                <p style={{ margin: 0, padding: "7px 10px", background: "var(--rp-surface-3)", fontSize: 10.5, color: "var(--rp-faint)", lineHeight: 1.45 }}>
                  Ranked on failures prevented first; hospital protection breaks ties within a tier — a larger population
                  figure alone does not outrank.
                </p>
              </div>
            )}
          </div>
        );
      })()}
    </Section>
  );
}
