import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SimulationResult } from "../types";
import { storedIdsForNetwork, useSimulationStore, type StoredScenario, type StoredSim } from "./simulationStore";

const sim = (id: string, network_id: string, extra: Partial<StoredSim> = {}): StoredSim => ({
  id,
  network_id,
  initial_failures: ["a"],
  ...extra,
});
const scenario = (id: string, network_id: string): StoredScenario => ({
  id,
  name: id,
  network_id,
  initial_failures: ["a"],
});

function run(id: string, network_id: string, scenario_id: string | null = null): SimulationResult {
  return {
    id,
    network_id,
    scenario_id,
    initial_failures: ["a"],
    waves: [{ wave: 0, failed_node_ids: ["a"] }],
    total_failed: 1,
    population_affected_estimate: 0,
    global_efficiency_before: 1,
    global_efficiency_after: 0.9,
    status: "completed",
  };
}

/** What the store persisted, read from the stand-in below. */
const persisted = new Map<string, string>();

beforeEach(() => {
  // Tests run in Node, which has no localStorage; the store's writes are what
  // these tests check, so give it a minimal one.
  const data = persisted;
  data.clear();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
  });
  useSimulationStore.setState({
    simulations: [],
    scenarios: [],
    baselineSimulationId: null,
    lastAppliedScenarioId: null,
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("storedIdsForNetwork", () => {
  const state = {
    simulations: [sim("old-base", "net-old"), sim("base", "net-1")],
    scenarios: [scenario("old-scn", "net-old"), scenario("scn", "net-1")],
  };

  it("keeps ids that belong to the network on screen", () => {
    expect(
      storedIdsForNetwork({ ...state, baselineSimulationId: "base", lastAppliedScenarioId: "scn" }, "net-1")
    ).toEqual({ baselineId: "base", scenarioId: "scn" });
  });

  it("drops ids left over from another network, e.g. before a re-seed", () => {
    expect(
      storedIdsForNetwork({ ...state, baselineSimulationId: "old-base", lastAppliedScenarioId: "old-scn" }, "net-1")
    ).toEqual({ baselineId: null, scenarioId: null });
  });

  it("drops ids with no stored entry to check", () => {
    expect(
      storedIdsForNetwork({ ...state, baselineSimulationId: "gone", lastAppliedScenarioId: "gone" }, "net-1")
    ).toEqual({ baselineId: null, scenarioId: null });
  });
});

describe("forgetting stale history", () => {
  it("removes a run, clears it as the baseline and persists the list", () => {
    useSimulationStore.setState({ simulations: [sim("a", "n"), sim("b", "n")], baselineSimulationId: "a" });
    useSimulationStore.getState().forgetSimulation("a");
    const state = useSimulationStore.getState();
    expect(state.simulations.map((s) => s.id)).toEqual(["b"]);
    expect(state.baselineSimulationId).toBeNull();
    expect(JSON.parse(persisted.get("ripple_simulations") ?? "[]").map((s: StoredSim) => s.id)).toEqual(["b"]);
  });

  it("leaves the baseline alone when another run is forgotten", () => {
    useSimulationStore.setState({ simulations: [sim("a", "n"), sim("b", "n")], baselineSimulationId: "a" });
    useSimulationStore.getState().forgetSimulation("b");
    expect(useSimulationStore.getState().baselineSimulationId).toBe("a");
  });

  it("removes a scenario and clears it as the last applied one", () => {
    useSimulationStore.setState({ scenarios: [scenario("s1", "n"), scenario("s2", "n")], lastAppliedScenarioId: "s1" });
    useSimulationStore.getState().forgetScenario("s1");
    const state = useSimulationStore.getState();
    expect(state.scenarios.map((s) => s.id)).toEqual(["s2"]);
    expect(state.lastAppliedScenarioId).toBeNull();
  });
});

describe("choosing the baseline", () => {
  it("makes the first completed run the baseline", () => {
    useSimulationStore.getState().setSimulationResult(run("first", "net-1"), { autoplay: false });
    useSimulationStore.getState().setSimulationResult(run("second", "net-1"), { autoplay: false });
    expect(useSimulationStore.getState().baselineSimulationId).toBe("first");
  });

  it("replaces a baseline left over from another network", () => {
    useSimulationStore.setState({ simulations: [sim("old", "net-old")], baselineSimulationId: "old" });
    useSimulationStore.getState().setSimulationResult(run("new", "net-1"), { autoplay: false });
    expect(useSimulationStore.getState().baselineSimulationId).toBe("new");
  });

  it("never makes a what-if run the baseline", () => {
    useSimulationStore.getState().setSimulationResult(run("whatif", "net-1", "scn"), { autoplay: false });
    expect(useSimulationStore.getState().baselineSimulationId).toBeNull();
  });
});
