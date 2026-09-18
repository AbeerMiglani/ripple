/**
 * Headless driver for the one-click demos. Renders nothing.
 *
 * Mounted once by App. Watches `demoStore` for an armed preset, runs the
 * simulation through the same API hooks every other panel uses, then walks the
 * resulting beat list on a timer so the judge never has to click again.
 *
 * It keys result adoption off its own mutation id, which is how `ControlPanel`
 * and `RecommendationPanel` already coexist: each poller only ever adopts runs
 * it started itself, so a third one cannot hijack the others' results.
 */

import { useCallback, useEffect, useRef } from "react";
import { useRunSimulation, useSimulationResult, useNetworkTopology } from "../api/hooks";
import { useSimulationStore } from "../stores/simulationStore";
import { useUIStore } from "../stores/uiStore";
import type { SimulationResult } from "../types";
import { BEAT_DURATION_MS, useDemoStore, type DemoBeat } from "./demoStore";
import { buildRecoveryPlan, type RecoveryStage } from "./recovery";
import { presetById, resolveSeedNodeIds, type DemoPreset } from "./presets";

/** Flattens a completed run plus its derived recovery into one ordered timeline. */
function buildBeats(
  preset: DemoPreset,
  result: SimulationResult,
  recoveryPlan: RecoveryStage[]
): DemoBeat[] {
  const beats: DemoBeat[] = result.waves.map((_, i) => ({
    kind: "collapse",
    waveIndex: i,
    label: `W${i}`,
    caption:
      preset.collapseCaptions[i] ??
      `Wave ${i + 1} — the cascade is still spreading through the network.`,
  }));

  beats.push({ kind: "peak", label: "Peak", caption: preset.peakCaption });

  for (const stage of recoveryPlan) {
    beats.push({
      kind: "recovery",
      stageIndex: stage.index,
      label: stage.percentRestored >= 100 ? "Restored" : `${stage.percentRestored}%`,
      caption: stage.caption,
      percent: stage.percentRestored,
      milestone: stage.milestone,
      restoredNodeIds: stage.restoredNodeIds,
    });
  }

  return beats;
}

