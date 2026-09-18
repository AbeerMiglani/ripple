/**
 * ExplainPanel — what happened, and what to do about it.
 *
 * Two modes:
 *
 * 1. A node is selected → an inspection card for that asset: its name, whether
 *    it is offline, WHY (the engine's own root cause, e.g. "Transit edge
 *    severed"), and one plain-language recommended action. This replaces a
 *    background data dump that told a user clicking a blocked road junction
 *    everything except what to do about it.
 *
 * 2. Nothing selected → the run-level narrative, as before.
 *
 * Either way the internal field names (`waves · total_failed`,
 * `raw_population_affected`) and the raw-field table move into the Advanced
 * diagnostics drawer. They are still there for anyone checking the engine's
 * working; they are no longer the first thing a newcomer reads.
 *
 * This is templating, not an LLM call — see utils/derive.ts.
 */
import React, { useMemo } from "react";
import { useSimulationStore } from "../stores/simulationStore";
import { useCompareScenarios, useMitigations, useNetworkTopology } from "../api/hooks";
import { useUIStore } from "../stores/uiStore";
import { buildExplanation, failedNodeIdsForResult, rawResultFields } from "../utils/derive";
import { NODE_LABELS } from "../types";
import type { MitigationRecommendation } from "../types";
import Section from "./shared/Section";
import AdvancedDetails from "./shared/AdvancedDetails";
import ProvenanceTag from "./shared/ProvenanceTag";

/**
 * Plain-language root cause.
 *
 * Prefers the engine's own explanation for this asset. Falls back to the
 * generic shape of the run when no mitigation mentions the node — an asset can
 * be offline without being a worthwhile place to intervene.
 */
function describeRootCause(
  offline: boolean,
  initialFailure: boolean,
  mitigation: MitigationRecommendation | undefined
): string {
  if (!offline) return "Operating normally in this scenario.";
  if (initialFailure) return "Directly hit by the initiating event.";
  if (mitigation?.root_cause_detail) return mitigation.root_cause_detail;
  return "Knocked out by the cascade spreading from upstream failures.";
}

