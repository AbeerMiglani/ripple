import React, { useEffect, useRef, useState } from "react";
import { useUIStore } from "../stores/uiStore";
import { useSimulationStore } from "../stores/simulationStore";
import { useRunSimulation, useSimulationResult, useCreateScenario, useNetworkTopology } from "../api/hooks";
import { comparablePopulation } from "../types";
import ProvenanceTag from "./shared/ProvenanceTag";
import { UI_FLAGS } from "../config/uiFlags";
import { useDemoStore } from "../demo/demoStore";
import { useNodeLookup } from "../hooks/useNodeLookup";

const ControlPanel: React.FC = () => {
  const mode = useUIStore((s) => s.mode);
  const setMode = useUIStore((s) => s.setMode);
  const networkId = useUIStore((s) => s.networkId);
  const selectedNodeIds = useUIStore((s) => s.selectedNodeIds);
  const toggleNodeSelection = useUIStore((s) => s.toggleNodeSelection);
  const redundancyNodes = useUIStore((s) => s.redundancyNodes);
  const clearSelection = useUIStore((s) => s.clearSelection);
  const clearRedundancyNodes = useUIStore((s) => s.clearRedundancyNodes);

  const { data: topology } = useNetworkTopology(networkId);
  const nodeLookup = useNodeLookup(topology?.nodes);

  // Per-field selectors, not `useSimulationStore()`. Subscribing to the whole
  // store re-rendered this panel on every animation tick, because `currentWave`
  // and `failedNodeIds` change together every 800ms during cascade playback.
  const result = useSimulationStore((s) => s.result);
  const runError = useSimulationStore((s) => s.runError);
  const reset = useSimulationStore((s) => s.reset);
  const setSimulationResult = useSimulationStore((s) => s.setSimulationResult);
  const addSimulation = useSimulationStore((s) => s.addSimulation);
  const registerScenario = useSimulationStore((s) => s.registerScenario);
  const setRunning = useSimulationStore((s) => s.setRunning);
  const setRunError = useSimulationStore((s) => s.setRunError);
  const stopDemo = useDemoStore((s) => s.stop);
  const [dismissedSimulationIds, setDismissedSimulationIds] = useState<Set<string>>(new Set());

  const simMutation = useRunSimulation();
  const createScenarioMutation = useCreateScenario();

  const { data: polledResult } = useSimulationResult(simMutation.data?.id || null);

  // Ids this panel has already adopted/recorded once they settled. Deciding
  // by "have I handled this polled id before" — rather than by comparing
  // against the shared `result` — matters: `result` can legitimately move on
  // afterward (e.g. RecommendationPanel adopting a verified rerun), and the
  // old comparison (`result.id !== polledResult.id`) treated that as "my
  // baseline hasn't been adopted yet" and silently re-adopted it, reverting
  // the whole app back to the pre-intervention baseline behind the user's
  // back. A ref survives re-renders without re-triggering this effect.
  const handledPolledIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!polledResult || polledResult.status !== "completed") return;
    if (handledPolledIds.current.has(polledResult.id)) return;
    handledPolledIds.current.add(polledResult.id);

    // The store owns persistence of `ripple_simulations`; setSimulationResult
    // records the run as part of adopting it.
    if (!dismissedSimulationIds.has(polledResult.id)) {
      setSimulationResult(polledResult);
      return;
    }

    // Dismissed — setSimulationResult is skipped, so record the run directly
    // instead of losing it from history.
    addSimulation(
      {
        id: polledResult.id,
        network_id: polledResult.network_id,
        initial_failures: polledResult.initial_failures,
        total_failed: polledResult.total_failed,
        is_baseline: false,
        created_at: new Date().toISOString(),
      },
      false
    );
  }, [polledResult, dismissedSimulationIds, setSimulationResult, addSimulation]);

  // Broadcast running/failed status to the shared store so the map overlay
  // and impact summary agree with this panel about what's happening.
  useEffect(() => {
    if (simMutation.isPending) {
      setRunning(true);
      return;
    }
    if (simMutation.isError) {
      setRunError(simMutation.error instanceof Error ? simMutation.error.message : "Failed to start simulation.");
      return;
    }
    if (polledResult) {
      if (polledResult.status === "failed") {
        setRunError(polledResult.error_message || "The simulation engine reported a failure.");
      } else if (polledResult.status === "pending" || polledResult.status === "running") {
        setRunning(true);
      }
    }
  }, [simMutation.isPending, simMutation.isError, simMutation.error, polledResult, setRunning, setRunError]);

  const handleRunBaseline = () => {
    if (!networkId || selectedNodeIds.size === 0) return;
    // A manual run replaces the shared result; a demo still narrating would
    // keep applying its beats to it.
    stopDemo();
    setRunning(true);
    simMutation.mutate({
      network_id: networkId,
      initial_failures: Array.from(selectedNodeIds),
      scenario_id: undefined,
    });
  };

  const handleSaveScenario = async () => {
    if (!networkId || redundancyNodes.length !== 2 || selectedNodeIds.size === 0) return;
    try {
      const scenario = await createScenarioMutation.mutateAsync({
        network_id: networkId,
        name: "Redundancy What-If",
        modifications: [
          {
            type: "add_edge",
            source: redundancyNodes[0],
            target: redundancyNodes[1],
            edge_type: "power_supply",
            is_bidirectional: true,
          },
        ],
        initial_failures: Array.from(selectedNodeIds),
      });

      // registerScenario persists to `ripple_scenarios` and updates the store's
      // `scenarios` / `lastAppliedScenarioId`, so ScenarioCompare picks the new
      // scenario up immediately rather than only after a reload.
      registerScenario({
        id: scenario.id,
        name: scenario.name,
        network_id: scenario.network_id,
        initial_failures: scenario.initial_failures,
        created_at: scenario.created_at ?? new Date().toISOString(),
      });

      stopDemo();
      setRunning(true);
      simMutation.mutate({
        network_id: networkId,
        initial_failures: Array.from(selectedNodeIds),
        scenario_id: scenario.id,
      });

      setMode("default");
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Failed to create scenario.");
    }
  };

  /**
   * Clears the board — every Clear control goes through this.
   *
   * The button beside "Simulate baseline" used to call `clearSelection` alone,
   * a uiStore action that empties `selectedNodeIds` and touches nothing else.
   * The simulation store kept the completed `result`, the `failedNodeIds` the
   * map and graph colour from, the scrubber position and the running animation
   * interval, so the cascade stayed painted on screen and the board visibly did
   * not reset. `reset()` is the action that actually tears all of that down.
   *
   * Dismissing the result id first keeps the run in history and stops the
   * polling effect below re-adopting it the moment it is cleared.
   */
  const handleClearBoard = () => {
    stopDemo();
    if (result?.status === "completed") {
      setDismissedSimulationIds((prev) => {
        const next = new Set(prev);
        next.add(result.id);
        return next;
      });
    }
    clearSelection();
    clearRedundancyNodes();
    reset();
  };

  const isRunning = simMutation.isPending || (polledResult && polledResult.status !== "completed" && polledResult.status !== "failed");

  // There is something to clear whenever the board is showing anything at all,
  // not just while nodes happen to be selected. Gating on the selection alone
  // left Clear disabled with a full cascade still on the map.
  const canClear = (selectedNodeIds.size > 0 || !!result || !!runError) && !isRunning;

  return (
    <div style={{ padding: 16, borderBottom: "1px solid var(--rp-divider)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <h4 style={{ fontSize: 13.5, letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--rp-text)" }}>Simulation controls</h4>
        {UI_FLAGS.whatIfRedundancyMode && (
          <select
            className="rp-btn rp-btn-secondary"
            style={{ fontSize: 11.5, padding: "4px 8px" }}
            value={mode}
            onChange={(e) => setMode(e.target.value as any)}
          >
            <option value="default">Baseline</option>
            <option value="add_redundancy">What-if: add redundancy</option>
          </select>
        )}
      </div>

      {UI_FLAGS.manualFailureSelection && mode === "default" && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontSize: 11.5, color: "var(--rp-mute)", fontWeight: 600 }}>
              Initial failures ({selectedNodeIds.size})
            </span>
            {selectedNodeIds.size > 0 && (
              <button className="rp-btn rp-btn-ghost" style={{ fontSize: 11, padding: 0 }} onClick={handleClearBoard} disabled={!canClear}>
                Clear all
              </button>
            )}
          </div>

          {selectedNodeIds.size === 0 ? (
            <p style={{ fontSize: 12, color: "var(--rp-mute)", margin: "0 0 10px 0", fontStyle: "italic" }}>
              Click assets on the map or the topology graph to select failure trigger points.
            </p>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 110, overflowY: "auto", marginBottom: 10, padding: "2px 0" }}>
              {Array.from(selectedNodeIds).map((id) => {
                const node = nodeLookup.get(id);
                const name = node?.display_name || node?.name || id.slice(0, 8);
                const nameSource = node?.name_source || node?.data_source || "synthetic";
                const dataQuality = node?.data_quality || "estimated";
                return (
                  <span
                    key={id}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      background: "var(--rp-surface-2)",
                      border: "1px solid var(--rp-wave-0)",
                      padding: "2px 6px",
                      fontSize: 11,
                      color: "#fca5a5",
                    }}
                    // Plain-language only. The raw id, source and quality
                    // are in ExplainPanel's asset-details drawer, where they
                    // can be read deliberately rather than hovered into.
                    title={`${name} — ${nameSource} data, ${dataQuality}`}
                  >
                    <span>{name}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleNodeSelection(id);
                      }}
                      disabled={!!isRunning}
                      style={{ background: "transparent", border: "none", color: "var(--rp-mute)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: 0, marginLeft: 2 }}
                      title="Deselect node"
                    >
                      ×
                    </button>
                  </span>
                );
              })}
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button
              className={`rp-btn ${selectedNodeIds.size > 0 && !isRunning ? "rp-btn-danger" : "rp-btn-secondary"}`}
              style={{ flex: 1 }}
              onClick={handleRunBaseline}
              disabled={selectedNodeIds.size === 0 || !!isRunning}
            >
              {isRunning && (
                <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} className="rp-spin">
                  <path d="M12 3a9 9 0 019 9" strokeLinecap="round" />
                </svg>
              )}
              {isRunning ? "Running…" : "Simulate baseline"}
            </button>
            <button
              className="rp-btn rp-btn-secondary"
              onClick={handleClearBoard}
              disabled={!canClear}
              title="Clear the selection, the active result and the cascade on the map"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {UI_FLAGS.whatIfRedundancyMode && mode === "add_redundancy" && (
        <div className="rp-blueprint" style={{ marginBottom: 12, padding: 12, background: "rgba(92,178,166,.06)", borderColor: "rgba(92,178,166,.4)" }}>
          <i className="rp-corner tl" />
          <i className="rp-corner br" />
          <p style={{ fontSize: 12.5, marginBottom: 8, color: "var(--rp-teal-bright)", lineHeight: 1.5 }}>
            1. Select exactly 2 nodes to add a redundant power line between.
            <br />
            2. Also select initial failures in Baseline mode.
          </p>
          <div style={{ fontSize: 12, marginBottom: 8, color: "var(--rp-text-dim)" }}>
            <span>Connect pair ({redundancyNodes.length}/2):</span>
            {redundancyNodes.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                {redundancyNodes.map((id, idx) => (
                  <span key={id} style={{ background: "rgba(92,178,166,.12)", border: "1px solid var(--rp-teal)", padding: "2px 8px", fontSize: 11, color: "var(--rp-teal-bright)" }}>
                    {idx === 0 ? "From: " : "To: "}
                    <strong>{nodeLookup.get(id)?.display_name || nodeLookup.get(id)?.name || id.slice(0, 8)}</strong>
                  </span>
                ))}
              </div>
            ) : (
              <span style={{ color: "var(--rp-teal)", fontStyle: "italic", marginLeft: 4 }}>Select 2 nodes on the map</span>
            )}
          </div>
          <p style={{ fontSize: 12, marginBottom: 10 }}>Initial failures ready: {selectedNodeIds.size}</p>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="rp-btn rp-btn-primary"
              style={{ flex: 1, background: "var(--rp-teal)", borderColor: "var(--rp-teal)" }}
              onClick={handleSaveScenario}
              disabled={redundancyNodes.length !== 2 || selectedNodeIds.size === 0 || !!isRunning}
            >
              {isRunning ? "Running…" : "Save & simulate"}
            </button>
            <button className="rp-btn rp-btn-secondary" onClick={clearRedundancyNodes}>
              Clear
            </button>
          </div>
        </div>
      )}

      {simMutation.isError && (
        <div className="rp-blueprint" style={{ marginBottom: 12, padding: "8px 10px", background: "rgba(240,68,56,.08)", borderColor: "rgba(240,68,56,.4)" }}>
          <span style={{ fontSize: 12, color: "var(--rp-error-soft)" }}>
            {simMutation.error instanceof Error ? simMutation.error.message : "Failed to start simulation."}
          </span>
        </div>
      )}

      {result && result.status === "completed" && (
        <div style={{ padding: 10, background: "var(--rp-surface-3)", display: "flex", flexDirection: "column", gap: 3 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--rp-mute)" }}>Active result</span>
            <button className="rp-btn rp-btn-ghost" style={{ fontSize: 10.5, padding: 0 }} onClick={handleClearBoard}>
              Clear
            </button>
          </div>
          <p style={{ margin: "2px 0", fontSize: 12.5 }}>
            Failed: <strong style={{ color: "var(--rp-wave-0)" }}>{result.total_failed}</strong> · Waves:{" "}
            <strong style={{ color: "var(--rp-accent)" }}>{result.waves.length}</strong>
          </p>
          <p style={{ margin: "2px 0", fontSize: 12.5, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            People affected:{" "}
            <strong style={{ color: "var(--rp-wave-2)" }}>
              {(result.deduplicated_population_affected ?? comparablePopulation(result)).toLocaleString()}
            </strong>
            {result.has_unresolved_overlap && (
              <span title="These assets carry no service geometry, so overlapping service areas could not be resolved and this total may count some residents more than once.">
                <ProvenanceTag kind="estimated" label="⚠ overlap unresolved" />
              </span>
            )}
          </p>
          {result.cascade_stabilized === false && (
            <div style={{ marginTop: 2 }}>
              <ProvenanceTag kind="derived" label="⏱ truncated at wave guardrail" />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ControlPanel;
