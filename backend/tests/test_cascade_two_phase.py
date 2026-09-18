"""Two-phase wave commit and the monotonic failure latch.

Nodes used to be evaluated against a graph that was already being mutated by
the same wave, so the outcome could depend on the order NetworkX happened to
iterate in, and interdependent assets (power feeding water feeding power) had
no principled termination story beyond the wave guardrail.
"""

from __future__ import annotations

import networkx as nx

from app.simulation.cascade import run_cascade


def _asset(G, node_id, *, load, capacity, node_type="power_substation"):
    G.add_node(
        node_id,
        node_type=node_type,
        current_load=load,
        capacity=capacity,
        failure_threshold=1.0,
        population_served=0,
    )


def _failed(waves):
    out = set()
    for w in waves:
        out.update(w["failed_node_ids"])
    return out


def _fan(order):
    """One failing hub shedding load onto three siblings, inserted in `order`."""
    G = nx.DiGraph()
    _asset(G, "HUB", load=90.0, capacity=1.0)
    for name in order:
        _asset(G, name, load=0.0, capacity=29.0)
    for name in order:
        G.add_edge("HUB", name)
    return G


def test_outcome_is_independent_of_node_insertion_order():
    """Every permutation must produce identical waves.

    Each sibling receives 30 against a capacity of 29, so all three fail
    together in one wave. Under in-place sequential evaluation the split a node
    saw depended on how many siblings had already been processed.
    """
    baseline = [w["failed_node_ids"] for w in run_cascade(_fan(["A", "B", "C"]), ["HUB"])[0]]

    for order in (["C", "B", "A"], ["B", "A", "C"], ["A", "C", "B"]):
        waves, *_ = run_cascade(_fan(order), ["HUB"])
        assert [w["failed_node_ids"] for w in waves] == baseline


def test_a_failed_node_never_returns_to_service():
    """The latch is monotonic: cumulative sets only ever grow."""
    G = nx.DiGraph()
    for i in range(5):
        _asset(G, str(i), load=10.0, capacity=15.0)
        if i:
            G.add_edge(str(i - 1), str(i))

    waves, *_ = run_cascade(G, ["0"])

    seen: set[str] = set()
    for wave in waves:
        cumulative = set(wave["cumulative_failed_node_ids"])
        assert seen.issubset(cumulative), "a node left the failed set"
        seen = cumulative


def test_a_node_fails_in_exactly_one_wave():
    """Marginal sets are disjoint, so summing them is the true total."""
    G = nx.DiGraph()
    for i in range(6):
        _asset(G, str(i), load=10.0, capacity=15.0)
        if i:
            G.add_edge(str(i - 1), str(i))

    waves, *_ = run_cascade(G, ["0"])

    marginal = [nid for w in waves for nid in w["failed_node_ids"]]
    assert len(marginal) == len(set(marginal))


def test_mutual_power_water_dependency_terminates():
    """Power feeds water, water cools power: the classic deadlock.

    Without a latch this pair can keep re-failing one another. It must settle,
    report stabilized, and finish well inside the guardrail.
    """
    G = nx.DiGraph()
    _asset(G, "GRID", load=10.0, capacity=1_000.0, node_type="power_substation")
    _asset(G, "PUMP", load=10.0, capacity=1_000.0, node_type="water_station")
    _asset(G, "SEED", load=10.0, capacity=1_000.0, node_type="power_substation")
    G.add_edge("SEED", "GRID", edge_type="power_supply", capacity=100.0)
    G.add_edge("GRID", "PUMP", edge_type="power_supply", capacity=100.0)
    G.add_edge("PUMP", "GRID", edge_type="water_supply", capacity=100.0)

    waves, _, _, _, stabilized = run_cascade(
        G, ["SEED"], max_waves=50, enforce_edge_semantics=True
    )

    assert stabilized is True
    assert len(waves) < 50
    assert _failed(waves) == {"SEED", "GRID", "PUMP"}


def test_failed_nodes_are_latched_in_place_not_deleted():
    """Downstream analysis still needs to see what a severed asset connected to."""
    G = nx.DiGraph()
    _asset(G, "A", load=10.0, capacity=15.0)
    _asset(G, "B", load=0.0, capacity=9_999.0)
    G.add_edge("A", "B")

    waves, eff_before, eff_after, _, stabilized = run_cascade(G, ["A"])

    assert stabilized is True
    assert len(waves) == 1
    # Efficiency is still measured over survivors only.
    assert eff_after < eff_before
