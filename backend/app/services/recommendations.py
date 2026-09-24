"""
Canonical recommendation engine for the Ripple infrastructure simulator.
Generates deterministic candidate node upgrades ('upgrade_node') and redundancy
connections ('add_edge'), evaluates candidates via in-memory simulation reruns,
detects critical service (hospital) preservation, and produces ranked recommendations
with pre-built scenario payloads.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Literal

import networkx as nx
from pydantic import UUID4, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.models.network import Edge, Node, SimulationResult
from app.services.graph_build import build_graph
from app.simulation.cascade import (
    _build_supplier_index,
    calculate_global_efficiency,
    run_cascade,
)
from app.simulation.population import calculate_population_impact

logger = logging.getLogger(__name__)


#: Domain-aware mitigations, keyed by (asset class, severed service).
#:
#: A generic "upgrade this node's capacity" is not an action anyone can take in
#: the field. What a hospital that lost its feeders needs is a generator; what a
#: blocked junction needs is a diversion. The engine already knows both the
#: asset class and which service was severed, so it can say which.
MITIGATION_PLAYBOOK: dict[tuple[str, str], tuple[str, str]] = {
    ("hospital", "power"): ("backup_generator", "Deploy backup generator at {name}"),
    ("hospital", "water"): ("water_tanker", "Dispatch water tankers to {name}"),
    ("hospital", "transit"): ("reroute", "Reroute emergency access to {name}"),
    ("water_station", "power"): ("backup_generator", "Deploy backup generator at {name}"),
    ("telecom_tower", "power"): ("backup_power", "Install backup battery bank at {name}"),
    ("power_substation", "power"): ("redundant_feeder", "Add a redundant feeder into {name}"),
    ("road_junction", "transit"): ("reroute", "Reroute logistics traffic around {name}"),
}

#: Fallback when the asset class and severed service are not in the playbook.
DEFAULT_MITIGATIONS: dict[str, tuple[str, str]] = {
    "upgrade_node": ("capacity_upgrade", "Increase capacity headroom at {name}"),
    "add_edge": ("redundant_link", "Add a redundant link into {name}"),
}

_SERVICE_LABELS = {
    "power": "Power",
    "water": "Water",
    "transit": "Transit",
}


def surviving_source_reach(G: nx.DiGraph, failed: set[str]) -> set[str]:
    """Nodes still connected to a working source over surviving assets only.

    A "source" is a surviving strongly-connected component that nothing
    surviving feeds from outside -- generation at the head of the network, or a
    self-sustaining island. Everything reachable downstream of one still has a
    continuous supply path; everything else is stranded, however healthy it
    looks in isolation.

    This is what the redundancy search was missing. It scored a candidate source
    by whether it happened to survive (``is_survivor``) and then used that only
    as a *sort preference*, never a filter -- so it would cheerfully propose
    feeding a hospital from a substation that had itself lost every upstream
    feed, and credit the intervention with a recovery that could not happen.

    One reverse-free BFS over the condensation, O(V+E) per call, rather than a
    path query per candidate pair.
    """
    alive = {str(n) for n in G.nodes if str(n) not in failed}
    if not alive:
        return set()
    H = G.subgraph(alive)

    roots: set[str] = set()
    for component in nx.strongly_connected_components(H):
        # Fed from outside the component by a survivor => not a source itself.
        if any(p not in component for n in component for p in H.predecessors(n)):
            continue
        # Nor is an asset that *lost* its supply a source. Without this an
        # island stranded by the very failure under study -- a substation whose
        # only feeder just died -- would be read as generation, and the search
        # would happily propose it as the donor for a redundancy link it has no
        # power to carry.
        if any(
            str(p) in failed
            for n in component
            for p in G.predecessors(n)
        ):
            continue
        roots |= {str(n) for n in component}

    if not roots:
        # Every surviving asset is fed by another surviving asset, so nothing
        # distinguishes a source. Refusing every candidate here would be worse
        # than not filtering: fall back to "no information" rather than "no".
        return alive

    seen = set(roots)
    frontier = sorted(roots)
    while frontier:
        u = frontier.pop()
        for v in H.successors(u):
            v = str(v)
            if v not in seen:
                seen.add(v)
                frontier.append(v)
    return seen


def diagnose_failure(
    G: nx.DiGraph,
    node_id: str,
    failed: set[str],
    initial_failures: set[str],
    supplier_index: dict[str, dict[str, set[str]]] | None = None,
) -> tuple[str, str]:
    """Why this asset went down: (root_cause_code, plain-language explanation)."""
    if node_id in initial_failures:
        return ("initial_shock", "Directly hit by the initiating event")

    index = supplier_index if supplier_index is not None else _build_supplier_index(G)
    severed = sorted(
        service
        for service, suppliers in index.get(node_id, {}).items()
        if suppliers and suppliers.issubset(failed)
    )
    if severed:
        service = severed[0]
        label = _SERVICE_LABELS.get(service, service.title())
        return (
            f"{service}_dependency_severed",
            f"{label} edge severed — every upstream {service} supplier is offline",
        )
    return ("overload", "Overloaded by load shed from upstream failures")


def _mitigation_for(
    node_data: dict[str, Any],
    root_cause: str,
    intervention_type: str,
    display_name: str,
) -> tuple[str, str]:
    """Pick the contextual mitigation for this asset and failure mode."""
    node_type = str(node_data.get("node_type", ""))
    service = root_cause.removesuffix("_dependency_severed") if "_dependency_severed" in root_cause else ""
    entry = MITIGATION_PLAYBOOK.get((node_type, service))
    if entry is None:
        entry = DEFAULT_MITIGATIONS.get(intervention_type, DEFAULT_MITIGATIONS["upgrade_node"])
    kind, template = entry
    return kind, template.format(name=display_name)


def _to_deterministic_uuid4(val: Any) -> uuid.UUID:
    """Converts a value to a valid UUID4, generating a deterministic UUID4 if invalid."""
    if isinstance(val, uuid.UUID) and val.version == 4:
        return val
    val_str = str(val)
    try:
        u = uuid.UUID(val_str)
        if u.version == 4:
            return u
    except (ValueError, AttributeError):
        pass
    # Generate an RFC 4122 compliant deterministic UUID4 from a secure digest.
    h = bytearray(hashlib.blake2b(val_str.encode("utf-8"), digest_size=16).digest())
    h[6] = (h[6] & 0x0F) | 0x40  # Version 4
    h[8] = (h[8] & 0x3F) | 0x80  # Variant RFC 4122
    return uuid.UUID(bytes=bytes(h))


class MitigationRecommendation(BaseModel):
    """
    Deterministic mitigation recommendation generated by in-memory resimulation.
    Directly provides a ready-to-post ScenarioCreate scenario_payload for the frontend.
    Supports both node capacity upgrades ('upgrade_node') and structural redundancy ('add_edge').
    """
    rank: int = Field(description="1-indexed deterministic rank (1 is highest impact)")
    node_id: UUID4 = Field(description="UUID of the primary target node to harden or source of redundancy")
    node_name: str = Field(description="Name of the target node")
    display_name: str | None = Field(default=None, description="Display name of the target node")
    intervention_type: Literal["upgrade_node", "add_edge"] = Field(
        default="upgrade_node", description="Scenario modification type"
    )
    proposed_capacity: float | None = Field(
        default=None, description="Proposed hardened capacity for the target node (for upgrade_node)"
    )
    target_node_id: UUID4 | None = Field(
        default=None, description="UUID of target destination node (for add_edge)"
    )
    target_node_name: str | None = Field(
        default=None, description="Name of destination node (for add_edge)"
    )
    target_display_name: str | None = Field(
        default=None, description="Display name of destination node (for add_edge)"
    )
    failures_prevented: int = Field(description="Total cascade failures prevented (baseline - candidate)")
    raw_population_saved: int = Field(description="Raw population protected from blackout/outage")
    efficiency_gain: float = Field(description="Delta in post-cascade global network efficiency")
    protects_critical_services: bool = Field(
        default=False, description="True if at least one critical service (hospital) that failed in baseline survives"
    )
    verified: bool = Field(
        default=True, description="True if candidate was verified via in-memory simulation rerun"
    )
    root_cause: str = Field(
        default="overload",
        description="Why the target asset failed: initial_shock, overload, or <service>_dependency_severed",
    )
    root_cause_detail: str = Field(
        default="", description="Plain-language explanation of the root cause, for direct display"
    )
    mitigation_kind: str = Field(
        default="capacity_upgrade",
        description="Domain-aware mitigation class, e.g. backup_generator, reroute, redundant_feeder",
    )
    action_label: str = Field(
        default="", description="Plain-language recommended action, ready to render verbatim"
    )
    restores_supply_path: bool = Field(
        default=False,
        description="True if the intervention reconnects the target to a surviving source over a continuous path",
    )
    scenario_payload: dict[str, Any] = Field(
        description="Ready-to-post ScenarioCreate dictionary compatible with POST /api/scenarios"
    )

    model_config = ConfigDict(from_attributes=True)


def build_network_graph(network_id: str | uuid.UUID, db: Session) -> nx.DiGraph:
    """Builds an in-memory NetworkX DiGraph from the database network topology.

    Delegates to the shared builder so this graph and the Celery runner's are
    the same graph. They used to be built separately and carried different node
    attributes, which meant a candidate was scored against a topology that did
    not quite match the one the baseline run had used.
    """
    nodes = db.query(Node).filter(Node.network_id == network_id).all()
    edges = db.query(Edge).filter(Edge.network_id == network_id).all()
    return build_graph(nodes, edges)


def identify_candidate_nodes(
    G: nx.DiGraph,
    waves: list[dict[str, Any]],
    initial_failures: list[str],
    bc_scores: dict[str, float] | None = None,
) -> list[str]:
    """
    Deterministically identifies candidate intervention nodes from:
    1. Wave 1 casualties (direct successors absorbing initial shock)
    2. Top betweenness centrality secondary casualties (structural transit bottlenecks)
    Strictly excludes initial shock failures.
    """
    initial_set = {str(uid) for uid in initial_failures}

    # 1. Wave 1 casualties
    wave1_nodes: list[str] = []
    if len(waves) > 1:
        wave1_nodes = sorted({str(nid) for nid in waves[1].get("failed_node_ids", [])} - initial_set)

    # 2. All secondary casualties
    all_failed: set[str] = set()
    for w in waves:
        all_failed.update(str(nid) for nid in w.get("failed_node_ids", []))
    secondary_failed = sorted(all_failed - initial_set)

    # 3. Betweenness centrality calculation if not provided
    if bc_scores is None:
        try:
            bc_scores = nx.betweenness_centrality(G, weight=None, normalized=True)
        except Exception:
            logger.exception("Failed to compute betweenness centrality for candidates")
            bc_scores = {}

    top_bc_secondary = sorted(
        secondary_failed,
        key=lambda nid: (-bc_scores.get(nid, 0.0), str(nid)),
    )[:5]

    # Deterministic union of candidate nodes sorted alphabetically
    candidate_node_ids = sorted(set(wave1_nodes) | set(top_bc_secondary), key=lambda x: str(x))
    return candidate_node_ids


def resimulate_candidate(
    G_baseline: nx.DiGraph,
    initial_failures: list[str],
    node_id: str,
    proposed_capacity: float,
    baseline_failed_count: int,
    baseline_raw_pop: int,
    baseline_efficiency: float,
    baseline_failed_hospitals: set[str] | None = None,
) -> dict[str, Any]:
    """
    Clones the graph, applies candidate capacity upgrade, runs Motter-Lai cascade,
    and computes prevented failures, saved population, efficiency delta, and critical hospital survival.
    """
    G_cand = G_baseline.copy()
    if node_id in G_cand.nodes:
        G_cand.nodes[node_id]["capacity"] = float(proposed_capacity)

    # The same cascade model the runner used for the baseline this is
    # differenced against (`runner.py:146` passes the same setting). Letting it
    # default to False made `failures_prevented` subtract a load-overload-only
    # rerun from a dependency-aware baseline, so a capacity upgrade appeared to
    # rescue assets that had in fact lost every feeder -- capacity cannot help
    # a severed dependency, but with the semantics off the engine could not see
    # that and scored the candidate as a save.
    waves_c, _, eff_after_c, _raw_pop_c, _ = run_cascade(
        G_cand,
        initial_failures,
        enforce_edge_semantics=settings.enforce_edge_semantics,
    )
    cand_failed_count = sum(len(w.get("failed_node_ids", [])) for w in waves_c)

    all_failed_cand: set[str] = set()
    for w in waves_c:
        all_failed_cand.update(str(nid) for nid in w.get("failed_node_ids", []))

    # Both sides of this subtraction must come from the same computation.
    # The candidate figure used to be run_cascade's naive double-counting sum
    # while the baseline came from calculate_population_impact, so
    # "population saved" was a difference between two different quantities.
    failures_prevented = baseline_failed_count - cand_failed_count
    cand_pop = calculate_population_impact(all_failed_cand, G_baseline)["raw_population_affected"]
    raw_population_saved = baseline_raw_pop - cand_pop
    efficiency_gain = round(eff_after_c - baseline_efficiency, 5)

    protects_critical = False
    if baseline_failed_hospitals:
        survived = baseline_failed_hospitals - all_failed_cand
        protects_critical = len(survived) > 0

    return {
        "intervention_type": "upgrade_node",
        "node_id": node_id,
        "target_node_id": None,
        "proposed_capacity": proposed_capacity,
        "failures_prevented": failures_prevented,
        "raw_population_saved": raw_population_saved,
        "efficiency_gain": efficiency_gain,
        "protects_critical_services": protects_critical,
        "candidate_failed_count": cand_failed_count,
        "candidate_efficiency": eff_after_c,
    }


def _is_hospital(node_data: dict[str, Any]) -> bool:
    """Checks if a node is a hospital or critical medical facility."""
    node_type = str(node_data.get("node_type", "")).lower()
    name = str(node_data.get("name", "")).lower()
    display_name = str(node_data.get("display_name", "")).lower()
    return node_type == "hospital" or "hospital" in name or "hospital" in display_name


def get_recommendations(
    simulation: SimulationResult | Any,
    db: Session | None = None,
    G_baseline: nx.DiGraph | None = None,
    limit: int = 10,
) -> list[MitigationRecommendation]:
    """
    Main entry point to generate deterministic mitigation recommendations.
    
    Guarantees:
    1. Returns empty list if simulation has zero secondary cascade casualties.
    2. Generates both upgrade_node and add_edge redundancy candidates.
    3. Evaluates hospital protection flag (protects_critical_services) for every candidate.
    4. Sorts strictly by (-failures_prevented, -(1 if protects_critical_services else 0),
       -raw_population_saved, -efficiency_gain, str(node_id), str(target_node_id or '')).
    5. Sets verified: True on all candidates.
    6. Synthesizes ready-to-post ScenarioCreate payloads with zero missing keys.
    """
    waves = getattr(simulation, "waves", None) or []
    raw_initial = getattr(simulation, "initial_failures", None) or []
    initial_failures = [str(uid) for uid in raw_initial]
    initial_set = set(initial_failures)
    total_failed = getattr(simulation, "total_failed", None)
    if total_failed is None:
        total_failed = sum(len(w.get("failed_node_ids", [])) for w in waves)

    # Edge case: No secondary cascade occurred
    if len(waves) <= 1 or total_failed <= len(initial_failures):
        return []

    # Obtain baseline NetworkX DiGraph
    if G_baseline is None:
        if db is None:
            raise ValueError("Either db or G_baseline must be provided.")
        G_baseline = build_network_graph(simulation.network_id, db)

    # Compute baseline metrics
    baseline_failed_count = total_failed
    baseline_efficiency = getattr(simulation, "global_efficiency_after", None)
    if baseline_efficiency is None:
        baseline_efficiency = calculate_global_efficiency(G_baseline)

    all_failed_ids: set[str] = set()
    for w in waves:
        all_failed_ids.update(str(nid) for nid in w.get("failed_node_ids", []))
    pop_impact = calculate_population_impact(all_failed_ids, G_baseline)
    baseline_raw_pop = pop_impact["raw_population_affected"]

    # Detect baseline failed hospitals
    baseline_failed_hospitals: set[str] = {
        nid for nid in all_failed_ids
        if nid in G_baseline.nodes and _is_hospital(G_baseline.nodes[nid])
    }

    # Which services each asset cannot operate without, and which assets still
    # have a continuous supply path to a working source. Both are computed once
    # against the baseline outcome and reused for every candidate.
    supplier_index = _build_supplier_index(G_baseline)
    baseline_reach = surviving_source_reach(G_baseline, all_failed_ids)

    scored_candidates: list[dict[str, Any]] = []

    # --- 1. Candidate Generation: Node Upgrades (upgrade_node) ---
    candidate_node_ids = identify_candidate_nodes(G_baseline, waves, initial_failures)
    for nid in candidate_node_ids:
        if nid not in G_baseline.nodes:
            continue
        orig_cap = float(G_baseline.nodes[nid].get("capacity", 100.0))
        proposed_cap = round(orig_cap * 2.0, 2)

        scored = resimulate_candidate(
            G_baseline=G_baseline,
            initial_failures=initial_failures,
            node_id=nid,
            proposed_capacity=proposed_cap,
            baseline_failed_count=baseline_failed_count,
            baseline_raw_pop=baseline_raw_pop,
            baseline_efficiency=baseline_efficiency,
            baseline_failed_hospitals=baseline_failed_hospitals,
        )

        if scored["failures_prevented"] > 0 or scored["raw_population_saved"] > 0:
            scored_candidates.append(scored)

    # --- 2. Candidate Generation: Redundancy Connections (add_edge) ---
    wave1_failed_nodes: list[str] = []
    if len(waves) > 1:
        wave1_failed_nodes = sorted(
            {str(nid) for nid in waves[1].get("failed_node_ids", [])} - initial_set
        )

    tested_edges: set[tuple[str, str]] = set()

    for w1 in wave1_failed_nodes:
        if w1 not in G_baseline.nodes:
            continue
        # Downstream successors that lost connectivity
        successors = sorted([
            str(s) for s in G_baseline.successors(w1)
            if str(s) not in initial_set and str(s) != w1
        ])

        for succ in successors:
            if succ not in G_baseline.nodes:
                continue

            orig_edge = G_baseline.get_edge_data(w1, succ) or {}
            edge_type = orig_edge.get("edge_type", "power_supply")
            weight = float(orig_edge.get("weight", 1.0))
            capacity = float(orig_edge.get("capacity", 100.0))

            # Identify candidate sources among surviving operational nodes.
            #
            # The reachability filter is the fix for the single-point-of-failure
            # blind spot: a node that survived but sits downstream of an
            # unmitigated SPF has no supply to donate, so a redundancy link from
            # it restores nothing. Previously survivorship was only a sort
            # preference, so such a link could be proposed and credited.
            candidate_sources: list[tuple[int, int, float, str]] = []
            for src_cand, data in G_baseline.nodes(data=True):
                src_cand_str = str(src_cand)
                if src_cand_str in initial_set or src_cand_str == succ or src_cand_str == w1:
                    continue
                if G_baseline.has_edge(src_cand_str, succ) or (src_cand_str, succ) in tested_edges:
                    continue
                if src_cand_str not in baseline_reach:
                    continue

                is_survivor = 1 if src_cand_str not in all_failed_ids else 0
                w1_type = G_baseline.nodes[w1].get("node_type", "")
                same_type = 1 if data.get("node_type", "") == w1_type else 0
                cap = float(data.get("capacity", 100.0))
                candidate_sources.append((is_survivor, same_type, cap, src_cand_str))

            # Deterministic sorting: survivors first, matching type, capacity, node id
            candidate_sources.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))

            # Test top candidate redundancy connections
            for _, _, _, src_id in candidate_sources[:2]:
                tested_edges.add((src_id, succ))

                G_cand = G_baseline.copy()
                G_cand.add_edge(src_id, succ, weight=weight, capacity=capacity, edge_type=edge_type)

                # Same model as the baseline, for the reason at the upgrade_node
                # call site above.
                waves_c, _, eff_after_c, _raw_pop_c, _ = run_cascade(
                    G_cand,
                    initial_failures,
                    enforce_edge_semantics=settings.enforce_edge_semantics,
                )
                cand_failed_count = sum(len(w.get("failed_node_ids", [])) for w in waves_c)

                all_failed_cand: set[str] = set()
                for w in waves_c:
                    all_failed_cand.update(str(nid) for nid in w.get("failed_node_ids", []))

                failures_prevented = baseline_failed_count - cand_failed_count
                cand_pop = calculate_population_impact(
                    all_failed_cand, G_baseline
                )["raw_population_affected"]
                raw_population_saved = baseline_raw_pop - cand_pop
                efficiency_gain = round(eff_after_c - baseline_efficiency, 5)

                # Credit downstream recovery only when the target genuinely ends
                # up on a continuous surviving path back to a working source.
                cand_reach = surviving_source_reach(G_cand, all_failed_cand)
                restores_supply_path = succ in cand_reach and succ not in all_failed_cand

                protects_critical = False
                if baseline_failed_hospitals:
                    survived = baseline_failed_hospitals - all_failed_cand
                    protects_critical = len(survived) > 0

                # A candidate that makes the cascade worse is never a mitigation,
                # whatever it does for efficiency. This guard used to be absent,
                # so an edge could be accepted and reported with a negative
                # failures_prevented.
                if failures_prevented >= 0 and (
                    failures_prevented > 0
                    or raw_population_saved > 0
                    or efficiency_gain > 0
                    or protects_critical
                ):
                    scored_candidates.append({
                        "intervention_type": "add_edge",
                        "node_id": src_id,
                        "target_node_id": succ,
                        "proposed_capacity": None,
                        "edge_type": edge_type,
                        "weight": weight,
                        "capacity": capacity,
                        "failures_prevented": failures_prevented,
                        "raw_population_saved": raw_population_saved,
                        "efficiency_gain": efficiency_gain,
                        "protects_critical_services": protects_critical,
                        "restores_supply_path": restores_supply_path,
                        "candidate_failed_count": cand_failed_count,
                        "candidate_efficiency": eff_after_c,
                    })

    # --- 3. Deterministic Ranking ---
    # Prioritize protects_critical_services ahead of raw_population_saved
    # Sort key: (-failures_prevented, -(1 if protects_critical_services else 0),
    #            -raw_population_saved, -efficiency_gain, str(node_id), str(target_node_id or ''))
    sorted_candidates = sorted(
        scored_candidates,
        key=lambda c: (
            -c["failures_prevented"],
            -(1 if c.get("protects_critical_services", False) else 0),
            -c["raw_population_saved"],
            -c["efficiency_gain"],
            str(c["node_id"]),
            str(c.get("target_node_id") or ""),
        ),
    )

    # --- 4. Synthesize Recommendations & Scenario Payloads ---
    recommendations: list[MitigationRecommendation] = []
    net_id_str = str(getattr(simulation, "network_id", ""))

    for idx, cand in enumerate(sorted_candidates[:limit]):
        intervention = cand["intervention_type"]
        failures_prevented = cand["failures_prevented"]
        raw_population_saved = cand["raw_population_saved"]
        efficiency_gain = cand["efficiency_gain"]
        protects_critical = cand.get("protects_critical_services", False)

        if intervention == "upgrade_node":
            node_id_str = str(cand["node_id"])
            node_data = G_baseline.nodes.get(node_id_str, {})
            display_name = node_data.get("display_name") or node_data.get("name") or f"Node {node_id_str[:8]}"
            node_name = node_data.get("name") or display_name
            proposed_capacity = cand["proposed_capacity"]

            root_cause, root_cause_detail = diagnose_failure(
                G_baseline, node_id_str, all_failed_ids, initial_set, supplier_index
            )
            mitigation_kind, action_label = _mitigation_for(
                node_data, root_cause, "upgrade_node", display_name
            )

            scenario_payload = {
                "network_id": net_id_str,
                "name": f"Mitigation: Upgrade {display_name}"[:120],
                "description": (
                    f"Recommended hardening: Upgrade {display_name} capacity to {proposed_capacity}. "
                    f"Projected to prevent {failures_prevented} failures, save {raw_population_saved:,} "
                    f"affected population, and improve efficiency by +{efficiency_gain:.4f}."
                )[:2000],
                "modifications": [
                    {
                        "type": "upgrade_node",
                        "node_id": node_id_str,
                        "capacity": proposed_capacity,
                    }
                ],
                "initial_failures": initial_failures,
            }

            recommendations.append(
                MitigationRecommendation(
                    rank=idx + 1,
                    node_id=_to_deterministic_uuid4(node_id_str),
                    node_name=node_name,
                    display_name=display_name,
                    intervention_type="upgrade_node",
                    proposed_capacity=proposed_capacity,
                    target_node_id=None,
                    target_node_name=None,
                    target_display_name=None,
                    failures_prevented=failures_prevented,
                    raw_population_saved=raw_population_saved,
                    efficiency_gain=efficiency_gain,
                    protects_critical_services=protects_critical,
                    verified=True,
                    root_cause=root_cause,
                    root_cause_detail=root_cause_detail,
                    mitigation_kind=mitigation_kind,
                    action_label=action_label,
                    restores_supply_path=bool(cand.get("restores_supply_path", False)),
                    scenario_payload=scenario_payload,
                )
            )

        elif intervention == "add_edge":
            src_str = str(cand["node_id"])
            tgt_str = str(cand["target_node_id"])
            src_data = G_baseline.nodes.get(src_str, {})
            tgt_data = G_baseline.nodes.get(tgt_str, {})

            src_display = src_data.get("display_name") or src_data.get("name") or f"Node {src_str[:8]}"
            src_name = src_data.get("name") or src_display
            tgt_display = tgt_data.get("display_name") or tgt_data.get("name") or f"Node {tgt_str[:8]}"
            tgt_name = tgt_data.get("name") or tgt_display

            edge_type = cand.get("edge_type", "power_supply")
            edge_weight = float(cand.get("weight", 1.0))
            edge_capacity = float(cand.get("capacity", 100.0))

            # The asset with the problem is the destination: it is the one that
            # lost its supply. The action is therefore described in terms of it,
            # and names the surviving source that would feed it.
            root_cause, root_cause_detail = diagnose_failure(
                G_baseline, tgt_str, all_failed_ids, initial_set, supplier_index
            )
            mitigation_kind, action_label = _mitigation_for(
                tgt_data, root_cause, "add_edge", tgt_display
            )
            if mitigation_kind in {"redundant_link", "redundant_feeder"}:
                action_label = f"Feed {tgt_display} from {src_display} via a redundant {edge_type} link"
            elif mitigation_kind == "reroute":
                action_label = f"Reroute traffic for {tgt_display} via {src_display}"

            scenario_payload = {
                "network_id": net_id_str,
                "name": f"Mitigation: Redundancy {src_display} -> {tgt_display}"[:120],
                "description": (
                    f"Recommended redundancy: Add link from {src_display} to {tgt_display} ({edge_type}). "
                    f"Projected to prevent {failures_prevented} failures, save {raw_population_saved:,} "
                    f"affected population, and improve efficiency by +{efficiency_gain:.4f}."
                )[:2000],
                "modifications": [
                    {
                        "type": "add_edge",
                        "source": src_str,
                        "target": tgt_str,
                        "edge_type": edge_type,
                        "weight": edge_weight,
                        "capacity": edge_capacity,
                        "is_bidirectional": False,
                    }
                ],
                "initial_failures": initial_failures,
            }

            recommendations.append(
                MitigationRecommendation(
                    rank=idx + 1,
                    node_id=_to_deterministic_uuid4(src_str),
                    node_name=src_name,
                    display_name=src_display,
                    intervention_type="add_edge",
                    proposed_capacity=None,
                    target_node_id=_to_deterministic_uuid4(tgt_str),
                    target_node_name=tgt_name,
                    target_display_name=tgt_display,
                    failures_prevented=failures_prevented,
                    raw_population_saved=raw_population_saved,
                    efficiency_gain=efficiency_gain,
                    protects_critical_services=protects_critical,
                    verified=True,
                    root_cause=root_cause,
                    root_cause_detail=root_cause_detail,
                    mitigation_kind=mitigation_kind,
                    action_label=action_label,
                    restores_supply_path=bool(cand.get("restores_supply_path", False)),
                    scenario_payload=scenario_payload,
                )
            )

    return recommendations


# Alias for backward compatibility
generate_mitigation_recommendations = get_recommendations
