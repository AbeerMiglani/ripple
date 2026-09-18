/**
 * Shared TypeScript types for the Ripple frontend.
 */

// ---------------------------------------------------------------------------
// Node types
// ---------------------------------------------------------------------------
export type NodeType =
  | "power_substation"
  | "water_station"
  | "hospital"
  | "road_junction"
  | "telecom_tower";

export type NodeStatus = "operational" | "degraded" | "failed";

export type DataSource = "osm" | "synthetic" | "hybrid" | string;

export interface InfraNode {
  id: string;
  name: string;
  display_name?: string;
  node_type: NodeType;
  lat: number;
  lng: number;
  capacity: number;
  current_load: number;
  failure_threshold: number;
  population_served: number;
  status: NodeStatus;
  is_synthetic?: boolean;
  data_source?: DataSource;
  name_source?: string;
  data_quality?: string;
}

// ---------------------------------------------------------------------------
// Edge types
// ---------------------------------------------------------------------------
export type EdgeType = "power_supply" | "water_supply" | "road_link" | "depends_on";

export interface InfraEdge {
  id: string;
  source_id: string;
  target_id: string;
  edge_type: EdgeType;
  weight: number;
  capacity: number;
  is_bidirectional: boolean;
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------
export interface Network {
  id: string;
  name: string;
  nodes: InfraNode[];
  edges: InfraEdge[];
}

// ---------------------------------------------------------------------------
// Simulation
// ---------------------------------------------------------------------------
export interface CascadeWave {
  wave: number;
  /**
   * The MARGINAL set: assets that newly failed in this wave, not the running
   * total. Always present, including on results recorded before the explicit
   * fields below existed.
   */
  failed_node_ids: string[];
  /** Same as `failed_node_ids`, named so the distinction cannot be misread. */
  marginal_failed_node_ids?: string[];
  /**
   * Everything failed up to AND INCLUDING this wave. Published by the engine so
   * consumers stop re-accumulating it themselves — which is how cumulative
   * totals kept leaking into per-wave figures and inflating failure velocity.
   */
  cumulative_failed_node_ids?: string[];
}

/** The assets that newly failed in one wave, whichever fields the record carries. */
export function marginalFailures(wave: CascadeWave): string[] {
  return wave.marginal_failed_node_ids ?? wave.failed_node_ids;
}

export interface SimulationResult {
  id: string;
  network_id: string;
  initial_failures: string[];
  waves: CascadeWave[];
  total_failed: number;
  /** Population total after the study-area cap is applied. */
  population_affected_estimate: number;
  /**
   * Uncapped population total. Null for simulations recorded before this field
   * existed. Use this for before/after comparisons: when both runs exceed the
   * cap, `population_affected_estimate` is identical for each and a genuine
   * improvement would otherwise be invisible.
   */
  raw_population_affected?: number | null;
  /**
   * Population total with overlapping service areas resolved, so each resident
   * is counted once. Null when the assets carry no service geometry, in which
   * case `raw_population_affected` is an additive sum that may double-count.
   */
  deduplicated_population_affected?: number | null;
  study_area_population_cap?: number;
  is_population_capped?: boolean;
  has_unresolved_overlap?: boolean;
  /** False when the cascade was truncated at the wave guardrail. */
  cascade_stabilized?: boolean;
  global_efficiency_before: number;
  global_efficiency_after: number;
  status: "pending" | "running" | "completed" | "failed";
  /** Set by the backend when status is "failed"; null/absent otherwise. */
  error_message?: string | null;
}

/**
 * Chooses the population figure to compare across runs.
 *
 * The capped estimate is the honest headline number, but it saturates: a
 * baseline and an intervention that both exceed the study-area cap report the
 * same value. Comparisons therefore use the uncapped total when it is
 * available, falling back to the capped estimate for older records.
 */
export function comparablePopulation(result: {
  population_affected_estimate: number;
  raw_population_affected?: number | null;
}): number {
  return result.raw_population_affected ?? result.population_affected_estimate;
}

export type InterventionType = "upgrade_node" | "add_edge" | string;

export interface MitigationRecommendation {
  rank: number;
  node_id: string;
  node_name: string;
  display_name?: string;
  intervention_type: InterventionType;
  proposed_capacity?: number;
  target_node_id?: string;
  target_node_name?: string;
  target_display_name?: string;
  failures_prevented: number;
  raw_population_saved: number;
  efficiency_gain: number;
  protects_critical_services?: boolean;
  verified?: boolean;
  /** Why the target asset failed: initial_shock, overload, or <service>_dependency_severed. */
  root_cause?: string;
  /** Plain-language root cause, ready to render verbatim. */
  root_cause_detail?: string;
  /** Domain-aware mitigation class, e.g. backup_generator, reroute, redundant_feeder. */
  mitigation_kind?: string;
  /** Plain-language recommended action, ready to render verbatim. */
  action_label?: string;
  /** True when the intervention reconnects the target to a surviving source. */
  restores_supply_path?: boolean;
  scenario_payload: {
    network_id: string;
    name: string;
    description: string;
    modifications: Modification[];
    initial_failures: string[];
  };
}

export interface CentralityScore {
  node_id: string;
  name: string;
  display_name?: string;
  node_type: string;
  is_synthetic?: boolean;
  data_source?: DataSource;
  name_source?: string;
  data_quality?: string;
  score: number;
  rank: number;
}

// ---------------------------------------------------------------------------
// Scenario
// ---------------------------------------------------------------------------
export interface AddEdgeModification {
  type: "add_edge";
  source: string;
  target: string;
  edge_type: EdgeType;
  weight?: number;
  capacity?: number;
  is_bidirectional?: boolean;
}

export interface UpgradeNodeModification {
  type: "upgrade_node";
  node_id: string;
  capacity?: number;
  capacity_multiplier?: number;
  capacity_add?: number;
  failure_threshold?: number;
  failure_threshold_add?: number;
}

export type Modification = AddEdgeModification | UpgradeNodeModification;

export interface Scenario {
  id: string;
  network_id: string;
  name: string;
  description: string;
  modifications: Modification[];
  initial_failures: string[];
  cached_result_id: string | null;
}

// ---------------------------------------------------------------------------
// UI color mapping
// ---------------------------------------------------------------------------
export const NODE_COLORS: Record<NodeType, [number, number, number]> = {
  power_substation: [239, 68, 68],   // red
  water_station: [59, 130, 246],     // blue
  hospital: [34, 197, 94],           // green
  road_junction: [148, 163, 184],    // slate gray
  telecom_tower: [234, 179, 8],      // yellow
};

export const NODE_LABELS: Record<NodeType, string> = {
  power_substation: "Power",
  water_station: "Water",
  hospital: "Hospital",
  road_junction: "Road",
  telecom_tower: "Telecom",
};

export const FAILED_COLOR: [number, number, number] = [220, 38, 38]; // bright red
export const SELECTED_COLOR: [number, number, number] = [251, 191, 36]; // amber
