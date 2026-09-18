"""Baseline graphs must survive a simulation unchanged.

A run that mutates the shared baseline makes the *next* scenario evaluate a
degraded network. The symptom -- a second run scoring worse than the first for
no reason -- reads as a modelling result rather than a bug, which is exactly
what makes it worth pinning.
"""

from __future__ import annotations

import networkx as nx

from app.simulation.cascade import run_cascade
from app.simulation.isolation import graph_fingerprint, isolated_graph


def _network():
    G = nx.DiGraph()
    for i in range(6):
        G.add_node(
            str(i),
            node_type="power_substation",
            current_load=10.0,
            capacity=15.0,
            failure_threshold=1.0,
            population_served=100,
        )
        if i:
            G.add_edge(str(i - 1), str(i), capacity=100.0, edge_type="power_supply")
    return G


def test_isolated_graph_yields_a_working_copy():
    G = _network()
    with isolated_graph(G) as working:
        working.nodes["0"]["current_load"] = 9_999.0
        working.remove_node("1")

    assert G.nodes["0"]["current_load"] == 10.0
    assert "1" in G


def test_run_cascade_leaves_the_baseline_untouched():
    G = _network()
    before = graph_fingerprint(G)

    run_cascade(G, ["0"])

    assert graph_fingerprint(G) == before


def test_sequential_runs_against_one_baseline_are_identical():
    """Session bleed would make the second run differ from the first."""
    G = _network()

    first = run_cascade(G, ["0"])
    second = run_cascade(G, ["0"])

    assert [w["failed_node_ids"] for w in first[0]] == [w["failed_node_ids"] for w in second[0]]
    assert first[1:] == second[1:]


def test_a_scenario_run_does_not_contaminate_the_next_one():
    """Interleave a heavy run between two identical light runs."""
    G = _network()

    clean = run_cascade(G, ["0"])
    run_cascade(G, ["0", "2", "4"])
    again = run_cascade(G, ["0"])

    assert [w["failed_node_ids"] for w in clean[0]] == [w["failed_node_ids"] for w in again[0]]


def test_fingerprint_notices_a_mutated_attribute():
    """Guard the guard: a no-op fingerprint would make these tests vacuous."""
    G = _network()
    before = graph_fingerprint(G)
    G.nodes["3"]["current_load"] = 11.0

    assert graph_fingerprint(G) != before
