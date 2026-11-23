import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SimulationResult } from "../types";
import { useSimulationStore } from "./simulationStore";

function completedRun(): SimulationResult {
  return {
    id: "sim-1",
    network_id: "net-1",
    initial_failures: ["a"],
    waves: [
      { wave: 0, failed_node_ids: ["a"] },
      { wave: 1, failed_node_ids: ["b", "c"] },
      { wave: 2, failed_node_ids: ["d"] },
    ],
    total_failed: 4,
    population_affected_estimate: 0,
    global_efficiency_before: 1,
    global_efficiency_after: 0.5,
    status: "completed",
  };
}

describe("cascade playback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSimulationStore.getState().reset();
    useSimulationStore.getState().setSimulationResult(completedRun(), { autoplay: false });
  });

  afterEach(() => {
    useSimulationStore.getState().pause();
    vi.useRealTimers();
  });

  it("plays through every wave and stops at the last one", () => {
    useSimulationStore.getState().play();
    vi.advanceTimersByTime(5_000);
    const state = useSimulationStore.getState();
    expect(state.currentWave).toBe(2);
    expect(state.isPlaying).toBe(false);
    expect([...state.failedNodeIds].sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("replays from the first wave when Play is pressed at the end", () => {
    useSimulationStore.getState().setWave(2);
    useSimulationStore.getState().play();

    const state = useSimulationStore.getState();
    expect(state.isPlaying).toBe(true);
    // play() advances one wave immediately: the replay starts at wave 0.
    expect(state.currentWave).toBe(0);
    expect([...state.failedNodeIds]).toEqual(["a"]);
  });
});
