"""Recommendations for a scenario run are scored against the scenario's topology.

A scenario run records the scenario it applied (``SimulationResult.scenario_id``).
Its recommendations used to be computed against the *unmodified* network
anyway: every candidate rerun reproduced the original, worse cascade, so its
deltas against the scenario's own (improved) baseline came out negative and the
UI reported that nothing further could help. The payloads also dropped the
modification already in place, so applying one silently undid the previous
intervention.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import networkx as nx

from app.config import settings
from app.models.network import Scenario, SimulationResult
from app.services.recommendations import get_recommendations
from app.simulation.cascade import run_cascade
from app.simulation.scenario import apply_scenario_modifications


def _node(G, name, capacity, current_load, population_served=1000):
    node_id = str(uuid.uuid4())
    G.add_node(
        node_id,
        name=name,
        display_name=name,
        capacity=float(capacity),
        current_load=float(current_load),
        failure_threshold=1.0,
        population_served=population_served,
        node_type="power_substation",
        status="operational",
    )
    return node_id


def _two_branch_network():
    """A feeds B and C; B protects four leaves, C protects one.

    Failing A sheds 10 units onto each of B and C (5 + 10 > 12), taking both
    down along with every leaf. ``depends_on`` links carry load but declare no
    critical service, so overload is the only failure mechanism.
    """
    G = nx.DiGraph()
    a = _node(G, "A", 50.0, 20.0)
    b = _node(G, "B", 12.0, 5.0)
    c = _node(G, "C", 12.0, 5.0)
    b_leaves = [_node(G, f"B{i}", 6.0, 5.0, population_served=200) for i in range(4)]
    h = _node(G, "H", 6.0, 5.0, population_served=200)
    for u, v in [(a, b), (a, c), *((b, leaf) for leaf in b_leaves), (c, h)]:
        G.add_edge(u, v, weight=1.0, capacity=100.0, edge_type="depends_on")
    return G, a, b, c, h


def _run(G, initial_failures, scenario_id=None):
    waves, _, eff_after, _, _ = run_cascade(
        G, initial_failures, enforce_edge_semantics=settings.enforce_edge_semantics
    )
    return SimulationResult(
        id=uuid.uuid4(),
        network_id=uuid.uuid4(),
        scenario_id=scenario_id,
        status="completed",
        initial_failures=initial_failures,
        waves=waves,
        total_failed=sum(len(w["failed_node_ids"]) for w in waves),
        global_efficiency_after=eff_after,
    )


def test_scenario_run_is_scored_against_the_modified_topology():
    G, a, b, c, h = _two_branch_network()
    upgrade_b = [{"type": "upgrade_node", "node_id": b, "capacity": 24.0}]

    # The scenario run: B survives the shock, so only A, C and H fail.
    scenario_run = _run(apply_scenario_modifications(G, upgrade_b), [a])
    assert scenario_run.total_failed == 3

    recs = get_recommendations(
        simulation=scenario_run, G_baseline=G, limit=5, scenario_modifications=upgrade_b
    )

    assert recs, "an improved network still has C and H to save"
    top = recs[0]
    assert str(top.node_id) == c
    # Measured against the scenario's own 3 failures, not the raw network's 8.
    assert top.failures_prevented == 2
    assert all(r.failures_prevented >= 0 for r in recs)
    # B no longer fails, so it is not a candidate a second time.
    assert all(str(r.node_id) != b for r in recs)


def test_payload_stacks_on_the_modification_already_applied():
    G, a, b, c, _h = _two_branch_network()
    upgrade_b = [{"type": "upgrade_node", "node_id": b, "capacity": 24.0}]
    scenario_run = _run(apply_scenario_modifications(G, upgrade_b), [a])

    recs = get_recommendations(
        simulation=scenario_run, G_baseline=G, limit=5, scenario_modifications=upgrade_b
    )

    mods = recs[0].scenario_payload["modifications"]
    assert mods[0] == upgrade_b[0], "the applied intervention must be carried forward"
    assert mods[-1]["type"] == "upgrade_node" and mods[-1]["node_id"] == c
    assert len(mods) == 2


def test_modifications_are_loaded_from_the_runs_scenario():
    G, a, b, c, _h = _two_branch_network()
    upgrade_b = [{"type": "upgrade_node", "node_id": b, "capacity": 24.0}]
    scenario_id = uuid.uuid4()
    scenario_run = _run(apply_scenario_modifications(G, upgrade_b), [a], scenario_id=scenario_id)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        id=scenario_id, modifications=upgrade_b
    )

    recs = get_recommendations(simulation=scenario_run, db=db, G_baseline=G, limit=5)

    assert recs and str(recs[0].node_id) == c
    assert recs[0].failures_prevented == 2
    assert recs[0].scenario_payload["modifications"][0] == upgrade_b[0]
    db.query.assert_called_with(Scenario)


def test_baseline_run_is_unchanged_by_the_scenario_path():
    """A run with no scenario still ranks exactly as before."""
    G, a, b, c, _h = _two_branch_network()
    baseline_run = _run(G, [a])

    db = MagicMock()
    recs = get_recommendations(simulation=baseline_run, db=db, G_baseline=G, limit=5)

    db.query.assert_not_called()
    assert str(recs[0].node_id) == b
    assert recs[0].failures_prevented == 5
    assert len(recs[0].scenario_payload["modifications"]) == 1


def test_no_recommendations_when_the_payload_would_be_too_long():
    """Every candidate adds one modification; POST /scenarios caps the list."""
    G, a, b, _c, _h = _two_branch_network()
    full = [
        {"type": "upgrade_node", "node_id": b, "capacity": 24.0 + i}
        for i in range(settings.max_scenario_modifications)
    ]
    scenario_run = _run(apply_scenario_modifications(G, full), [a])

    assert get_recommendations(
        simulation=scenario_run, G_baseline=G, limit=5, scenario_modifications=full
    ) == []
