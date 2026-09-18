/**
 * Client-side derivations over already-fetched API data.
 *
 * Nothing here calls a model or an external service — every value is
 * computed from fields the backend already returned. `buildExplanation` in
 * particular is a deterministic template, not an LLM call: the same
 * approach the design mockup's own "Explain this result" panel uses
 * ("Assembled from recorded result fields · not an autonomous decision").
 * An LLM-generated version is a possible later upgrade, not implemented
 * here.
 */
import type { InfraNode, SimulationResult } from "../types";
import { comparablePopulation as compPop, marginalFailures } from "../types";

export interface ExplanationEntry {
  category: string;
  color: string;
  field: string;
  text: string;
}

const CRITICAL_NODE_TYPE = "hospital";

/** Count of hospitals among the currently-failed nodes. */
export function criticalServicesOffline(nodes: InfraNode[], failedNodeIds: Set<string>): number {
  let count = 0;
  for (const n of nodes) {
    if (n.node_type === CRITICAL_NODE_TYPE && failedNodeIds.has(n.id)) count++;
  }
  return count;
}

/**
 * Every asset failed by the end of the run.
 *
 * The engine publishes the cumulative set on the final wave, so it is read
 * rather than re-derived. Re-accumulating the per-wave lists here was fine as
 * long as those lists were marginal, but it put the same union logic in three
 * places and invited exactly the marginal/cumulative confusion this fixes.
 * Older records without the field fall back to accumulating marginal sets.
 */
export function failedNodeIdsForResult(result: SimulationResult): Set<string> {
  const last = result.waves[result.waves.length - 1];
  if (last?.cumulative_failed_node_ids) {
    return new Set([...last.cumulative_failed_node_ids, ...result.initial_failures]);
  }
  const ids = new Set<string>();
  for (const w of result.waves) for (const id of marginalFailures(w)) ids.add(id);
  for (const id of result.initial_failures) ids.add(id);
  return ids;
}

/**
 * Builds the "Explain this result" paragraphs from a completed simulation
 * result, optionally enriched with a verified comparison (baseline vs.
 * scenario) once a recommendation has actually been re-simulated.
 */
export function buildExplanation(params: {
  result: SimulationResult;
  nodes: InfraNode[];
  topRecommendation?: { rank: number; failures_prevented: number } | null;
  verifiedComparison?: { baseline: SimulationResult; scenario: SimulationResult } | null;
}): ExplanationEntry[] {
  const { result, nodes, topRecommendation, verifiedComparison } = params;
  const failed = failedNodeIdsForResult(result);
  const critical = criticalServicesOffline(nodes, failed);
  const isVerifiedView = !!verifiedComparison;
  const activeResult = verifiedComparison?.scenario ?? result;

  const entries: ExplanationEntry[] = [];

  entries.push({
    category: "Simulation",
    color: "var(--rp-accent)",
    field: "waves · total_failed",
    text: result.cascade_stabilized === false
      ? `The seeded initial failure triggered a cascade that was still spreading when it reached the ${result.waves.length}-wave guardrail, so the run was bounded and recorded as truncated — ${result.total_failed} assets failed up to that point.`
      : `The seeded initial failure propagated through the network's dependency and road links and settled after ${result.waves.length} wave${result.waves.length === 1 ? "" : "s"}, with ${activeResult.total_failed} asset${activeResult.total_failed === 1 ? "" : "s"} failed${isVerifiedView ? ` (was ${result.total_failed})` : ""}.`,
  });

  entries.push({
    category: "Derived impact",
    color: "var(--rp-wave-2)",
    field: "critical services · efficiency",
    text: critical > 0
      ? `${critical} critical service${critical === 1 ? " is" : "s are"} offline in this result.${
          result.global_efficiency_before != null && result.global_efficiency_after != null
            ? ` Network efficiency moved from ${result.global_efficiency_before.toFixed(3)} to ${result.global_efficiency_after.toFixed(3)}.`
            : ""
        }`
      : `No critical service is offline in this result.${
          result.global_efficiency_before != null && result.global_efficiency_after != null
            ? ` Network efficiency settled at ${result.global_efficiency_after.toFixed(3)} (from ${result.global_efficiency_before.toFixed(3)} pre-failure).`
            : ""
        }`,
  });

  const uncapped = compPop(result);
  entries.push({
    category: "Estimated population",
    color: "#c58fc4",
    field: "raw_population_affected",
    text: `${
      result.deduplicated_population_affected != null
        ? `Overlapping service areas are resolved geometrically, so each resident is counted once: ${result.deduplicated_population_affected.toLocaleString()} people affected, against an additive total of ${uncapped.toLocaleString()} that counts anyone served by two failed assets twice.`
        : `Exposure sums population_served across the failed assets: ${uncapped.toLocaleString()} uncapped. These assets carry no service geometry, so overlapping areas could not be resolved and this total may count some residents more than once.`
    }${
      result.is_population_capped
        ? ` The headline figure is capped at the ${(result.study_area_population_cap ?? 65000).toLocaleString()} study-area total, so it is an estimate rather than a count of people.`
        : ""
    }`,
  });

  if (verifiedComparison) {
    const basePop = compPop(verifiedComparison.baseline);
    const scenPop = compPop(verifiedComparison.scenario);
    entries.push({
      category: "Verified rerun",
      color: "var(--rp-ok)",
      field: "verified",
      text: `The engine replayed the exact baseline initial failure on the modified graph: ${verifiedComparison.scenario.total_failed} failed assets (was ${verifiedComparison.baseline.total_failed}), ${scenPop.toLocaleString()} uncapped exposure (was ${basePop.toLocaleString()}). This column is measured by the deterministic engine — it is not a projection.`,
    });
  } else if (topRecommendation) {
    entries.push({
      category: "Verified rerun",
      color: "var(--rp-mute)",
      field: "verified",
      text: `Not yet run. The rank-${topRecommendation.rank} candidate projects −${topRecommendation.failures_prevented} failures from an in-memory resimulation of the candidate graph; nothing is recorded as verified until the rerun is executed.`,
    });
  }

  return entries;
}

/** Raw-field fallback shown when no explanation can be assembled. */
export function rawResultFields(result: SimulationResult): { k: string; v: string }[] {
  return [
    { k: "total_failed", v: String(result.total_failed) },
    { k: "waves", v: String(result.waves.length) },
    { k: "raw_population_affected", v: (result.raw_population_affected ?? result.population_affected_estimate).toLocaleString() },
    { k: "population_affected_estimate", v: `${result.population_affected_estimate.toLocaleString()}${result.is_population_capped ? " (capped)" : ""}` },
    { k: "cascade_stabilized", v: String(result.cascade_stabilized ?? "unknown") },
    { k: "status", v: result.status },
  ];
}
