/**
 * Derives a restoration sequence from a completed cascade result.
 *
 * The engine models collapse only — there is no recovery, repair or
 * restoration logic anywhere in `backend/app/simulation/`. So this is a
 * *derivation*, not a simulation, and every surface that renders it says so
 * with the same `derived` provenance tag the rest of the app uses for
 * client-side values.
 *
 * The order is not invented. Restoration replays the recorded waves forwards:
 * the assets that failed first are the ones nothing else was waiting on, so
 * they come back first, and an asset returns only after the wave that carried
 * it down has been restored. That is the real repair order — a hospital cannot
 * be brought back before the substation feeding it — which is exactly the
 * dependency structure `waves` already encodes.
 *
 * What is chosen here, and only this, is granularity: a wave that took out half
 * the network is split into smaller steps so the animation reads as a gradual
 * return rather than one jump. Chunking never reorders assets across waves.
 */

import type { InfraNode, SimulationResult } from "../types";
import { marginalFailures } from "../types";
import { failedNodeIdsForResult } from "../utils/derive";

/** Restoration percentages called out as milestones on the replay rail. */
export const RECOVERY_MILESTONES = [20, 70, 100] as const;

/** No single stage restores more than this share of the failed assets. */
const MAX_STAGE_SHARE = 0.25;

export interface RecoveryStage {
  index: number;
  /** Everything back online at this stage, cumulative. */
  restoredNodeIds: string[];
  /** Just this stage's additions. */
  newlyRestoredNodeIds: string[];
  restoredCount: number;
  stillOfflineCount: number;
  totalFailed: number;
  /** 0–100, rounded. */
  percentRestored: number;
  caption: string;
  /** 20, 70 or 100 when this stage is where that milestone is first reached. */
  milestone: number | null;
}

const TYPE_PHRASE: Record<string, string> = {
  power_substation: "Substations re-energised",
  water_station: "Water stations back on supply",
  hospital: "Critical services back online",
  telecom_tower: "Comms restored",
  road_junction: "Routes reopened",
};

/** Names the dominant asset class in a batch, for the caption. */
function phraseFor(ids: string[], nodeById: Map<string, InfraNode>): string {
  const counts = new Map<string, number>();
  for (const id of ids) {
    const type = nodeById.get(id)?.node_type;
    if (!type) continue;
    counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [type, count] of counts) {
    if (count > bestCount) {
      best = type;
      bestCount = count;
    }
  }
  return (best && TYPE_PHRASE[best]) || "Assets back online";
}

/**
 * Splits one wave's assets into stages of at most `limit`, preserving order.
 */
function chunk(ids: string[], limit: number): string[][] {
  if (ids.length === 0) return [];
  const out: string[][] = [];
  for (let i = 0; i < ids.length; i += limit) {
    out.push(ids.slice(i, i + limit));
  }
  return out;
}

export function buildRecoveryPlan(
  result: SimulationResult,
  nodes: InfraNode[] = []
): RecoveryStage[] {
  const failed = failedNodeIdsForResult(result);
  const total = failed.size;
  if (total === 0) return [];

  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // Group by the wave each asset fell in, earliest first. An asset seen in an
  // earlier wave is not re-listed, so each appears exactly once.
  const seen = new Set<string>();
  const groups: string[][] = [];

  for (const id of result.initial_failures) {
    if (failed.has(id) && !seen.has(id)) seen.add(id);
  }
  if (seen.size > 0) groups.push([...seen]);

  for (const wave of result.waves) {
    const batch = marginalFailures(wave).filter((id) => failed.has(id) && !seen.has(id));
    if (batch.length === 0) continue;
    for (const id of batch) seen.add(id);
    groups.push(batch);
  }

  // Anything in the failed set that no wave claimed — defensive, so a stray id
  // cannot leave the recovery stuck below 100%.
  const orphans = [...failed].filter((id) => !seen.has(id));
  if (orphans.length > 0) groups.push(orphans);

  const limit = Math.max(1, Math.ceil(total * MAX_STAGE_SHARE));
  const batches = groups.flatMap((group) => chunk(group, limit));

  const stages: RecoveryStage[] = [];
  const restored: string[] = [];

  batches.forEach((batch, index) => {
    restored.push(...batch);
    const percent = Math.round((restored.length / total) * 100);
    stages.push({
      index,
      restoredNodeIds: [...restored],
      newlyRestoredNodeIds: batch,
      restoredCount: restored.length,
      stillOfflineCount: total - restored.length,
      totalFailed: total,
      percentRestored: percent,
      caption: `${phraseFor(batch, nodeById)} — ${restored.length} of ${total} assets restored`,
      milestone: null,
    });
  });

  // Attach each milestone to the first stage that reaches it. A stage that
  // crosses two at once keeps the higher one, so no label is shown twice.
  //
  // The stage's own percentage is what gets displayed, not the milestone's:
  // with eleven failed assets there is no way to land on exactly 20%, and
  // rounding the count to make the label match would be the one lie in an app
  // that is otherwise careful to say where every number came from. The caption
  // names the milestone instead, so the beat is announced without the figure
  // being massaged.
  for (const target of RECOVERY_MILESTONES) {
    const stage = stages.find((s) => s.percentRestored >= target);
    if (stage) stage.milestone = target;
  }

  for (const stage of stages) {
    if (stage.milestone === null) continue;
    stage.caption =
      stage.milestone >= 100
        ? `Fully restored — all ${total} assets back online.`
        : `${stage.milestone}% milestone passed. ${stage.caption}.`;
  }

  return stages;
}
