"""
Unit tests for canonical recommendations engine (backend/app/services/recommendations.py).
Follows the style of test_cascade.py with small, deliberately constructed graphs.

Covers four mandatory cases:
1. Clear winner case: One node provably prevents more failures than others; ranks first.
2. Empty wave_one case: Initial failure does not cascade; returns empty list without error.
3. No-improvement case: No candidate improves outcome; no false positives fabricated.
4. Self-exclusion case: A node in initial_failures is never proposed as a candidate fix for itself.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import networkx as nx

# Ensure backend directory is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment module shims (sqlalchemy, pydantic, fastapi, ...) are
# installed once for the whole suite by tests/conftest.py, before this file
# is collected.

from app.models.network import SimulationResult
from app.services.recommendations import (
    get_recommendations,
    identify_candidate_nodes,
)
from app.simulation.cascade import run_cascade


def _make_node(
    G: nx.DiGraph,
    node_id: str,
    name: str,
    capacity: float,
    current_load: float,
    failure_threshold: float = 1.0,
    population_served: int = 1000,
    node_type: str = "power_substation",
) -> str:
    """Helper to populate node attributes on DiGraph."""
    G.add_node(
        node_id,
        name=name,
        display_name=name,
        capacity=float(capacity),
        current_load=float(current_load),
        failure_threshold=float(failure_threshold),
        population_served=int(population_served),
        node_type=node_type,
        status="operational",
    )
    return node_id


def test_clear_winner_ranks_first():
    """
    Clear winner case:
    Graph structure with 2 branches off initial failure node A:
    - Branch 1: Node B protects 4 downstream fragile nodes (D, E, F, G).
    - Branch 2: Node C protects 1 downstream fragile node (H).
    Upgrading B prevents 4 cascade failures, while upgrading C prevents only 1.
    Asserts candidate B provably ranks first (rank == 1).
    """
    G = nx.DiGraph()

    # Deterministic UUID4s
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())
    node_c = str(uuid.uuid4())
    node_d = str(uuid.uuid4())
    node_e = str(uuid.uuid4())
    node_f = str(uuid.uuid4())
    node_g = str(uuid.uuid4())
    node_h = str(uuid.uuid4())

    _make_node(G, node_a, "Node A", capacity=50.0, current_load=20.0)
    _make_node(G, node_b, "Node B", capacity=12.0, current_load=5.0)
    _make_node(G, node_c, "Node C", capacity=12.0, current_load=5.0)

    # 4 fragile nodes downstream of B
    for nid, name in [(node_d, "D"), (node_e, "E"), (node_f, "F"), (node_g, "G")]:
        _make_node(G, nid, f"Node {name}", capacity=6.0, current_load=5.0, population_served=200)

    # 1 fragile node downstream of C
    _make_node(G, node_h, "Node H", capacity=6.0, current_load=5.0, population_served=200)

    # Topology edges
    # Load-redistribution fixture: `depends_on` carries load like any other
    # edge but declares no critical service, so the only failure mechanism
    # here is overload -- which is what this test is about. With
    # `power_supply` these single-feed chains also fail by dependency
    # severing, and a capacity upgrade cannot prevent that, so every
    # candidate would correctly score zero and the ranking would go untested.
    G.add_edge(node_a, node_b, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_a, node_c, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_b, node_d, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_b, node_e, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_b, node_f, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_b, node_g, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_c, node_h, weight=1.0, capacity=100.0, edge_type="depends_on")

    # Run baseline cascade
    waves, _, eff_a, _, _ = run_cascade(G, [node_a])
    total_failed = sum(len(w["failed_node_ids"]) for w in waves)
    assert total_failed == 8  # A, B, C, D, E, F, G, H all fail

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=total_failed,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert len(recs) >= 2

    # Clear winner assertion: Candidate B must rank #1
    top_rec = recs[0]
    assert top_rec.rank == 1
    assert str(top_rec.node_id) == node_b
    assert top_rec.failures_prevented == 5
    assert top_rec.intervention_type == "upgrade_node"
    assert top_rec.verified is True

    # Second place must be Candidate C
    second_rec = recs[1]
    assert second_rec.rank == 2
    assert str(second_rec.node_id) == node_c
    assert second_rec.failures_prevented == 2



def test_empty_wave_one_returns_empty():
    """
    Empty wave_one case:
    Initial failure node A does not overload successor B.
    Simulation terminates with 0 secondary casualties (total_failed == 1).
    Asserts returns empty list [] cleanly without error or exceptions.
    """
    G = nx.DiGraph()
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())

    _make_node(G, node_a, "Node A", capacity=10.0, current_load=5.0)
    _make_node(G, node_b, "Node B", capacity=100.0, current_load=5.0)  # High capacity absorbs load
    G.add_edge(node_a, node_b, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [node_a])
    total_failed = sum(len(w["failed_node_ids"]) for w in waves)
    assert total_failed == 1
    assert len(waves) == 1

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=1,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=10)
    assert recs == []


def test_no_improvement_candidates_excluded():
    """
    No-improvement case:
    Cascade load overwhelmingly exceeds even 2x candidate capacity.
    No candidate can prevent failures or save population.
    Asserts zero false positive candidates are fabricated (returns empty list []).
    """
    G = nx.DiGraph()
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())
    node_c = str(uuid.uuid4())

    # Massive load from A that far exceeds 2x capacity of B (15.0 * 2 = 30.0 << 1000.0)
    _make_node(G, node_a, "Node A", capacity=100.0, current_load=1000.0)
    _make_node(G, node_b, "Node B", capacity=15.0, current_load=10.0)
    _make_node(G, node_c, "Node C", capacity=15.0, current_load=10.0)

    G.add_edge(node_a, node_b, weight=1.0, capacity=5000.0, edge_type="power_supply")
    G.add_edge(node_b, node_c, weight=1.0, capacity=5000.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [node_a])
    assert len(waves) > 1

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    # No candidate improved the outcome, so no false positives may be returned
    assert recs == []


def test_self_exclusion_initial_failures_omitted():
    """
    Self-exclusion case:
    A node already in initial_failures is strictly excluded from candidate selection
    and never proposed as an intervention fix for itself.
    """
    G = nx.DiGraph()
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())
    node_c = str(uuid.uuid4())

    _make_node(G, node_a, "Node A", capacity=50.0, current_load=20.0)
    _make_node(G, node_b, "Node B", capacity=10.0, current_load=5.0)
    _make_node(G, node_c, "Node C", capacity=10.0, current_load=5.0)

    G.add_edge(node_a, node_b, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(node_b, node_c, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [node_a])

    # 1. Candidate identification function level
    candidate_ids = identify_candidate_nodes(G, waves, [node_a])
    assert node_a not in candidate_ids

    # 2. Recommendations level
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    for r in recs:
        assert str(r.node_id) != node_a
        assert str(getattr(r, "target_node_id", None)) != node_a


def test_protects_critical_services_hospital_priority():
    """
    Verifies that protects_critical_services is prioritized ahead of raw_population_saved.
    Candidate 1 (protects a hospital) saves 1,100 population.
    Candidate 2 (does not protect a hospital) saves 11,000 population.
    Both candidates prevent 2 failures.
    Asserts Candidate 1 ranks #1 because protects_critical_services == True.
    """
    G = nx.DiGraph()
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())
    node_c = str(uuid.uuid4())
    node_h = str(uuid.uuid4())  # Hospital
    node_p = str(uuid.uuid4())  # Residential

    _make_node(G, node_a, "Root Substation A", capacity=50.0, current_load=20.0)
    _make_node(G, node_b, "Substation B", capacity=12.0, current_load=5.0, population_served=1000)
    _make_node(G, node_c, "Substation C", capacity=12.0, current_load=5.0, population_served=1000)
    _make_node(
        G,
        node_h,
        "City Hospital MC-01",
        capacity=6.0,
        current_load=5.0,
        population_served=100,
        node_type="hospital",
    )
    _make_node(
        G,
        node_p,
        "Residential Sector",
        capacity=6.0,
        current_load=5.0,
        population_served=10000,
        node_type="power_substation",
    )

    # Topology: A -> B -> H (Hospital), A -> C -> P (Residential)
    # Load-redistribution fixture: `depends_on` carries load like any other
    # edge but declares no critical service, so the only failure mechanism
    # here is overload -- which is what this test is about. With
    # `power_supply` these single-feed chains also fail by dependency
    # severing, and a capacity upgrade cannot prevent that, so every
    # candidate would correctly score zero and the ranking would go untested.
    G.add_edge(node_a, node_b, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_a, node_c, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_b, node_h, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(node_c, node_p, weight=1.0, capacity=100.0, edge_type="depends_on")

    # Baseline cascade: A fails, sends 10 to B and 10 to C. Both B and C overload and fail.
    # Then B sends to H (fails), C sends to P (fails). All 5 nodes fail.
    waves, _, eff_a, _, _ = run_cascade(G, [node_a])
    assert len(waves) > 1
    total_failed = sum(len(w["failed_node_ids"]) for w in waves)
    assert total_failed == 5

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=total_failed,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert len(recs) >= 2

    # Candidate 1: Substation B (protects hospital node H)
    # Candidate 2: Substation C (protects residential node P)
    # Even though C saves 11,000 population vs B's 1,100, B must rank #1 due to hospital protection!
    assert recs[0].rank == 1
    assert str(recs[0].node_id) == node_b
    assert recs[0].protects_critical_services is True
    assert recs[0].failures_prevented == 2
    assert recs[0].raw_population_saved == 1100

    assert recs[1].rank == 2
    assert str(recs[1].node_id) == node_c
    assert recs[1].protects_critical_services is False
    assert recs[1].failures_prevented == 2
    assert recs[1].raw_population_saved == 11000


def test_add_edge_redundancy_candidate_generated():
    """
    Verifies that the recommendation engine generates and evaluates add_edge redundancy candidates
    connecting surviving operational nodes to disconnected downstream successors of wave-1 casualties.
    """
    G = nx.DiGraph()
    node_a = str(uuid.uuid4())
    node_b = str(uuid.uuid4())
    node_c = str(uuid.uuid4())
    node_s = str(uuid.uuid4())  # Surviving operational generator / substation
    node_t = str(uuid.uuid4())  # Grid node connected to S

    # A overloads B (wave 1 casualty)
    _make_node(G, node_a, "Root Substation A", capacity=50.0, current_load=15.0)
    _make_node(G, node_b, "Substation B", capacity=10.0, current_load=5.0)

    # C has high capacity, so it survives load from B but loses upstream connectivity
    _make_node(G, node_c, "Terminal Substation C", capacity=200.0, current_load=5.0)

    # S and T form a surviving operational component
    _make_node(G, node_s, "Surviving Gen S", capacity=100.0, current_load=5.0)
    _make_node(G, node_t, "Grid Node T", capacity=100.0, current_load=5.0)

    # Topology: A -> B -> C; S <-> T
    G.add_edge(node_a, node_b, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(node_b, node_c, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(node_s, node_t, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(node_t, node_s, weight=1.0, capacity=100.0, edge_type="power_supply")

    # Baseline cascade: A fails (wave 0), sends 15 to B. B fails (wave 1, load 20 > 10).
    # B sends 20 to C. C absorbs 20 (load 25 <= 200) and SURVIVES.
    # C is now isolated from S and T.
    waves, _, eff_a, _, _ = run_cascade(G, [node_a])
    assert len(waves) == 2  # Wave 0 (A), Wave 1 (B)
    total_failed = sum(len(w["failed_node_ids"]) for w in waves)
    assert total_failed == 2

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[node_a],
        waves=waves,
        total_failed=total_failed,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=10)
    add_edge_recs = [r for r in recs if r.intervention_type == "add_edge"]

    assert len(add_edge_recs) >= 1
    rec = add_edge_recs[0]
    assert rec.intervention_type == "add_edge"
    assert str(rec.node_id) in [node_s, node_t]
    assert str(rec.target_node_id) == node_c
    assert rec.verified is True
    assert rec.efficiency_gain > 0
    assert rec.scenario_payload["network_id"] == str(sim.network_id)
    assert len(rec.scenario_payload["modifications"]) == 1

    mod = rec.scenario_payload["modifications"][0]
    assert mod["type"] == "add_edge"
    assert mod["source"] in [node_s, node_t]
    assert mod["target"] == node_c

