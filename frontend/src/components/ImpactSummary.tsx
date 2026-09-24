/**
 * ImpactSummary — the headline answer to "how bad is this, and what now?".
 *
 * This used to render six tiles of equal weight, mixing the two numbers anyone
 * needs first with engineering diagnostics: a raw efficiency figure to three
 * decimals, a stabilization flag that only means something if you know what the
 * wave guardrail is, and a wave count. For a first-time reader that is six
 * things to rank before learning anything.
 *
 * Now three headline numbers carry the summary — assets offline, people
 * affected, and the single recommended next step — and everything diagnostic
 * moves into a collapsed drawer, where it stays available without competing.
 */
import React, { useMemo } from "react";
import { useSimulationStore } from "../stores/simulationStore";
import { useMitigations, useNetworkTopology } from "../api/hooks";
import { useUIStore } from "../stores/uiStore";
import { comparablePopulation } from "../types";
import { criticalServicesOffline, failedNodeIdsForResult } from "../utils/derive";
import StatTile from "./shared/StatTile";
import Section from "./shared/Section";
import AdvancedDetails from "./shared/AdvancedDetails";

const GRID: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(142px, 1fr))",
  gap: 9,
};

const HEADLINE_LABELS = ["Assets offline", "People affected", "Recommended next step"];

