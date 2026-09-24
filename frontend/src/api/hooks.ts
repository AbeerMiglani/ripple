import { useQuery, useMutation } from "@tanstack/react-query";
import type { CentralityScore, InfraEdge, InfraNode, MitigationRecommendation, Modification, SimulationResult } from "../types";
import { apiFetch, apiPost } from "./client";

// ---------------------------------------------------------------------------
// Networks
// ---------------------------------------------------------------------------

export function useNetworks() {
  return useQuery({
    queryKey: ["networks"],
    queryFn: () => apiFetch<{ id: string; name: string }[]>("/api/networks", "Failed to fetch networks"),
  });
}

export function useNetworkTopology(networkId: string | null) {
  return useQuery({
    queryKey: ["networks", networkId, "topology"],
    queryFn: async () => {
      const [nodes, edges] = await Promise.all([
        apiFetch<InfraNode[]>(`/api/networks/${networkId}/nodes`, "Failed to fetch network assets"),
        apiFetch<InfraEdge[]>(`/api/networks/${networkId}/edges`, "Failed to fetch network links"),
      ]);
      return { nodes, edges };
    },
    enabled: !!networkId,
  });
}

export function useCentrality(networkId: string | null, metric: "betweenness" | "pagerank" = "betweenness") {
  return useQuery({
    queryKey: ["networks", networkId, "centrality", metric],
    queryFn: () =>
      apiFetch<CentralityScore[]>(`/api/networks/${networkId}/centrality?metric=${metric}`, "Failed to fetch centrality"),
    enabled: !!networkId,
  });
}

// ---------------------------------------------------------------------------
// Simulations
// ---------------------------------------------------------------------------

export function useRunSimulation() {
  return useMutation({
    mutationFn: (params: { network_id: string; initial_failures: string[]; scenario_id?: string }) =>
      apiPost<SimulationResult>("/api/simulations", params, "Failed to start simulation"),
  });
}

export function useSimulationResult(simId: string | null) {
  return useQuery({
    queryKey: ["simulations", simId],
    queryFn: () => apiFetch<SimulationResult>(`/api/simulations/${simId}`, "Failed to fetch simulation result"),
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
    const data = await apiFetch<SimulationResult>(`/api/simulations/${simId}`, "Failed to poll simulation");
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
    mutationFn: (params: { network_id: string; name: string; description?: string; modifications: Modification[]; initial_failures: string[] }) =>
      createScenario(params),
  });
}

export function useCompareScenarios(baselineSimId: string | null, scenarioId: string | null) {
  return useQuery({
    queryKey: ["scenarios", "compare", baselineSimId, scenarioId],
    queryFn: () =>
      apiFetch<{ baseline_result: SimulationResult; scenario_result: SimulationResult }>(
        `/api/scenarios/compare/${baselineSimId}/${scenarioId}`,
        "Failed to fetch comparison"
      ),
    enabled: !!baselineSimId && !!scenarioId,
  });
}

/**
 * How many ranked mitigations the frontend ever shows. Every consumer reads
 * the same query and takes the prefix it needs: the ranking is deterministic,
 * so the top 1 of the top 10 is exactly what a request for 1 would return.
 */
export const MITIGATION_FETCH_LIMIT = 10;

/**
 * Ranked mitigations for a completed simulation.
 *
 * The only recommendations query in the app. Each panel used to fetch its own
 * (limit 5, 1 and 10 under three different cache keys), and every one of those
 * requests reruns the cascade once per candidate server-side. A completed
 * run's recommendations never change, so the result is also never refetched.
 *
 * Deliberately NOT named after the removed hook: `test_frontend_contracts_p2.py`
 * pins that one as gone. This one is typed against what the endpoint actually
 * returns -- a bare list.
 */
export function useMitigations(simulationId: string | null | undefined) {
  return useQuery<MitigationRecommendation[]>({
    queryKey: ["mitigations", simulationId],
    enabled: !!simulationId,
    staleTime: Infinity,
    queryFn: () =>
      apiFetch<MitigationRecommendation[]>(
        `/api/simulations/${simulationId}/recommendations?limit=${MITIGATION_FETCH_LIMIT}`,
        "Failed to fetch mitigations"
      ),
  });
}

/** POST /api/scenarios, shared by the what-if form and the recommendation flow. */
export function createScenario(params: {
  network_id: string;
  name: string;
  description?: string;
  modifications: Modification[];
  initial_failures: string[];
}): Promise<CreatedScenario> {
  return apiPost<CreatedScenario>("/api/scenarios", params, "Failed to create scenario");
}

/** The fields of a ScenarioResponse the frontend reads. */
export interface CreatedScenario {
  id: string;
  name: string;
  network_id: string;
  initial_failures: string[];
  created_at?: string;
}
