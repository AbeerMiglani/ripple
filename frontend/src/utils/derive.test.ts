import { describe, expect, it } from "vitest";
import type { CascadeWave, InfraNode, SimulationResult } from "../types";
import { criticalServicesOffline, failedNodeIdsForResult } from "./derive";

function wave(partial: Partial<CascadeWave> & { wave: number }): CascadeWave {
  return { failed_node_ids: [], ...partial };
}

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

function node(partial: Partial<InfraNode> & { id: string; node_type: InfraNode["node_type"] }): InfraNode {
  return {
    name: partial.id,
    lat: 0,
    lng: 0,
    capacity: 100,
    current_load: 0,
    failure_threshold: 1,
    population_served: 0,
    status: "operational",
    ...partial,
  };
}

describe("failedNodeIdsForResult", () => {
  it("reads the cumulative set off the final wave when present", () => {
    const r = result({
      initial_failures: ["a"],
      waves: [
        wave({ wave: 0, failed_node_ids: ["a"], cumulative_failed_node_ids: ["a"] }),
        wave({ wave: 1, failed_node_ids: ["b", "c"], cumulative_failed_node_ids: ["a", "b", "c"] }),
      ],
    });
    expect(failedNodeIdsForResult(r)).toEqual(new Set(["a", "b", "c"]));
  });

  it("falls back to accumulating marginal sets for legacy records with no cumulative field", () => {
    const r = result({
      initial_failures: ["a"],
      waves: [
        wave({ wave: 0, failed_node_ids: ["a"] }),
        wave({ wave: 1, failed_node_ids: ["b"] }),
        wave({ wave: 2, failed_node_ids: ["c"] }),
      ],
    });
    expect(failedNodeIdsForResult(r)).toEqual(new Set(["a", "b", "c"]));
  });

  it("always folds in initial_failures even if a wave record omits them", () => {
    const r = result({
      initial_failures: ["seed"],
      waves: [wave({ wave: 0, failed_node_ids: ["seed"], cumulative_failed_node_ids: ["seed"] })],
    });
    expect(failedNodeIdsForResult(r).has("seed")).toBe(true);
  });

  it("returns an empty set for a run with no waves", () => {
    const r = result({ initial_failures: [], waves: [] });
    expect(failedNodeIdsForResult(r)).toEqual(new Set());
  });
});

describe("criticalServicesOffline", () => {
  const nodes: InfraNode[] = [
    node({ id: "h1", node_type: "hospital" }),
    node({ id: "h2", node_type: "hospital" }),
    node({ id: "p1", node_type: "power_substation" }),
  ];

  it("counts only hospitals present in the failed set", () => {
    expect(criticalServicesOffline(nodes, new Set(["h1", "p1"]))).toBe(1);
  });

  it("counts every failed hospital, not just the first", () => {
    expect(criticalServicesOffline(nodes, new Set(["h1", "h2"]))).toBe(2);
  });

  it("returns 0 when no hospital has failed", () => {
    expect(criticalServicesOffline(nodes, new Set(["p1"]))).toBe(0);
  });

  it("returns 0 for an empty failed set", () => {
    expect(criticalServicesOffline(nodes, new Set())).toBe(0);
  });
});
