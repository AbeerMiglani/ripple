import React, { useState, useMemo } from "react";
import { useCompareScenarios, useNetworkTopology } from "../api/hooks";
import { useSimulationStore } from "../stores/simulationStore";
import { useUIStore } from "../stores/uiStore";
import type { InfraNode } from "../types";
import { comparablePopulation } from "../types";
import Section from "./shared/Section";
import ProvenanceTag from "./shared/ProvenanceTag";

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 8px",
  background: "var(--rp-surface-2)",
  border: "1px solid var(--rp-divider-strong)",
  color: "var(--rp-text)",
  fontSize: 12,
};

const ScenarioCompare: React.FC = () => {
  const networkId = useUIStore((s) => s.networkId);
  const activeResult = useSimulationStore((s) => s.result);
  const simulations = useSimulationStore((s) => s.simulations);
  const scenarios = useSimulationStore((s) => s.scenarios);
  const baselineSimulationId = useSimulationStore((s) => s.baselineSimulationId);
  const lastAppliedScenarioId = useSimulationStore((s) => s.lastAppliedScenarioId);

  const { data: topology } = useNetworkTopology(networkId);
  const nodeLookup = useMemo(() => {
    const map = new Map<string, InfraNode>();
    if (topology?.nodes) {
      for (const n of topology.nodes) map.set(n.id, n);
    }
    return map;
  }, [topology]);

  const [selectedBaselineId, setSelectedBaselineId] = useState<string>("");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("");
  const [filterText, setFilterText] = useState<string>("");

  const getReadableFailures = (failureIds: string[]) => {
    if (!failureIds || failureIds.length === 0) return "No initial failures";
    return failureIds
      .map((id) => {
        const n = nodeLookup.get(id);
        const name = n?.display_name || n?.name || id.slice(0, 8);
        const source = n?.name_source || n?.data_source;
        return source ? `${name} (${source})` : name;
      })
      .join(", ");
  };

  const baselineOptions = useMemo(() => {
    const list: { id: string; label: string; isBaseline: boolean }[] = [];
    const filteredSims = simulations.filter((s) => !networkId || s.network_id === networkId);

    if (activeResult && activeResult.status === "completed" && (!networkId || activeResult.network_id === networkId)) {
      const isBase = activeResult.id === baselineSimulationId || !baselineSimulationId;
      list.push({
        id: activeResult.id,
        label: `${isBase ? "★ Baseline" : "Run"}: ${getReadableFailures(activeResult.initial_failures)} (${activeResult.id.slice(0, 8)})`,
        isBaseline: isBase,
      });
    }

    for (const sim of filteredSims) {
      if (!list.some((item) => item.id === sim.id)) {
        const isBase = sim.id === baselineSimulationId || sim.is_baseline;
        list.push({
          id: sim.id,
          label: `${isBase ? "★ Baseline" : "Run"}: ${getReadableFailures(sim.initial_failures)} (${sim.id.slice(0, 8)})`,
          isBaseline: !!isBase,
        });
      }
    }
    return list;
  }, [activeResult, simulations, baselineSimulationId, networkId, nodeLookup]);

  const scenarioOptions = useMemo(() => {
    const filtered = scenarios.filter((s) => !networkId || s.network_id === networkId);
    return filtered.filter((scen) => !filterText || scen.name.toLowerCase().includes(filterText.toLowerCase()));
  }, [scenarios, networkId, filterText]);

  const activeBaselineId = selectedBaselineId || baselineSimulationId || baselineOptions.find((o) => o.isBaseline)?.id || baselineOptions[0]?.id || "";
  const activeScenarioId = selectedScenarioId || lastAppliedScenarioId || scenarioOptions[0]?.id || "";

  const { data, isLoading, error } = useCompareScenarios(
    activeBaselineId && activeBaselineId.length > 30 ? activeBaselineId : null,
    activeScenarioId && activeScenarioId.length > 30 ? activeScenarioId : null
  );

  return (
    <Section title="Scenario comparison" defaultOpen={false}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 12 }}>
        <div>
          <label style={{ display: "block", fontSize: 11, color: "var(--rp-mute)", marginBottom: 4, fontWeight: 500 }}>Baseline run</label>
          <select value={activeBaselineId} onChange={(e) => setSelectedBaselineId(e.target.value)} style={selectStyle}>
            <option value="">-- Choose Baseline Simulation --</option>
            {baselineOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label style={{ fontSize: 11, color: "var(--rp-mute)", fontWeight: 500 }}>What-if scenario</label>
            {scenarioOptions.length > 2 && (
              <input
                type="text"
                placeholder="Filter..."
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                style={{ padding: "2px 4px", background: "var(--rp-surface-2)", border: "1px solid var(--rp-divider)", color: "var(--rp-text)", fontSize: 10, width: 70 }}
              />
            )}
          </div>
          <select value={activeScenarioId} onChange={(e) => setSelectedScenarioId(e.target.value)} style={selectStyle}>
            <option value="">-- Choose What-If Scenario --</option>
            {scenarioOptions.map((scen) => {
              const typeIcon = scen.intervention_type === "add_edge" ? "[redundancy]" : "[upgrade]";
              return (
                <option key={scen.id} value={scen.id}>
                  {typeIcon} {scen.name} ({getReadableFailures(scen.initial_failures)}) - {scen.id.slice(0, 8)}
                </option>
              );
            })}
          </select>
        </div>
      </div>

      {baselineOptions.length === 0 && scenarioOptions.length === 0 && (
        <p style={{ fontSize: 12, color: "var(--rp-mute)", fontStyle: "italic", margin: "0 0 12px 0" }}>
          Run a baseline simulation and apply a recommended intervention to view a comparison.
        </p>
      )}

      {isLoading && <p style={{ fontSize: 12, color: "var(--rp-mute)" }}>Loading comparison…</p>}
      {error && <p style={{ color: "var(--rp-wave-0)", fontSize: 12 }}>{(error as Error).message}</p>}

      {data &&
        (() => {
          const basePop = comparablePopulation(data.baseline_result);
          const scenPop = comparablePopulation(data.scenario_result);
          const popDelta = scenPop - basePop;
          const failedDelta = data.scenario_result.total_failed - data.baseline_result.total_failed;
          const eitherCapped = data.baseline_result.is_population_capped || data.scenario_result.is_population_capped;
          return (
            <div style={{ marginBottom: 10, padding: "8px 10px", background: "var(--rp-surface-3)", border: "1px solid var(--rp-divider)", fontSize: 12 }}>
              <div style={{ color: "var(--rp-mute)", fontSize: 11, marginBottom: 4, fontWeight: 600 }}>Verified change (scenario vs. baseline)</div>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
                <span>
                  Failed assets:{" "}
                  <strong style={{ color: failedDelta < 0 ? "var(--rp-teal-bright)" : failedDelta > 0 ? "var(--rp-wave-0)" : "var(--rp-text)" }}>
                    {failedDelta > 0 ? "+" : ""}
                    {failedDelta}
                  </strong>
                </span>
                <span>
                  Population:{" "}
                  <strong style={{ color: popDelta < 0 ? "var(--rp-teal-bright)" : popDelta > 0 ? "var(--rp-wave-0)" : "var(--rp-text)" }}>
                    {popDelta > 0 ? "+" : ""}
                    {popDelta.toLocaleString()}
                  </strong>
                </span>
              </div>
              {eitherCapped && (
                <div style={{ color: "var(--rp-faint)", fontSize: 10, marginTop: 4 }}>
                  Population change measured on the uncapped exposure sum, because the capped headline figure saturates
                  for both runs.
                </div>
              )}
            </div>
          );
        })()}

      {data && (
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1, padding: 10, background: "var(--rp-surface-3)", border: "1px solid var(--rp-divider)" }}>
            <h5 style={{ margin: "0 0 6px 0", color: "var(--rp-mute)", fontSize: 12 }}>Baseline</h5>
            <p style={{ margin: "3px 0", fontSize: 12 }}>
              Failed: <strong style={{ color: "var(--rp-wave-0)" }}>{data.baseline_result.total_failed}</strong>
            </p>
            <div style={{ margin: "3px 0", fontSize: 12, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 4 }}>
              <span>
                Pop affected: <strong>{comparablePopulation(data.baseline_result).toLocaleString()}</strong>
              </span>
              {data.baseline_result.has_unresolved_overlap && <ProvenanceTag kind="estimated" label="⚠ overlap" />}
            </div>
            {data.baseline_result.is_population_capped && (
              <span style={{ color: "var(--rp-mute)", fontSize: 10, display: "block" }}>
                Uncapped sum; headline capped at {(data.baseline_result.study_area_population_cap ?? 65000).toLocaleString()}
              </span>
            )}
            <p style={{ margin: "3px 0", fontSize: 12 }}>Waves: {data.baseline_result.waves.length}</p>
            {data.baseline_result.global_efficiency_after !== null && (
              <p style={{ margin: "3px 0", fontSize: 12 }}>Final eff: {data.baseline_result.global_efficiency_after.toFixed(3)}</p>
            )}
          </div>
          <div style={{ flex: 1, padding: 10, background: "rgba(79,174,131,.08)", border: "1px solid rgba(79,174,131,.35)" }}>
            <h5 style={{ margin: "0 0 6px 0", color: "var(--rp-teal-bright)", fontSize: 12 }}>What-if scenario</h5>
            <p style={{ margin: "3px 0", fontSize: 12 }}>
              Failed: <strong style={{ color: "var(--rp-teal-bright)" }}>{data.scenario_result.total_failed}</strong>
            </p>
            <div style={{ margin: "3px 0", fontSize: 12, display: "flex", alignItems: "center", flexWrap: "wrap", gap: 4 }}>
              <span>
                Pop affected: <strong>{comparablePopulation(data.scenario_result).toLocaleString()}</strong>
              </span>
              {data.scenario_result.has_unresolved_overlap && <ProvenanceTag kind="estimated" label="⚠ overlap" />}
            </div>
            {data.scenario_result.is_population_capped && (
              <span style={{ color: "var(--rp-teal-bright)", fontSize: 10, display: "block" }}>
                Uncapped sum; headline capped at {(data.scenario_result.study_area_population_cap ?? 65000).toLocaleString()}
              </span>
            )}
            <p style={{ margin: "3px 0", fontSize: 12 }}>Waves: {data.scenario_result.waves.length}</p>
            {data.scenario_result.global_efficiency_after !== null && (
              <p style={{ margin: "3px 0", fontSize: 12 }}>Final eff: {data.scenario_result.global_efficiency_after.toFixed(3)}</p>
            )}
          </div>
        </div>
      )}
    </Section>
  );
};

export default ScenarioCompare;
