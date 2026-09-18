import { useQuery, useMutation } from "@tanstack/react-query";
import type { CentralityScore, InfraEdge, InfraNode, MitigationRecommendation, Modification, SimulationResult } from "../types";

// ---------------------------------------------------------------------------
// Networks
// ---------------------------------------------------------------------------

export function useNetworks() {
  return useQuery({
    queryKey: ["networks"],
    queryFn: async () => {
      const res = await fetch("/api/networks");
      if (!res.ok) throw new Error("Failed to fetch networks");
      return res.json() as Promise<{ id: string; name: string }[]>;
    },
  });
}

export function useNetworkTopology(networkId: string | null) {
  return useQuery({
    queryKey: ["networks", networkId, "topology"],
    queryFn: async () => {
      const [nodesRes, edgesRes] = await Promise.all([
        fetch(`/api/networks/${networkId}/nodes`),
        fetch(`/api/networks/${networkId}/edges`),
      ]);
      
      if (!nodesRes.ok || !edgesRes.ok) {
        throw new Error("Failed to fetch topology");
      }
      
      const nodes = await nodesRes.json() as InfraNode[];
      const edges = await edgesRes.json() as InfraEdge[];
      
      return { nodes, edges };
    },
    enabled: !!networkId,
  });
}

export function useCentrality(networkId: string | null, metric: "betweenness" | "pagerank" = "betweenness") {
  return useQuery({
    queryKey: ["networks", networkId, "centrality", metric],
    queryFn: async () => {
      const res = await fetch(`/api/networks/${networkId}/centrality?metric=${metric}`);
      if (!res.ok) throw new Error("Failed to fetch centrality");
      return res.json() as Promise<CentralityScore[]>;
    },
    enabled: !!networkId,
  });
}

// ---------------------------------------------------------------------------
// Simulations
// ---------------------------------------------------------------------------

export function useRunSimulation() {
  return useMutation({
    mutationFn: async (params: { network_id: string; initial_failures: string[]; scenario_id?: string }) => {
      const res = await fetch("/api/simulations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) throw new Error("Failed to start simulation");
      return res.json() as Promise<SimulationResult>;
    },
  });
}

export function useSimulationResult(simId: string | null) {
  return useQuery({
    queryKey: ["simulations", simId],
    queryFn: async () => {
      const res = await fetch(`/api/simulations/${simId}`);
      if (!res.ok) throw new Error("Failed to fetch simulation result");
      return res.json() as Promise<SimulationResult>;
    },
    enabled: !!simId,
    // Poll every 2 seconds if not completed/failed
    refetchInterval: (query) => {
      const state = query.state.data;
      if (state && (state.status === "completed" || state.status === "failed")) {
        return false;
      }

      return 2000;
    },
  });
}

/**
 * Backend hard cap on one simulation task — celery_app.py's task_time_limit
 * (120s), past which Celery kills the worker outright. Polling has to outlast
 * that: stopping any sooner risks reporting "timed out" on the frontend for a
 * run that is still legitimately executing server-side and about to complete.
 * 30s of margin on top covers queueing and network latency before the task
 * even starts running.
 */
const SIMULATION_POLL_TIMEOUT_MS = 150_000;
const SIMULATION_POLL_INTERVAL_MS = 2000;

/**
 * Polls a simulation until it settles, for callers that need the resolved
 * result inline (e.g. running a recommendation's verified rerun) rather than
 * through `useSimulationResult`'s query-cache subscription.
 */
export async function pollSimulationUntilSettled(simId: string): Promise<SimulationResult> {
  const deadline = Date.now() + SIMULATION_POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, SIMULATION_POLL_INTERVAL_MS));
    const res = await fetch(`/api/simulations/${simId}`);
    if (!res.ok) throw new Error("Failed to poll simulation");
    const data = (await res.json()) as SimulationResult;
    if (data.status === "completed" || data.status === "failed") {
      if (data.status === "failed") throw new Error(data.error_message || "Re-simulation failed.");
      return data;
    }
  }
  throw new Error(`Re-simulation timed out after ${Math.round(SIMULATION_POLL_TIMEOUT_MS / 1000)}s.`);
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

export function useCreateScenario() {
  return useMutation({
    mutationFn: async (params: { network_id: string; name: string; description?: string; modifications: Modification[]; initial_failures: string[] }) => {
      const res = await fetch("/api/scenarios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create scenario");
      }
      return res.json();
    },
  });
}

export function useCompareScenarios(baselineSimId: string | null, scenarioId: string | null) {
  return useQuery({
    queryKey: ["scenarios", "compare", baselineSimId, scenarioId],
    queryFn: async () => {
      const res = await fetch(`/api/scenarios/compare/${baselineSimId}/${scenarioId}`);
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to fetch comparison");
      }
      return res.json() as Promise<{ baseline_result: SimulationResult; scenario_result: SimulationResult }>;
    },
    enabled: !!baselineSimId && !!scenarioId,
  });
}

/**
 * Ranked mitigations for a completed simulation.
 *
 * Deliberately NOT named `useRecommendations`: a hook by that name was removed
 * because it had no call sites and its return type contradicted the endpoint,
 * and `test_frontend_contracts_p2.py` pins it as gone. This one is typed
 * against what the endpoint actually returns — a bare list.
 */
export function useMitigations(simulationId: string | null | undefined, limit = 5) {
  return useQuery<MitigationRecommendation[]>({
    queryKey: ["mitigations", simulationId, limit],
    enabled: !!simulationId,
    queryFn: async () => {
      const res = await fetch(`/api/simulations/${simulationId}/recommendations?limit=${limit}`);
      if (!res.ok) throw new Error("Failed to fetch mitigations");
      return res.json();
    },
  });
}
