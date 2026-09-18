import { describe, expect, it } from "vitest";
import type { CascadeWave, SimulationResult } from "../types";
import { buildRecoveryPlan } from "./recovery";

function result(partial: Partial<SimulationResult> & { waves: CascadeWave[] }): SimulationResult {
  return {
    id: "sim-1",
    network_id: "net-1",
    initial_failures: [],
    total_failed: 0,
    population_affected_estimate: 0,
    global_efficiency_before: 1,
    global_efficiency_after: 1,
    status: "completed",
    ...partial,
  };
}

describe("buildRecoveryPlan", () => {
  it("returns no stages for a run with nothing failed", () => {
    expect(buildRecoveryPlan(result({ initial_failures: [], waves: [] }))).toEqual([]);
  });

  it("replays the recorded waves forward, chunked and milestone-tagged", () => {
    // 4 failed assets total -> MAX_STAGE_SHARE (0.25) caps each stage at
    // ceil(4 * 0.25) = 1, so this produces exactly one stage per asset and
    // lands on clean, individually checkable percentages: 25/50/75/100.
    const r = result({
      initial_failures: ["root"],
      waves: [
        { wave: 0, failed_node_ids: ["root"], cumulative_failed_node_ids: ["root"] },
        { wave: 1, failed_node_ids: ["b", "c", "d"], cumulative_failed_node_ids: ["root", "b", "c", "d"] },
      ],
    });

    const plan = buildRecoveryPlan(r);

    expect(plan).toHaveLength(4);
    expect(plan.map((s) => s.percentRestored)).toEqual([25, 50, 75, 100]);
    expect(plan.map((s) => s.newlyRestoredNodeIds)).toEqual([["root"], ["b"], ["c"], ["d"]]);

    // Cumulative and monotonically growing — the root asset restored first
    // matches the real dependency order (nothing else was waiting on it).
    expect(plan[0].restoredNodeIds).toEqual(["root"]);
    expect(plan[3].restoredNodeIds).toEqual(["root", "b", "c", "d"]);
    expect(plan[3].stillOfflineCount).toBe(0);
    expect(plan[3].totalFailed).toBe(4);

    // Milestones land on the first stage that reaches each threshold, and are
    // never attached twice: 20% is first reached at stage 0 (25%), 70% first
    // at stage 2 (75%) since stage 1 (50%) falls short.
    expect(plan.map((s) => s.milestone)).toEqual([20, null, 70, 100]);
  });

  it("states the final restoration stage as a fact, not a rounded milestone label", () => {
    const r = result({
      initial_failures: ["only"],
      waves: [{ wave: 0, failed_node_ids: ["only"], cumulative_failed_node_ids: ["only"] }],
    });
    const plan = buildRecoveryPlan(r);
    expect(plan).toHaveLength(1);
    expect(plan[0].percentRestored).toBe(100);
    expect(plan[0].caption).toBe("Fully restored — all 1 assets back online.");
  });

  it("never drops an asset that no recorded wave claimed", () => {
    // Defensive path: total_failed / cumulative_failed_node_ids disagree with
    // what the per-wave failed_node_ids actually list.
    const r = result({
      initial_failures: ["root"],
      waves: [{ wave: 0, failed_node_ids: ["root"], cumulative_failed_node_ids: ["root", "orphan"] }],
    });
    const plan = buildRecoveryPlan(r);
    const allRestored = plan[plan.length - 1].restoredNodeIds;
    expect(new Set(allRestored)).toEqual(new Set(["root", "orphan"]));
  });
});