export default function ExplainPanel({
  topRecommendationRank,
  topRecommendationPrevented,
}: {
  topRecommendationRank?: number;
  topRecommendationPrevented?: number;
}) {
  const result = useSimulationStore((s) => s.result);
  const networkId = useUIStore((s) => s.networkId);
  const selectedNodeIds = useUIStore((s) => s.selectedNodeIds);
  const hoveredNodeId = useUIStore((s) => s.hoveredNodeId);
  const baselineSimulationId = useSimulationStore((s) => s.baselineSimulationId);
  const lastAppliedScenarioId = useSimulationStore((s) => s.lastAppliedScenarioId);
  const { data: topology } = useNetworkTopology(networkId);

  const completedId = result && result.status === "completed" ? result.id : null;
  const { data: mitigations } = useMitigations(completedId, 10);

  // If the current result *is* the scenario re-run of the last applied
  // scenario, pull the baseline/scenario pair so the explanation can speak
  // to the verified rerun in measured terms rather than "not yet run".
  const isLikelyVerifiedRun =
    !!result && !!lastAppliedScenarioId && !!baselineSimulationId && result.id !== baselineSimulationId;
  const { data: comparison } = useCompareScenarios(
    isLikelyVerifiedRun ? baselineSimulationId : null,
    isLikelyVerifiedRun ? lastAppliedScenarioId : null
  );

  const entries = useMemo(() => {
    if (!result || result.status !== "completed" || !topology?.nodes) return null;
    return buildExplanation({
      result,
      nodes: topology.nodes,
      topRecommendation:
        topRecommendationRank != null && topRecommendationPrevented != null
          ? { rank: topRecommendationRank, failures_prevented: topRecommendationPrevented }
          : null,
      verifiedComparison:
        comparison && comparison.scenario_result.id === result.id
          ? { baseline: comparison.baseline_result, scenario: comparison.scenario_result }
          : null,
    });
  }, [result, topology, comparison, topRecommendationRank, topRecommendationPrevented]);

  // The inspected asset: an explicit selection wins, otherwise whatever the
  // map or graph is hovering, so pointing at a node already tells you about it.
  const inspected = useMemo(() => {
    if (!topology?.nodes) return null;
    const id = selectedNodeIds.size === 1 ? [...selectedNodeIds][0] : hoveredNodeId;
    if (!id) return null;
    return topology.nodes.find((n) => n.id === id) ?? null;
  }, [topology, selectedNodeIds, hoveredNodeId]);

  if (!result || result.status !== "completed") return null;

  const available = !!entries && entries.length > 0;
  const failedIds = failedNodeIdsForResult(result);

  // ---- Mode 1: a single asset is being inspected --------------------------
  if (inspected) {
    const offline = failedIds.has(inspected.id);
    const initialFailure = result.initial_failures.includes(inspected.id);
    const mitigation = mitigations?.find(
      (m) => m.node_id === inspected.id || m.target_node_id === inspected.id
    );
    const action =
      mitigation?.action_label ||
      (offline
        ? "No single intervention modelled for this asset — see the mitigations panel."
        : "No action needed.");

    return (
      <Section title="Asset inspection" state={offline ? "Offline" : "Operational"} defaultOpen>
        <div
          className="rp-blueprint"
          style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10, background: "var(--rp-surface-3)" }}
        >
          <i className="rp-corner tl" />
          <i className="rp-corner br" />

          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: "var(--rp-text-xl)" }}>
              {inspected.display_name || inspected.name}
            </span>
            <span
              style={{
                fontSize: "var(--rp-text-xs)",
                textTransform: "uppercase",
                letterSpacing: "0.1em",
                color: "var(--rp-mute)",
              }}
            >
              {NODE_LABELS[inspected.node_type] || inspected.node_type}
            </span>
            <span
              style={{
                marginLeft: "auto",
                fontSize: "var(--rp-text-sm)",
                fontWeight: 600,
                color: offline ? "var(--rp-wave-0)" : "var(--rp-ok)",
              }}
            >
              {offline ? "Failed" : "Operational"}
            </span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span style={{ fontSize: "var(--rp-text-xs)", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-dim)" }}>
              Root cause
            </span>
            <p style={{ margin: 0, fontSize: "var(--rp-text-md)", color: "var(--rp-text-dim)", lineHeight: 1.55 }}>
              {describeRootCause(offline, initialFailure, mitigation)}
            </p>
          </div>

          {/* The action card: the one thing the reader is meant to act on. */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 5,
              padding: "10px 11px",
              background: "rgba(92,178,166,.10)",
              borderLeft: "3px solid var(--rp-teal-bright)",
            }}
          >
            <span style={{ fontSize: "var(--rp-text-xs)", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-teal-bright)" }}>
              Recommended action
            </span>
            <span style={{ fontSize: "var(--rp-text-lg)", color: "var(--rp-text)", lineHeight: 1.45 }}>
              {action}
            </span>
            {mitigation && (
              <span style={{ fontSize: "var(--rp-text-sm)", color: "var(--rp-mute)" }}>
                Prevents {mitigation.failures_prevented} further failure
                {mitigation.failures_prevented === 1 ? "" : "s"} and protects{" "}
                {mitigation.raw_population_saved.toLocaleString()} people, verified by resimulation.
              </span>
            )}
          </div>

          <AdvancedDetails label="Asset details" hint="id · provenance">
            <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.12)" }}>
              {[
                { k: "id", v: inspected.id },
                { k: "capacity", v: String(inspected.capacity) },
                { k: "current_load", v: String(inspected.current_load) },
                { k: "failure_threshold", v: String(inspected.failure_threshold) },
                { k: "population_served", v: inspected.population_served.toLocaleString() },
                { k: "name_source", v: inspected.name_source || inspected.data_source || "synthetic" },
                { k: "data_quality", v: inspected.data_quality || "estimated" },
                ...(mitigation?.root_cause ? [{ k: "root_cause", v: mitigation.root_cause }] : []),
              ].map((f) => (
                <div key={f.k} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "5px 8px", background: "var(--rp-surface-3)" }}>
                  <span style={{ fontFamily: "var(--rp-font-mono)", fontSize: "var(--rp-text-xs)", color: "var(--rp-dim)" }}>{f.k}</span>
                  <span style={{ marginLeft: "auto", fontSize: "var(--rp-text-sm)", color: "var(--rp-text)", fontVariantNumeric: "tabular-nums" }}>{f.v}</span>
                </div>
              ))}
            </div>
          </AdvancedDetails>
        </div>
      </Section>
    );
  }

  // ---- Mode 2: the run-level narrative ------------------------------------
  return (
    <Section title="Explain this result" state={available ? "Assembled" : "Unavailable"} defaultOpen>
      {available ? (
        <div
          className="rp-blueprint"
          style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10, background: "var(--rp-surface-3)" }}
        >
          <i className="rp-corner tl" />
          <i className="rp-corner br" />
          <span style={{ fontSize: "var(--rp-text-sm)", color: "var(--rp-mute)", lineHeight: 1.45 }}>
            Select an asset on the map to see why it failed and what to do about it.
          </span>
          {entries!.map((x, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ width: 8, height: 8, background: x.color, flexShrink: 0 }} />
                <span style={{ fontSize: "var(--rp-text-xs)", letterSpacing: "0.13em", textTransform: "uppercase", color: x.color }}>
                  {x.category}
                </span>
              </span>
              <p style={{ margin: 0, fontSize: "var(--rp-text-md)", color: "var(--rp-text-dim)", lineHeight: 1.55 }}>{x.text}</p>
            </div>
          ))}

          <AdvancedDetails hint="source fields">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span style={{ fontSize: "var(--rp-text-xs)", color: "var(--rp-dim)", lineHeight: 1.45 }}>
                Each paragraph above is assembled from these recorded fields. Nothing here is an
                autonomous decision, and no claim is made that these synthetic assets correspond to
                real-world infrastructure.
              </span>
              <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.12)" }}>
                {entries!.map((x, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "5px 8px", background: "var(--rp-surface-3)" }}>
                    <span style={{ fontFamily: "var(--rp-font-mono)", fontSize: "var(--rp-text-xs)", color: "var(--rp-dim)" }}>{x.field}</span>
                    <span style={{ marginLeft: "auto", fontSize: "var(--rp-text-xs)", color: "var(--rp-mute)" }}>{x.category}</span>
                  </div>
                ))}
                {rawResultFields(result).map((f) => (
                  <div key={f.k} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "5px 8px", background: "var(--rp-surface-3)" }}>
                    <span style={{ fontFamily: "var(--rp-font-mono)", fontSize: "var(--rp-text-xs)", color: "var(--rp-dim)" }}>{f.k}</span>
                    <span style={{ marginLeft: "auto", fontSize: "var(--rp-text-sm)", color: "var(--rp-text)", fontVariantNumeric: "tabular-nums" }}>{f.v}</span>
                  </div>
                ))}
              </div>
            </div>
          </AdvancedDetails>
        </div>
      ) : (
        <div style={{ padding: 13, border: "1px dashed var(--rp-divider-strong)", display: "flex", flexDirection: "column", gap: 9 }}>
          <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: "var(--rp-text-lg)" }}>
            Explanation unavailable
          </span>
          <span style={{ fontSize: "var(--rp-text-sm)", color: "var(--rp-text-dim)", lineHeight: 1.5 }}>
            No explanation could be assembled for this run. The result itself is unaffected — the
            recorded fields are shown below exactly as the engine wrote them.
          </span>
          <ProvenanceTag kind="muted" label="Recorded fields only" />
          <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.12)" }}>
            {rawResultFields(result).map((f) => (
              <div key={f.k} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "5px 8px", background: "var(--rp-surface-3)" }}>
                <span style={{ fontFamily: "var(--rp-font-mono)", fontSize: "var(--rp-text-xs)", color: "var(--rp-dim)" }}>{f.k}</span>
                <span style={{ marginLeft: "auto", fontSize: "var(--rp-text-sm)", color: "var(--rp-text)", fontVariantNumeric: "tabular-nums" }}>{f.v}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}
