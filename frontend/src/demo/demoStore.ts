/**
 * State machine for the one-click demos.
 *
 * The whole run is flattened into an ordered list of *beats* — one per cascade
 * wave, one held peak, then one per restoration stage. Auto-play is nothing
 * more than walking that list on a timer, and the replay rail is the same list
 * rendered as chips, so "play forward" and "jump back to 70% restored" go
 * through one code path instead of two.
 *
 * Deliberately writes no localStorage: `test_localstorage_is_accessed_only_by_
 * the_simulation_store` in `backend/tests/test_frontend_contracts_p2.py` pins
 * `simulationStore.ts` as the single writer, and a demo run is session state
 * that nobody wants restored on reload anyway.
 */

import { create } from "zustand";
import { useSimulationStore } from "../stores/simulationStore";
import type { DemoId } from "./presets";
import type { RecoveryStage } from "./recovery";

export type DemoPhase =
  | "idle"
  | "arming"
  | "collapse"
  | "peak"
  /** Act 1 is done and the board is holding at peak, waiting to be told to recover. */
  | "awaiting-recovery"
  | "recovery"
  | "complete"
  | "error";

export type DemoBeat =
  | { kind: "collapse"; waveIndex: number; label: string; caption: string }
  | { kind: "peak"; label: string; caption: string }
  | {
      kind: "recovery";
      stageIndex: number;
      label: string;
      caption: string;
      percent: number;
      milestone: number | null;
      /** Cumulative restored set for this stage, so applying a beat needs no lookup. */
      restoredNodeIds: string[];
    };

/** How long each beat holds before the runner advances, in ms. */
export const BEAT_DURATION_MS: Record<DemoBeat["kind"], number> = {
  collapse: 950,
  peak: 2400,
  recovery: 1100,
};

interface DemoState {
  activePresetId: DemoId | null;
  phase: DemoPhase;
  beats: DemoBeat[];
  recoveryPlan: RecoveryStage[];
  /** Index into `beats`; -1 while the simulation is still being computed. */
  beatIndex: number;
  error: string | null;
  /** True while auto-play owns the timeline; false after a manual rail jump. */
  isAutoPlaying: boolean;
  /**
   * Bumped on every `arm()`. The runner dispatches once per token, so pressing
   * the same demo button twice replays it instead of being swallowed by an
   * "already dispatched for this preset" guard.
   */
  runToken: number;
  /** Index of the first recovery beat, or -1 when this run has no recovery. */
  recoveryStartIndex: number;
  /**
   * Whether act 2 has been started for this run. Collapse and recovery are two
   * separate stories and auto-play stops between them, so the rail must not let
   * a viewer skip into a restoration that has not been run.
   */
  hasRunRecovery: boolean;

  arm: (presetId: DemoId) => void;
  loadBeats: (beats: DemoBeat[], recoveryPlan: RecoveryStage[]) => void;
  goToBeat: (index: number, options?: { auto?: boolean }) => void;
  /** End act 1: hold at peak instead of running on into the restoration. */
  pauseForRecovery: () => void;
  /** Begin act 2. */
  startRecovery: () => void;
  finish: () => void;
  fail: (message: string) => void;
  stop: () => void;
}

const phaseForBeat = (beat: DemoBeat | undefined): DemoPhase => {
  if (!beat) return "arming";
  return beat.kind;
};

export const useDemoStore = create<DemoState>((set, get) => ({
  activePresetId: null,
  phase: "idle",
  beats: [],
  recoveryPlan: [],
  beatIndex: -1,
  error: null,
  isAutoPlaying: false,
  runToken: 0,
  recoveryStartIndex: -1,
  hasRunRecovery: false,

  arm: (presetId) =>
    set((state) => ({
      activePresetId: presetId,
      phase: "arming",
      beats: [],
      recoveryPlan: [],
      beatIndex: -1,
      error: null,
      isAutoPlaying: true,
      runToken: state.runToken + 1,
      recoveryStartIndex: -1,
      hasRunRecovery: false,
    })),

  loadBeats: (beats, recoveryPlan) =>
    set({
      beats,
      recoveryPlan,
      recoveryStartIndex: beats.findIndex((b) => b.kind === "recovery"),
    }),

  pauseForRecovery: () => set({ phase: "awaiting-recovery", isAutoPlaying: false }),

  startRecovery: () => {
    const { recoveryStartIndex } = get();
    if (recoveryStartIndex < 0) return;
    set({ hasRunRecovery: true });
    get().goToBeat(recoveryStartIndex, { auto: true });
  },

  goToBeat: (index, options) => {
    const { beats } = get();
    if (index < 0 || index >= beats.length) return;
    const beat = beats[index];
    applyBeat(beat);
    set({
      beatIndex: index,
      // A manual jump ends auto-play, and nothing would ever call finish()
      // afterwards, so it lands the run in "complete" directly.
      phase: options?.auto ? phaseForBeat(beat) : "complete",
      isAutoPlaying: options?.auto ?? false,
    });
  },

  finish: () => set({ phase: "complete", isAutoPlaying: false }),

  fail: (message) => set({ phase: "error", error: message, isAutoPlaying: false }),

  stop: () => {
    // A demo stopped mid-flight leaves the simulation store believing a run is
    // still in progress, which pins ImpactSummary and the map overlay on their
    // loading states forever. The recorded result itself is left alone: this
    // stops the narration, it does not clear the board.
    if (useSimulationStore.getState().isRunning) {
      useSimulationStore.getState().setRunning(false);
    }
    set({
      activePresetId: null,
      phase: "idle",
      beats: [],
      recoveryPlan: [],
      beatIndex: -1,
      error: null,
      isAutoPlaying: false,
      recoveryStartIndex: -1,
      hasRunRecovery: false,
    });
  },
}));

/**
 * Puts the board into the state a beat describes.
 *
 * Collapse and peak scrub the existing wave animation; recovery hands the
 * simulation store the cumulative restored set, which it subtracts from
 * `failedNodeIds` so the map and graph show assets coming back with no extra
 * wiring of their own.
 */
export function applyBeat(beat: DemoBeat): void {
  const sim = useSimulationStore.getState();

  if (beat.kind === "recovery") {
    sim.applyRecovery(new Set(beat.restoredNodeIds), beat.stageIndex);
    return;
  }

  // `setWave` clears any recovery state itself, so scrubbing back from the
  // recovery phase into the collapse cannot leave assets showing as restored.
  const lastWave = sim.result ? sim.result.waves.length - 1 : -1;
  const target = beat.kind === "collapse" ? beat.waveIndex : lastWave;
  if (target >= 0) sim.setWave(target);
}
