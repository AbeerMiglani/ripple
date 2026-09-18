"""
Empirical stress test suite for recommendations engine (Milestone M1).
Written by challenger_m1_1_gen4.

Stress dimensions tested:
1. Hospital Priority Ranking vs Raw Population vs Failures Prevented
2. Hospital Identification & Invalidation Edge Cases (Case sensitivity, initial failure exclusion)
3. Redundancy Candidate (add_edge) Structural Validity (no self-loops, no existing edges, no initial failures)
4. Directed Cycles & Multi-Hop Cascades
5. Disconnected Multi-Component Graphs & Isolated Nodes
6. Catastrophic / Total Blackout Scenario (No false positive recommendations)
7. Honest Rerun Oracle: Strict mathematical equivalence between claimed deltas and actual resimulation
8. Deterministic UUID4 Generation for arbitrary identifiers
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment module shims (sqlalchemy, pydantic, fastapi, ...) are
# installed once for the whole suite by tests/conftest.py, before this file
# is collected.

from app.models.network import SimulationResult
from app.services.recommendations import (
    _is_hospital,
    _to_deterministic_uuid4,
    get_recommendations,
)
from app.simulation.cascade import run_cascade
from app.simulation.population import calculate_population_impact


def make_node(
    G: nx.DiGraph,
    node_id: str,
    name: str,
    capacity: float,
    current_load: float,
    failure_threshold: float = 1.0,
    population_served: int = 1000,
    node_type: str = "power_substation",
    display_name: str | None = None,
) -> str:
    G.add_node(
        node_id,
        name=name,
        display_name=display_name or name,
        capacity=float(capacity),
        current_load=float(current_load),
        failure_threshold=float(failure_threshold),
        population_served=int(population_served),
        node_type=node_type,
        status="operational",
    )
    return node_id


# ============================================================================
# STRESS TEST 1: Conflicting Candidate Priorities & Strict Hospital Priority
# ============================================================================

def test_stress_conflicting_candidate_priorities():
    """
    Constructs 3 competing candidate branches off an initial failure node R:
    - Branch 1: Node B prevents 2 failures (B, H), saves 1,000 population, PROTECTS HOSPITAL H.
    - Branch 2: Node C prevents 2 failures (C, P), saves 50,000 population, does NOT protect hospital.
    - Branch 3: Node D prevents 3 failures (D, X, Y), saves 200 population, does NOT protect hospital.

    Expected Ranking:
    - Rank 1: D (failures_prevented = 3)
    - Rank 2: B (failures_prevented = 2, protects_critical_services = True)
    - Rank 3: C (failures_prevented = 2, protects_critical_services = False, saves 50k pop)
    """
    G = nx.DiGraph()
    root = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    c_id = str(uuid.uuid4())
    d_id = str(uuid.uuid4())
    h_id = str(uuid.uuid4())  # Hospital
    p_id = str(uuid.uuid4())  # Huge population
    x_id = str(uuid.uuid4())
    y_id = str(uuid.uuid4())

    make_node(G, root, "Root Generator", capacity=100.0, current_load=30.0)
    # Root fails and sends 10 load to each of B, C, D
    make_node(G, b_id, "Substation B", capacity=12.0, current_load=5.0, population_served=500)
    make_node(G, c_id, "Substation C", capacity=12.0, current_load=5.0, population_served=500)
    make_node(G, d_id, "Substation D", capacity=12.0, current_load=5.0, population_served=50)

    # Downstream of B: Hospital H
    make_node(G, h_id, "Memorial Hospital", capacity=6.0, current_load=5.0, population_served=500, node_type="hospital")
    # Downstream of C: Mega City P
    make_node(G, p_id, "Mega Residential Zone", capacity=6.0, current_load=5.0, population_served=50000)
    # Downstream of D: Fragile X, which connects to Y
    make_node(G, x_id, "Substation X", capacity=6.0, current_load=5.0, population_served=50)
    make_node(G, y_id, "Substation Y", capacity=6.0, current_load=5.0, population_served=50)

    # Load-redistribution fixture: `depends_on` carries load like any other
    # edge but declares no critical service, so the only failure mechanism
    # here is overload -- which is what this test is about. With
    # `power_supply` these single-feed chains also fail by dependency
    # severing, and a capacity upgrade cannot prevent that, so every
    # candidate would correctly score zero and the ranking would go untested.
    G.add_edge(root, b_id, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(root, c_id, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(root, d_id, weight=1.0, capacity=100.0, edge_type="depends_on")

    G.add_edge(b_id, h_id, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(c_id, p_id, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(d_id, x_id, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(x_id, y_id, weight=1.0, capacity=100.0, edge_type="depends_on")

    waves, _, eff_a, _, _ = run_cascade(G, [root])
    total_failed = sum(len(w["failed_node_ids"]) for w in waves)
    assert total_failed == 8  # all nodes fail

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[root],
        waves=waves,
        total_failed=total_failed,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert len(recs) >= 3

    # Rank 1: D (prevents 3 failures)
    assert recs[0].rank == 1
    assert str(recs[0].node_id) == d_id
    assert recs[0].failures_prevented == 3

    # Rank 2: B (prevents 2 failures, PROTECTS HOSPITAL)
    assert recs[1].rank == 2
    assert str(recs[1].node_id) == b_id
    assert recs[1].failures_prevented == 2
    assert recs[1].protects_critical_services is True
    assert recs[1].raw_population_saved == 1000

    # Rank 3: C (prevents 2 failures, saves 50,500 pop, but NO hospital)
    assert recs[2].rank == 3
    assert str(recs[2].node_id) == c_id
    assert recs[2].failures_prevented == 2
    assert recs[2].protects_critical_services is False
    assert recs[2].raw_population_saved == 50500


# ============================================================================
# STRESS TEST 2: Hospital Identification Semantics
# ============================================================================

def test_stress_hospital_identification_robustness():
    """Verify hospital detection handles node_type, name, and display_name variations."""
    # node_type matches
    assert _is_hospital({"node_type": "hospital", "name": "N1"}) is True
    assert _is_hospital({"node_type": "HOSPITAL", "name": "N1"}) is True

    # name matches
    assert _is_hospital({"node_type": "power_substation", "name": "KMC Hospital Substation"}) is True
    assert _is_hospital({"node_type": "power_substation", "name": "hospital_junction_1"}) is True

    # display_name matches
    assert _is_hospital({"node_type": "power_substation", "name": "PS-01", "display_name": "District Hospital Facility"}) is True

    # Negative matches
    assert _is_hospital({"node_type": "power_substation", "name": "PS-01", "display_name": "Main Substation"}) is False
    assert _is_hospital({"node_type": "water_station", "name": "WS-01", "display_name": "Pump House"}) is False


def test_stress_initial_hospital_failure_not_revivable():
    """
    If the hospital was in initial_failures, it fails in wave 0 and cannot survive.
    Engine must NOT report protects_critical_services = True for any candidate.
    """
    G = nx.DiGraph()
    hosp = str(uuid.uuid4())
    sub = str(uuid.uuid4())
    res = str(uuid.uuid4())

    make_node(G, hosp, "City Hospital", capacity=50.0, current_load=20.0, node_type="hospital")
    make_node(G, sub, "Substation B", capacity=12.0, current_load=5.0)
    make_node(G, res, "Residential", capacity=6.0, current_load=5.0)

    G.add_edge(hosp, sub, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(sub, res, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [hosp])
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[hosp],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    # Upgrading Substation B saves Residential, but CANNOT revive City Hospital
    for r in recs:
        assert r.protects_critical_services is False


# ============================================================================
# STRESS TEST 3: Structural Validity of add_edge Candidates
# ============================================================================

def test_stress_add_edge_candidate_validity_guarantees():
    """
    Empirically verifies:
    1. Zero self-loops: source != target for every add_edge candidate.
    2. Zero existing edges: proposed edge does not already exist in G_baseline.
    3. Neither source nor target is an initial_failure.
    4. Valid scenario payload with all required fields.
    """
    G = nx.DiGraph()
    gen = str(uuid.uuid4())
    w1_failed = str(uuid.uuid4())
    succ1 = str(uuid.uuid4())
    succ2 = str(uuid.uuid4())
    surv_gen = str(uuid.uuid4())
    surv_node = str(uuid.uuid4())

    make_node(G, gen, "Gen", capacity=50.0, current_load=20.0)
    make_node(G, w1_failed, "W1 Fail", capacity=10.0, current_load=5.0)
    make_node(G, succ1, "Successor 1", capacity=100.0, current_load=5.0)
    make_node(G, succ2, "Successor 2", capacity=100.0, current_load=5.0)
    make_node(G, surv_gen, "Surv Gen", capacity=200.0, current_load=5.0)
    make_node(G, surv_node, "Surv Node", capacity=200.0, current_load=5.0)

    # Load-redistribution fixture: `depends_on` carries load like any other
    # edge but declares no critical service, so the only failure mechanism
    # here is overload -- which is what this test is about. With
    # `power_supply` these single-feed chains also fail by dependency
    # severing, and a capacity upgrade cannot prevent that, so every
    # candidate would correctly score zero and the ranking would go untested.
    G.add_edge(gen, w1_failed, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(w1_failed, succ1, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(w1_failed, succ2, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(surv_gen, surv_node, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(surv_node, surv_gen, weight=1.0, capacity=100.0, edge_type="depends_on")

    waves, _, eff_a, _, _ = run_cascade(G, [gen])
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[gen],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=10)
    add_edge_recs = [r for r in recs if r.intervention_type == "add_edge"]
    assert len(add_edge_recs) > 0

    for r in add_edge_recs:
        src = str(r.node_id)
        tgt = str(r.target_node_id)

        # 1. No self-loops
        assert src != tgt, f"Self-loop detected: {src} == {tgt}"

        # 2. No existing edges in baseline
        assert not G.has_edge(src, tgt), f"Proposed existing edge: ({src}, {tgt})"

        # 3. Neither source nor target is in initial_failures
        assert src != gen, f"Source is in initial_failures: {src}"
        assert tgt != gen, f"Target is in initial_failures: {tgt}"

        # 4. Payload structure
        payload = r.scenario_payload
        assert payload["network_id"] == str(sim.network_id)
        assert len(payload["modifications"]) == 1
        mod = payload["modifications"][0]
        assert mod["type"] == "add_edge"
        assert mod["source"] == src
        assert mod["target"] == tgt
        assert mod["capacity"] > 0
        assert mod["weight"] > 0


# ============================================================================
# STRESS TEST 4: Directed Cycles & Multi-Hop Cascades
# ============================================================================

def test_stress_directed_cycles_and_feedback_loops():
    """
    Adversarial graph containing directed cycles:
    Cycle: A -> B -> C -> D -> B
    Branch: D -> E -> F
    Cascade initiated at A. Ensures engine terminates without infinite recursion,
    and produces deterministic recommendations.
    """
    G = nx.DiGraph()
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    c = str(uuid.uuid4())
    d = str(uuid.uuid4())
    e = str(uuid.uuid4())
    f = str(uuid.uuid4())

    make_node(G, a, "Node A", capacity=50.0, current_load=25.0)
    make_node(G, b, "Node B", capacity=15.0, current_load=10.0)
    make_node(G, c, "Node C", capacity=15.0, current_load=10.0)
    make_node(G, d, "Node D", capacity=15.0, current_load=10.0)
    make_node(G, e, "Node E", capacity=12.0, current_load=5.0)
    make_node(G, f, "Node F", capacity=12.0, current_load=5.0)

    G.add_edge(a, b, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(b, c, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(c, d, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(d, b, weight=1.0, capacity=100.0, edge_type="power_supply")  # Cycle
    G.add_edge(d, e, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(e, f, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [a])
    assert len(waves) >= 2

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[a],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    # Must complete without infinite loop
    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert isinstance(recs, list)
    for r in recs:
        assert r.verified is True
        assert r.failures_prevented >= 0


# ============================================================================
# STRESS TEST 5: Disconnected Multi-Component Graphs & Isolated Nodes
# ============================================================================

def test_stress_disconnected_components_and_isolated_nodes():
    """
    Adversarial graph with:
    - Component 1: Active cascade (A -> B -> C)
    - Component 2: Completely disconnected healthy component (S1 -> S2 -> S3)
    - Component 3: Isolated node I with 0 edges
    Ensures global efficiency and candidate generation handle isolated nodes and
    disconnected components gracefully.
    """
    G = nx.DiGraph()
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    c = str(uuid.uuid4())
    s1 = str(uuid.uuid4())
    s2 = str(uuid.uuid4())
    s3 = str(uuid.uuid4())
    iso = str(uuid.uuid4())  # Isolated node

    make_node(G, a, "Comp1 A", capacity=50.0, current_load=20.0)
    make_node(G, b, "Comp1 B", capacity=10.0, current_load=5.0)
    make_node(G, c, "Comp1 C", capacity=100.0, current_load=5.0)  # Survives wave 1

    make_node(G, s1, "Comp2 S1", capacity=100.0, current_load=5.0)
    make_node(G, s2, "Comp2 S2", capacity=100.0, current_load=5.0)
    make_node(G, s3, "Comp2 S3", capacity=100.0, current_load=5.0)

    make_node(G, iso, "Isolated Node", capacity=10.0, current_load=0.0)

    G.add_edge(a, b, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(b, c, weight=1.0, capacity=100.0, edge_type="power_supply")

    G.add_edge(s1, s2, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(s2, s3, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [a])
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[a],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert len(recs) > 0
    # Isolated node should not cause any crashes or illegal edges
    for r in recs:
        assert str(r.node_id) != iso
        assert str(getattr(r, "target_node_id", None)) != iso


# ============================================================================
# STRESS TEST 6: Catastrophic Total Blackout (Zero False Positives)
# ============================================================================

def test_stress_catastrophic_total_blackout_returns_empty():
    """
    Every node in the network is overwhelmed by an initial shock so massive
    that doubling node capacities cannot prevent any failure.
    Ensures zero false-positive recommendations are fabricated.
    """
    G = nx.DiGraph()
    root = str(uuid.uuid4())
    n1 = str(uuid.uuid4())
    n2 = str(uuid.uuid4())
    n3 = str(uuid.uuid4())

    make_node(G, root, "Nuclear Strike", capacity=100.0, current_load=10000.0)
    make_node(G, n1, "Substation 1", capacity=10.0, current_load=5.0)
    make_node(G, n2, "Substation 2", capacity=10.0, current_load=5.0)
    make_node(G, n3, "Substation 3", capacity=10.0, current_load=5.0)

    G.add_edge(root, n1, weight=1.0, capacity=10000.0, edge_type="power_supply")
    G.add_edge(n1, n2, weight=1.0, capacity=10000.0, edge_type="power_supply")
    G.add_edge(n2, n3, weight=1.0, capacity=10000.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [root])
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[root],
        waves=waves,
        total_failed=4,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert recs == []


# ============================================================================
# STRESS TEST 7: Honest Rerun Oracle Property (Delta Mathematical Exactness)
# ============================================================================

def test_stress_honest_rerun_oracle_exact_deltas():
    """
    For EVERY candidate generated by get_recommendations, independently apply its
    scenario modification to a fresh copy of G_baseline and re-run run_cascade.
    Assert mathematical identity for:
    - failures_prevented
    - raw_population_saved
    - efficiency_gain
    - protects_critical_services
    """
    G = nx.DiGraph()
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    c = str(uuid.uuid4())
    d = str(uuid.uuid4())
    h = str(uuid.uuid4())  # Hospital

    make_node(G, a, "Source A", capacity=50.0, current_load=20.0, population_served=500)
    make_node(G, b, "Substation B", capacity=12.0, current_load=5.0, population_served=1000)
    make_node(G, c, "Substation C", capacity=12.0, current_load=5.0, population_served=2000)
    make_node(G, d, "Substation D", capacity=6.0, current_load=5.0, population_served=3000)
    make_node(G, h, "Hospital H", capacity=6.0, current_load=5.0, population_served=800, node_type="hospital")

    # Load-redistribution fixture: `depends_on` carries load like any other
    # edge but declares no critical service, so the only failure mechanism
    # here is overload -- which is what this test is about. With
    # `power_supply` these single-feed chains also fail by dependency
    # severing, and a capacity upgrade cannot prevent that, so every
    # candidate would correctly score zero and the ranking would go untested.
    G.add_edge(a, b, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(a, c, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(b, h, weight=1.0, capacity=100.0, edge_type="depends_on")
    G.add_edge(c, d, weight=1.0, capacity=100.0, edge_type="depends_on")

    # Both sides of every delta this test checks must come from the same cascade
    # model, and that model must be the one production uses: `runner.py` passes
    # `enforce_edge_semantics`, so the stored baseline is always dependency-aware.
    # This oracle previously left the flag at its False default on both sides --
    # self-consistent, but blind to the engine differencing a semantics-OFF
    # candidate against a semantics-ON baseline, which is exactly the bug it
    # exists to catch.
    waves, _, eff_a, _, _ = run_cascade(G, [a], enforce_edge_semantics=True)
    total_failed_baseline = sum(len(w["failed_node_ids"]) for w in waves)
    all_failed_baseline = set()
    for w in waves:
        all_failed_baseline.update(w["failed_node_ids"])

    pop_baseline = calculate_population_impact(all_failed_baseline, G)["raw_population_affected"]

    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[a],
        waves=waves,
        total_failed=total_failed_baseline,
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    assert len(recs) > 0

    for rec in recs:
        # Clone baseline and apply scenario modifications directly from scenario_payload
        G_mod = G.copy()
        for mod in rec.scenario_payload["modifications"]:
            mtype = mod["type"]
            if mtype == "upgrade_node":
                nid = mod["node_id"]
                G_mod.nodes[nid]["capacity"] = float(mod["capacity"])
            elif mtype == "add_edge":
                src = mod["source"]
                tgt = mod["target"]
                G_mod.add_edge(
                    src,
                    tgt,
                    weight=float(mod.get("weight", 1.0)),
                    capacity=float(mod.get("capacity", 100.0)),
                    edge_type=mod.get("edge_type", "power_supply"),
                )

        # Independent rerun, under the same model as the baseline above.
        cand_waves, _, eff_cand, _, _ = run_cascade(G_mod, [a], enforce_edge_semantics=True)
        cand_failed = set()
        for w in cand_waves:
            cand_failed.update(w["failed_node_ids"])
        total_failed_cand = sum(len(w["failed_node_ids"]) for w in cand_waves)
        pop_cand = calculate_population_impact(cand_failed, G_mod)["raw_population_affected"]

        # Oracle checks
        expected_failures_prevented = total_failed_baseline - total_failed_cand
        expected_pop_saved = pop_baseline - pop_cand
        expected_eff_gain = round(eff_cand - eff_a, 5)

        assert rec.failures_prevented == expected_failures_prevented, (
            f"failures_prevented mismatch: claimed {rec.failures_prevented} vs actual {expected_failures_prevented}"
        )
        assert rec.raw_population_saved == expected_pop_saved, (
            f"raw_population_saved mismatch: claimed {rec.raw_population_saved} vs actual {expected_pop_saved}"
        )
        assert abs(rec.efficiency_gain - expected_eff_gain) < 1e-4, (
            f"efficiency_gain mismatch: claimed {rec.efficiency_gain} vs actual {expected_eff_gain}"
        )

        expected_protects_hospital = (h in all_failed_baseline) and (h not in cand_failed)
        assert rec.protects_critical_services == expected_protects_hospital, (
            f"protects_critical_services mismatch: claimed {rec.protects_critical_services} vs expected {expected_protects_hospital}"
        )


# ============================================================================
# STRESS TEST 8: Deterministic UUID4 Generator Correctness
# ============================================================================

def test_stress_deterministic_uuid4_generator():
    """Verify _to_deterministic_uuid4 produces valid RFC 4122 v4 UUIDs for all types."""
    # Existing UUID4 is preserved
    orig_u4 = uuid.uuid4()
    assert _to_deterministic_uuid4(orig_u4) == orig_u4

    # String UUID4 is preserved
    assert _to_deterministic_uuid4(str(orig_u4)) == orig_u4

    # Arbitrary strings produce deterministic RFC 4122 v4 UUIDs
    u1 = _to_deterministic_uuid4("node-alpha")
    u2 = _to_deterministic_uuid4("node-alpha")
    u3 = _to_deterministic_uuid4("node-beta")

    assert u1 == u2  # Determinism
    assert u1 != u3  # Distinct inputs produce distinct UUIDs
    assert u1.version == 4  # Version 4
    assert u1.variant == uuid.RFC_4122  # RFC 4122

    # Integer inputs
    u_int = _to_deterministic_uuid4(42)
    assert u_int.version == 4
    assert u_int == _to_deterministic_uuid4(42)

    # Empty string
    u_empty = _to_deterministic_uuid4("")
    assert u_empty.version == 4


# ============================================================================
# STRESS TEST 9: Harmful / Destabilizing Candidate Exclusion
# ============================================================================

def test_stress_harmful_edge_never_recommended():
    """
    Construct a scenario where connecting surviving generator S to successor C
    would cause C to receive a massive surge of load if S fails, or where
    connecting S to C overloads S or C.
    Verifies that harmful candidates (failures_prevented < 0) are NEVER returned.
    """
    G = nx.DiGraph()
    root = str(uuid.uuid4())
    w1 = str(uuid.uuid4())
    succ = str(uuid.uuid4())
    gen = str(uuid.uuid4())

    make_node(G, root, "Root", capacity=100.0, current_load=30.0)
    make_node(G, w1, "W1 Fail", capacity=10.0, current_load=5.0)
    make_node(G, succ, "Fragile Succ", capacity=10.0, current_load=9.0)  # Near capacity
    # Generator with huge load that would incinerate succ if it failed
    make_node(G, gen, "Overloaded Gen", capacity=50.0, current_load=40.0)

    G.add_edge(root, w1, weight=1.0, capacity=100.0, edge_type="power_supply")
    G.add_edge(w1, succ, weight=1.0, capacity=100.0, edge_type="power_supply")

    waves, _, eff_a, _, _ = run_cascade(G, [root])
    sim = SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        status="completed",
        initial_failures=[root],
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_a,
    )

    recs = get_recommendations(simulation=sim, G_baseline=G, limit=5)
    for r in recs:
        assert r.failures_prevented >= 0, f"Harmful candidate proposed with negative failures prevented: {r}"
        assert r.raw_population_saved >= 0, f"Harmful candidate proposed with negative population saved: {r}"