export default function ImpactSummary() {
  const result = useSimulationStore((s) => s.result);
  const isRunning = useSimulationStore((s) => s.isRunning);
  // Recovery state, so the headline numbers move while a demo walks the
  // restoration rather than sitting frozen at peak impact.
  const failedNodeIds = useSimulationStore((s) => s.failedNodeIds);
  const restoredNodeIds = useSimulationStore((s) => s.restoredNodeIds);
  const recoveryStageIndex = useSimulationStore((s) => s.recoveryStageIndex);
  const currentWave = useSimulationStore((s) => s.currentWave);
  const networkId = useUIStore((s) => s.networkId);
  const { data: topology } = useNetworkTopology(networkId);

  const completedId = result && result.status === "completed" ? result.id : null;
  const { data: mitigations } = useMitigations(completedId);
  const topMitigation = mitigations?.[0];

  const critical = useMemo(() => {
    if (!result || !topology?.nodes) return null;
    return criticalServicesOffline(topology.nodes, failedNodeIdsForResult(result));
  }, [result, topology]);

  const state = isRunning
    ? "Computing"
    : result
    ? result.status === "completed"
      ? "Simulated"
      : result.status === "failed"
      ? "Stale"
      : "Computing"
    : "—";

  if (isRunning) {
    return (
      <Section title="Impact summary" state={state}>
        <div style={GRID}>
          {HEADLINE_LABELS.map((label) => (
            <StatTile key={label} label={label} value="" sub="Computing" provenance="muted" loading />
          ))}
        </div>
      </Section>
    );
  }

  if (!result || result.status !== "completed") {
    return (
      <Section title="Impact summary" state={state}>
        <div style={GRID}>
          {HEADLINE_LABELS.map((label) => (
            <StatTile key={label} label={label} value="—" sub="Awaiting simulation" provenance="muted" />
          ))}
        </div>
      </Section>
    );
  }

  const uncapped = comparablePopulation(result);
  const deduplicated = result.deduplicated_population_affected;

  const isRecovering = recoveryStageIndex >= 0;
  const restoredPercent = Math.round((restoredNodeIds.size / Math.max(1, result.total_failed)) * 100);

  // Mid-cascade the headline counts what is actually red on the map, not the
  // final total. A summary that reads "11 offline" while wave 0 has taken down
  // two of them is describing a different moment than the one being animated.
  const isMidCascade = !isRecovering && currentWave >= 0 && currentWave < result.waves.length - 1;

  // Say which figure this is. "Capped" and "deduplicated" mean different
  // things and the reader cannot tell them apart from the number alone.
  const popSub = result.is_population_capped
    ? `capped at the study-area total`
    : deduplicated != null
    ? `overlapping service areas counted once`
    : `service areas may overlap`;

  // One next step, in plain language, taken straight from the engine's own
  // domain-aware mitigation rather than restated here.
  const nextStep = topMitigation
    ? topMitigation.action_label || `Harden ${topMitigation.display_name ?? topMitigation.node_name}`
    : result.total_failed > 0
    ? "No single intervention helps"
    : "No action needed";
  const nextStepSub = topMitigation
    ? `prevents ${topMitigation.failures_prevented} further failure${topMitigation.failures_prevented === 1 ? "" : "s"}`
    : result.total_failed > 0
    ? "see the mitigations panel"
    : "nothing went offline";

  return (
    <Section title="Impact summary" state={state}>
      <div style={GRID}>
        <StatTile
          label={isRecovering ? "Still offline" : isMidCascade ? "Offline so far" : "Assets offline"}
          value={String(isRecovering || isMidCascade ? failedNodeIds.size : result.total_failed)}
          sub={
            isRecovering
              ? `down from ${result.total_failed} at peak`
              : isMidCascade
              ? `wave ${currentWave + 1} of ${result.waves.length} — ${result.total_failed} at peak`
              : topology?.nodes
              ? `of ${topology.nodes.length} in the network`
              : undefined
          }
          provenance={isRecovering || isMidCascade ? "derived" : "simulated"}
          color={
            isRecovering
              ? failedNodeIds.size === 0
                ? "var(--rp-ok)"
                : "var(--rp-wave-2)"
              : result.total_failed > 0
              ? "var(--rp-wave-0)"
              : undefined
          }
        />
        <StatTile
          label="People affected"
          value={result.population_affected_estimate.toLocaleString()}
          sub={isRecovering ? `at peak impact — ${popSub}` : popSub}
          provenance="estimated"
        />
        {/* During recovery the "next step" tile gives way to the restoration
            progress: it is what the narration is describing, and the
            recommendation is unchanged by a repair that has not happened yet. */}
        {isRecovering ? (
          <StatTile
            label="Restored"
            value={`${restoredPercent}%`}
            sub={`${restoredNodeIds.size} of ${result.total_failed} assets back online`}
            provenance="derived"
            color="var(--rp-ok)"
          />
        ) : (
          <StatTile
            label="Recommended next step"
            value={nextStep}
            sub={nextStepSub}
            provenance={topMitigation ? "simulated" : "muted"}
            color={topMitigation ? "var(--rp-teal-bright)" : undefined}
          />
        )}
      </div>

      <AdvancedDetails hint={`${result.waves.length} waves · efficiency`}>
        <div style={GRID}>
          <StatTile
            label="Critical services"
            value={critical != null ? String(critical) : "—"}
            sub={critical ? `${critical} hospital${critical === 1 ? "" : "s"} offline` : "all hospitals online"}
            provenance="derived"
            color={critical ? "var(--rp-wave-2)" : "var(--rp-ok)"}
          />
          <StatTile label="Cascade waves" value={String(result.waves.length)} sub="wave 0 → last" provenance="simulated" />
          <StatTile
            label="Cascade stabilized"
            value={result.cascade_stabilized === false ? "No" : result.cascade_stabilized === true ? "Yes" : "—"}
            sub={result.cascade_stabilized === false ? "guardrail reached" : "no further spread"}
            provenance="derived"
            color={result.cascade_stabilized === false ? "var(--rp-wave-2)" : "var(--rp-ok)"}
          />
          <StatTile
            label="Network efficiency"
            value={result.global_efficiency_after != null ? result.global_efficiency_after.toFixed(3) : "—"}
            sub={result.global_efficiency_before != null ? `from ${result.global_efficiency_before.toFixed(3)} baseline` : undefined}
            provenance="derived"
          />
          <StatTile
            label="Population, deduplicated"
            value={deduplicated != null ? deduplicated.toLocaleString() : "—"}
            sub={
              deduplicated != null
                ? `${uncapped.toLocaleString()} before overlap resolution`
                : "no service geometry available"
            }
            provenance="estimated"
          />
          <StatTile
            label="Population, additive"
            value={uncapped.toLocaleString()}
            sub="uncapped sum; may double-count"
            provenance="estimated"
          />
        </div>
      </AdvancedDetails>
    </Section>
  );
}