export default function DemoRunner() {
  const activePresetId = useDemoStore((s) => s.activePresetId);
  const phase = useDemoStore((s) => s.phase);
  const beats = useDemoStore((s) => s.beats);
  const beatIndex = useDemoStore((s) => s.beatIndex);
  const isAutoPlaying = useDemoStore((s) => s.isAutoPlaying);
  const runToken = useDemoStore((s) => s.runToken);
  const recoveryStartIndex = useDemoStore((s) => s.recoveryStartIndex);
  const hasRunRecovery = useDemoStore((s) => s.hasRunRecovery);
  const pauseForRecovery = useDemoStore((s) => s.pauseForRecovery);
  const loadBeats = useDemoStore((s) => s.loadBeats);
  const goToBeat = useDemoStore((s) => s.goToBeat);
  const finish = useDemoStore((s) => s.finish);
  const fail = useDemoStore((s) => s.fail);

  const networkId = useUIStore((s) => s.networkId);
  const { data: topology } = useNetworkTopology(networkId);

  const simMutation = useRunSimulation();
  const { data: polledResult } = useSimulationResult(simMutation.data?.id || null);

  /**
   * The run this runner has already dispatched, so a re-render cannot re-POST.
   * Keyed on the token rather than the preset id: pressing the same demo button
   * twice is a deliberate replay and must dispatch again.
   */
  const dispatchedToken = useRef<number>(-1);
  /**
   * The simulation id this run is waiting on.
   *
   * Without it, pressing a second demo replayed the first one's cascade under
   * the new narration: `arm()` swaps `activePresetId` a tick before the new
   * POST resolves, so for that tick the poller still holds the *previous* run's
   * completed result — and the adopt effect, re-running because the preset
   * changed, took it as this demo's answer. Gating on the id `mutate` actually
   * returned makes a stale result unadoptable rather than merely unlikely.
   */
  const pendingSimId = useRef<string | null>(null);

  const startRun = useCallback(
    (preset: DemoPreset) => {
      if (!networkId || !topology?.nodes) return;

      const seedIds = resolveSeedNodeIds(preset, topology.nodes);
      if (seedIds.length === 0) {
        fail(`Could not find ${preset.title} assets in this network.`);
        return;
      }

      const sim = useSimulationStore.getState();
      sim.reset();
      sim.setRunning(true);

      // Show the trigger points on the map immediately, before the engine has
      // returned anything — the judge sees where the shock lands right away.
      useUIStore.setState({ selectedNodeIds: new Set(seedIds), redundancyNodes: [] });

      simMutation.mutate(
        { network_id: networkId, initial_failures: seedIds },
        { onSuccess: (queued) => { pendingSimId.current = queued.id; } }
      );
    },
    [networkId, topology, simMutation, fail]
  );

  // Arm -> dispatch. Guarded by a ref so only the first render after `arm()`
  // fires the request.
  useEffect(() => {
    if (phase !== "arming" || !activePresetId) return;
    if (dispatchedToken.current === runToken) return;
    const preset = presetById(activePresetId);
    if (!preset) return;
    dispatchedToken.current = runToken;
    pendingSimId.current = null;
    startRun(preset);
  }, [phase, activePresetId, runToken, startRun]);

  // Surface a dispatch failure rather than leaving the bar stuck on "arming".
  useEffect(() => {
    if (!simMutation.isError) return;
    const message =
      simMutation.error instanceof Error ? simMutation.error.message : "Failed to start the demo simulation.";
    useSimulationStore.getState().setRunError(message);
    fail(message);
  }, [simMutation.isError, simMutation.error, fail]);

  // Poll -> adopt -> build the timeline -> start playing.
  useEffect(() => {
    if (!polledResult || !activePresetId) return;
    // Only this run's own result, never whatever the poller last held.
    if (polledResult.id !== pendingSimId.current) return;

    if (polledResult.status === "failed") {
      const message = polledResult.error_message || "The simulation engine reported a failure.";
      useSimulationStore.getState().setRunError(message);
      fail(message);
      return;
    }
    if (polledResult.status !== "completed") return;

    const preset = presetById(activePresetId);
    if (!preset) return;
    // Claimed — a re-render with the same result must not rebuild the beats and
    // restart the narration from wave 0.
    pendingSimId.current = null;

    // `autoplay: false` — this runner paces the cascade beat by beat, so the
    // store's own 800ms wave animation must not start and race it.
    useSimulationStore.getState().setSimulationResult(polledResult, { autoplay: false });

    if (
      polledResult.waves.length !== preset.expected.waves ||
      polledResult.total_failed !== preset.expected.totalFailed
    ) {
      // Not an error: the narration still describes real waves. Worth knowing
      // during development that the seed dataset has moved under the presets.
      console.info(
        `[demo:${preset.id}] engine returned ${polledResult.waves.length} waves / ` +
          `${polledResult.total_failed} failed; preset expected ` +
          `${preset.expected.waves} / ${preset.expected.totalFailed}.`
      );
    }

    const recoveryPlan = buildRecoveryPlan(polledResult, topology?.nodes ?? []);
    loadBeats(buildBeats(preset, polledResult, recoveryPlan), recoveryPlan);
  }, [polledResult, activePresetId, topology, loadBeats, fail]);

  // Beats loaded and nothing playing yet -> open on the first beat.
  useEffect(() => {
    if (phase !== "arming" || beats.length === 0 || beatIndex >= 0) return;
    goToBeat(0, { auto: true });
  }, [phase, beats.length, beatIndex, goToBeat]);

  // Auto-advance, stopping at the act boundary.
  useEffect(() => {
    if (!isAutoPlaying || beatIndex < 0 || beats.length === 0) return;
    if (beatIndex >= beats.length - 1) {
      finish();
      return;
    }

    // Collapse and recovery are two separate stories. Act 1 plays to the held
    // peak and stops there; the restoration only runs when it is asked for.
    const next = beatIndex + 1;
    if (recoveryStartIndex >= 0 && next === recoveryStartIndex && !hasRunRecovery) {
      const hold = setTimeout(() => pauseForRecovery(), BEAT_DURATION_MS[beats[beatIndex].kind]);
      return () => clearTimeout(hold);
    }

    const timer = setTimeout(
      () => goToBeat(next, { auto: true }),
      BEAT_DURATION_MS[beats[beatIndex].kind]
    );
    return () => clearTimeout(timer);
  }, [isAutoPlaying, beatIndex, beats, goToBeat, finish, recoveryStartIndex, hasRunRecovery, pauseForRecovery]);

  return null;
}
