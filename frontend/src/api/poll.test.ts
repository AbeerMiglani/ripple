import { afterEach, describe, expect, it, vi } from "vitest";
import type { SimulationResult } from "../types";
import { isSettled, pollSimulationUntilSettled } from "./hooks";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const run = (status: SimulationResult["status"], extra: Partial<SimulationResult> = {}): SimulationResult => ({
  id: "sim-1",
  network_id: "net-1",
  initial_failures: ["a"],
  waves: [],
  total_failed: 0,
  population_affected_estimate: 0,
  global_efficiency_before: 1,
  global_efficiency_after: 1,
  status,
  ...extra,
});

describe("pollSimulationUntilSettled", () => {
  it("returns a run the POST already finished without another request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const done = run("completed");
    await expect(pollSimulationUntilSettled(done)).resolves.toBe(done);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("raises a run the POST already failed", async () => {
    vi.stubGlobal("fetch", vi.fn());
    await expect(pollSimulationUntilSettled(run("failed", { error_message: "boom" }))).rejects.toThrow("boom");
  });

  it("polls a queued run until it settles", async () => {
    vi.useFakeTimers();
    const replies = [run("running"), run("completed", { total_failed: 3 })];
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(replies.shift())));
    vi.stubGlobal("fetch", fetchMock);
    const pending = pollSimulationUntilSettled(run("pending"));
    await vi.advanceTimersByTimeAsync(4_000);
    await expect(pending).resolves.toMatchObject({ status: "completed", total_failed: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("isSettled", () => {
  it("is true only for completed and failed runs", () => {
    expect(isSettled(run("completed"))).toBe(true);
    expect(isSettled(run("failed"))).toBe(true);
    expect(isSettled(run("pending"))).toBe(false);
    expect(isSettled(run("running"))).toBe(false);
    expect(isSettled(undefined)).toBe(false);
  });
});
