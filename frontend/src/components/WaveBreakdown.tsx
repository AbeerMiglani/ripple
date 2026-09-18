/**
 * WaveBreakdown — sidebar list of cascade waves, one row per wave with the
 * assets that failed in it. Clicking a row isolates that wave on the map
 * (reuses the existing CascadeTimeline scrubber state via setWave).
 */
import React from "react";
import { useSimulationStore } from "../stores/simulationStore";
import { useNetworkTopology } from "../api/hooks";
import { useUIStore } from "../stores/uiStore";
import { marginalFailures } from "../types";
import Section from "./shared/Section";
import { useNodeLookup } from "../hooks/useNodeLookup";

const WAVE_COLORS = ["var(--rp-wave-0)", "var(--rp-wave-1)", "var(--rp-wave-2)"];

export default function WaveBreakdown() {
  const result = useSimulationStore((s) => s.result);
  const currentWave = useSimulationStore((s) => s.currentWave);
  const setWave = useSimulationStore((s) => s.setWave);
  const isRunning = useSimulationStore((s) => s.isRunning);
  const networkId = useUIStore((s) => s.networkId);
  const { data: topology } = useNetworkTopology(networkId);
  const nodeLookup = useNodeLookup(topology?.nodes);

  const state = isRunning
    ? "Computing"
    : !result || result.status !== "completed"
    ? "No result"
    : currentWave === -1
    ? "Select to isolate"
    : `Wave ${currentWave + 1} isolated`;

  if (!result || result.status !== "completed" || isRunning) {
    return (
      <Section title="Cascade waves" state={state}>
        <p style={{ margin: 0, fontSize: 11.5, color: "var(--rp-mute)" }}>
          {isRunning ? "Waves will appear as the simulation completes." : "Run a simulation to see the wave-by-wave breakdown."}
        </p>
      </Section>
    );
  }

  return (
    <Section title="Cascade waves" state={state}>
      <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "rgba(148,188,227,.12)" }}>
        {result.waves.map((w, i) => {
          const color = WAVE_COLORS[Math.min(i, 2)];
          const open = currentWave === i;
          // Marginal: what newly failed in THIS wave. Cumulative: the running
          // total. Showing only one of them left the reader unable to tell a
          // cascade that was accelerating from one that was burning out.
          const marginal = marginalFailures(w);
          const cumulative =
            w.cumulative_failed_node_ids?.length ??
            result.waves.slice(0, i + 1).reduce((n, prev) => n + marginalFailures(prev).length, 0);
          const names = marginal.map((id) => nodeLookup.get(id)?.display_name || nodeLookup.get(id)?.name || id.slice(0, 8));
          return (
            <button
              key={i}
              onClick={() => setWave(open ? Math.max(0, i - 1) : i)}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 11,
                padding: "10px 11px",
                background: open ? "#1a2330" : "var(--rp-surface-3)",
                border: "none",
                borderLeft: `3px solid ${color}`,
                cursor: "pointer",
                textAlign: "left",
                width: "100%",
              }}
            >
              <span style={{ fontFamily: "var(--rp-font-heading)", fontWeight: 600, fontSize: 22, lineHeight: 1, color, fontVariantNumeric: "tabular-nums", minWidth: 20 }}>
                W{i}
              </span>
              <span style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1, minWidth: 0 }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--rp-text)", fontVariantNumeric: "tabular-nums" }}>
                    +{marginal.length} newly offline
                  </span>
                  <span style={{ fontSize: 10.5, color: "var(--rp-mute)", fontVariantNumeric: "tabular-nums" }}>
                    {cumulative} total
                  </span>
                </span>
                {open && names.length > 0 && (
                  <span style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 3 }}>
                    {names.map((n, j) => (
                      <span
                        key={j}
                        style={{ fontSize: 10.5, padding: "2px 6px", background: "rgba(148,188,227,.1)", border: "1px solid var(--rp-divider)", color: "var(--rp-accent-soft)" }}
                      >
                        {n}
                      </span>
                    ))}
                  </span>
                )}
              </span>
            </button>
          );
        })}
        {result.cascade_stabilized === false && (
          <div style={{ padding: "7px 10px", background: "var(--rp-surface-3)", fontSize: 10.5, color: "var(--rp-wave-2)" }}>
            Cascade was still spreading when the wave guardrail was reached — collapsed here as truncated.
          </div>
        )}
      </div>
    </Section>
  );
}
