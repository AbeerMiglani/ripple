/**
 * Zustand store for simulation state.
 *
 * Manages the cascade animation: which nodes are failed, which wave
 * is currently displayed, and the full simulation result.
 * Also tracks the registry of completed simulations and created scenarios
 * for seamless reactive selection in ScenarioCompare.
 */

import { create } from "zustand";
import type { SimulationResult } from "../types";

export interface StoredSim {
  id: string;
  network_id: string;
  initial_failures: string[];
  total_failed?: number;
  is_baseline?: boolean;
  scenario_id?: string;
  created_at?: string;
}

export interface StoredScenario {
  id: string;
  name: string;
  network_id: string;
  initial_failures: string[];
  intervention_type?: string;
  created_at?: string;
}

const getInitialSimulations = (): StoredSim[] => {
  try {
    return JSON.parse(localStorage.getItem("ripple_simulations") || "[]");
  } catch {
    return [];
  }
};

const getInitialScenarios = (): StoredScenario[] => {
  try {
    return JSON.parse(localStorage.getItem("ripple_scenarios") || "[]");
  } catch {
    return [];
  }
};

interface SimulationState {
  /** The full simulation result object from the API */
  result: SimulationResult | null;
  /** Index of the currently-displayed wave (for animation) */
  currentWave: number;
  /** Set of node IDs that are failed up to the current wave */
  failedNodeIds: Set<string>;
  /**
   * Assets shown as restored during a demo's recovery phase. Subtracted from
   * `failedNodeIds`, so the map and graph need no extra wiring to show a
   * cascade coming back — they simply stop seeing those ids as failed.
   *
   * The restoration order is derived client-side from the recorded waves (see
   * `demo/recovery.ts`); the engine models collapse only. Every surface that
   * renders it labels it as derived.
   */
  restoredNodeIds: Set<string>;
  /** Index into the active recovery plan, or -1 when no recovery is applied. */
  recoveryStageIndex: number;
  /**
   * Assets that failed in a baseline but survive under an applied intervention.
   *
   * Distinct from `restoredNodeIds`: restored means "came back during recovery",
   * saved means "never went down this time because of the change made". The map
   * and graph ring these so the answer to "what did applying that actually do"
   * is visible, not only tabular.
   */
  savedNodeIds: Set<string>;
  /** Whether the cascade animation is playing */
  isPlaying: boolean;
  /** Timer ID for the animation interval */
  animationTimer: ReturnType<typeof setTimeout> | null;

  /**
   * Whether a simulation request is currently in flight (queued, running,
   * or being polled) — set by whichever component triggered it
   * (ControlPanel's baseline run, RecommendationPanel's apply/verify).
   * Shared so the map overlay and impact summary can show a consistent
   * loading state without each re-deriving it from their own hooks.
   */
  isRunning: boolean;
  /** Message from the most recent failed run, or null. Cleared on the next run. */
  runError: string | null;

  // History and scenario registry
  simulations: StoredSim[];
  scenarios: StoredScenario[];
  baselineSimulationId: string | null;
  lastAppliedScenarioId: string | null;

  // Actions
  setSimulationResult: (result: SimulationResult, options?: { autoplay?: boolean }) => void;
  addSimulation: (sim: StoredSim, isBaseline?: boolean) => void;
  registerScenario: (scenario: StoredScenario) => void;
  setBaselineSimulationId: (id: string | null) => void;
  setLastAppliedScenarioId: (id: string | null) => void;
  setRunning: (running: boolean) => void;
  setRunError: (message: string | null) => void;
  advanceWave: () => void;
  play: () => void;
  pause: () => void;
  reset: () => void;
  setWave: (index: number) => void;
  rewindToStart: () => void;
  /** Show `restored` as back online; `stageIndex` is its position in the plan. */
  applyRecovery: (restored: Set<string>, stageIndex: number) => void;
  clearRecovery: () => void;
  /** Mark the assets an intervention rescued, for the map/graph highlight. */
  setSavedNodes: (saved: Set<string>) => void;
}

const initialSims = getInitialSimulations();
const initialBaseline = initialSims.find((s) => s.is_baseline)?.id || initialSims[0]?.id || null;

export const useSimulationStore = create<SimulationState>((set, get) => ({
  result: null,
  currentWave: -1,
  failedNodeIds: new Set(),
  restoredNodeIds: new Set(),
  recoveryStageIndex: -1,
  savedNodeIds: new Set(),
  isPlaying: false,
  animationTimer: null,

  isRunning: false,
  runError: null,

  simulations: initialSims,
  scenarios: getInitialScenarios(),
  baselineSimulationId: initialBaseline,
  lastAppliedScenarioId: null,

  setSimulationResult: (result, options) => {
    const autoplay = options?.autoplay ?? true;
    const state = get();
    if (state.animationTimer) clearInterval(state.animationTimer);

    let updatedSims = state.simulations;
    let newBaselineId = state.baselineSimulationId;

    if (result.status === "completed") {
      const isBaseline = !state.baselineSimulationId || updatedSims.length === 0;
      const simEntry: StoredSim = {
        id: result.id,
        network_id: result.network_id,
        initial_failures: result.initial_failures,
        total_failed: result.total_failed,
        is_baseline: isBaseline,
        created_at: new Date().toISOString(),
      };
      if (!updatedSims.some((s) => s.id === result.id)) {
        updatedSims = [simEntry, ...updatedSims].slice(0, 50);
        try {
          localStorage.setItem("ripple_simulations", JSON.stringify(updatedSims));
          window.dispatchEvent(new Event("ripple_simulations_updated"));
        } catch {}
      }
      if (isBaseline && !newBaselineId) {
        newBaselineId = result.id;
      }
    }

    set({
      result,
      currentWave: -1,
      failedNodeIds: new Set(),
      restoredNodeIds: new Set(),
      recoveryStageIndex: -1,
      savedNodeIds: new Set(),
      isPlaying: false,
      animationTimer: null,
      simulations: updatedSims,
      baselineSimulationId: newBaselineId,
      isRunning: false,
      runError: result.status === "failed" ? get().runError : null,
    });

    // A demo run passes `autoplay: false` and paces the cascade itself, beat by
    // beat, so the built-in animation must not also start and race it.
    if (autoplay && result.status === "completed" && result.waves.length > 0) {
      setTimeout(() => get().play(), 300);
    }
  },

  addSimulation: (sim, isBaseline = false) => {
    const current = get().simulations;
    const updated = [sim, ...current.filter((s) => s.id !== sim.id)].slice(0, 50);
    try {
      localStorage.setItem("ripple_simulations", JSON.stringify(updated));
      window.dispatchEvent(new Event("ripple_simulations_updated"));
    } catch {}
    set((state) => ({
      simulations: updated,
      baselineSimulationId: isBaseline
        ? sim.id
        : state.baselineSimulationId ?? (sim.is_baseline ? sim.id : state.baselineSimulationId),
    }));
  },

  registerScenario: (scenario) => {
    const current = get().scenarios;
    const updated = [scenario, ...current.filter((s) => s.id !== scenario.id)].slice(0, 50);
    try {
      localStorage.setItem("ripple_scenarios", JSON.stringify(updated));
      window.dispatchEvent(new Event("ripple_scenarios_updated"));
    } catch {}
    set({
      scenarios: updated,
      lastAppliedScenarioId: scenario.id,
    });
  },

  setBaselineSimulationId: (id) => set({ baselineSimulationId: id }),
  setLastAppliedScenarioId: (id) => set({ lastAppliedScenarioId: id }),
  setRunning: (running) => set({ isRunning: running, runError: running ? null : get().runError }),
  setRunError: (message) => set({ runError: message, isRunning: false }),

  advanceWave: () => {
    const { result, currentWave, failedNodeIds, animationTimer } = get();
    if (!result || result.status !== "completed") return;

    const waves = result.waves;
    const nextWave = currentWave + 1;

    if (nextWave >= waves.length) {
      if (animationTimer) clearInterval(animationTimer);
      set({ isPlaying: false, animationTimer: null });
      return;
    }

    const newFailed = new Set(failedNodeIds);
    for (const id of waves[nextWave].failed_node_ids) {
      newFailed.add(id);
    }

    set({
      currentWave: nextWave,
      failedNodeIds: newFailed,
      restoredNodeIds: new Set(),
      recoveryStageIndex: -1,
    });
  },

  play: () => {
    const { animationTimer, result, currentWave } = get();
    if (!result || result.status !== "completed") return;

    if (animationTimer) clearInterval(animationTimer);
    if (currentWave >= result.waves.length - 1) return;

    get().advanceWave();
    const timer = setInterval(() => {
      get().advanceWave();
    }, 800);

    set({ isPlaying: true, animationTimer: timer });
  },

  pause: () => {
    const { animationTimer } = get();
    if (animationTimer) clearInterval(animationTimer);
    set({ isPlaying: false, animationTimer: null });
  },

  reset: () => {
    const { animationTimer } = get();
    if (animationTimer) clearInterval(animationTimer);
    set({
      result: null,
      currentWave: -1,
      failedNodeIds: new Set(),
      restoredNodeIds: new Set(),
      recoveryStageIndex: -1,
      savedNodeIds: new Set(),
      isPlaying: false,
      animationTimer: null,
      isRunning: false,
      runError: null,
    });
  },

  setWave: (index) => {
    const { result } = get();
    if (!result || result.status !== "completed") return;

    const waves = result.waves;
    if (index < 0 || index >= waves.length) return;

    const newFailed = new Set<string>();
    for (let i = 0; i <= index; i++) {
      for (const id of waves[i].failed_node_ids) {
        newFailed.add(id);
      }
    }

    // Scrubbing back into the collapse always drops recovery state, so a
    // rail jump from "70% restored" to "wave 2" cannot leave ghost-restored
    // assets painted on the map.
    set({
      currentWave: index,
      failedNodeIds: newFailed,
      restoredNodeIds: new Set(),
      recoveryStageIndex: -1,
    });
  },

  rewindToStart: () => {
    const { animationTimer } = get();
    if (animationTimer) clearInterval(animationTimer);
    set({
      currentWave: -1,
      failedNodeIds: new Set(),
      restoredNodeIds: new Set(),
      recoveryStageIndex: -1,
      isPlaying: false,
      animationTimer: null,
    });
  },

  applyRecovery: (restored, stageIndex) => {
    const { result, animationTimer } = get();
    if (!result || result.status !== "completed") return;
    if (animationTimer) clearInterval(animationTimer);

    // The cascade's full extent, then minus what has come back. Reusing the
    // engine's own cumulative set rather than re-accumulating it here keeps
    // this consistent with `failedNodeIdsForResult` in utils/derive.
    const stillFailed = new Set<string>();
    for (const wave of result.waves) {
      for (const id of wave.failed_node_ids) stillFailed.add(id);
    }
    for (const id of result.initial_failures) stillFailed.add(id);
    for (const id of restored) stillFailed.delete(id);

    set({
      currentWave: result.waves.length - 1,
      failedNodeIds: stillFailed,
      restoredNodeIds: new Set(restored),
      recoveryStageIndex: stageIndex,
      isPlaying: false,
      animationTimer: null,
    });
  },

  clearRecovery: () => set({ restoredNodeIds: new Set(), recoveryStageIndex: -1 }),

  setSavedNodes: (saved) => set({ savedNodeIds: new Set(saved) }),
}));
