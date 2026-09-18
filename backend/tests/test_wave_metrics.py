"""Marginal vs cumulative wave accounting.

The engine publishes both sets explicitly; the metric layer must read the
marginal one. Conflating them turns failure velocity into a monotonically
rising curve that can never fall, so a cascade that is visibly burning out
still reads as accelerating.
"""

from __future__ import annotations

import networkx as nx

from app.services.analytics import summarize_cascade_waves
from app.simulation.cascade import run_cascade


def _chain(length: int) -> nx.DiGraph:
    G = nx.DiGraph()
    for i in range(length):
        G.add_node(str(i), current_load=10.0, capacity=15.0, failure_threshold=1.0)
        if i > 0:
            G.add_edge(str(i - 1), str(i))
    return G


def test_engine_publishes_marginal_and_cumulative_separately():
    waves, *_ = run_cascade(_chain(4), ["0"])

    assert [w["marginal_failed_node_ids"] for w in waves] == [["0"], ["1"], ["2"], ["3"]]
    assert [w["cumulative_failed_node_ids"] for w in waves] == [
        ["0"],
        ["0", "1"],
        ["0", "1", "2"],
        ["0", "1", "2", "3"],
    ]


def test_legacy_failed_node_ids_key_remains_the_marginal_set():
    """Persisted results and the API schema both read this key."""
    waves, *_ = run_cascade(_chain(3), ["0"])
    for wave in waves:
        assert wave["failed_node_ids"] == wave["marginal_failed_node_ids"]


def test_summary_counts_marginal_and_cumulative_distinctly():
    waves, *_ = run_cascade(_chain(3), ["0"])
    summary = summarize_cascade_waves(waves)

    assert [s["marginal_count"] for s in summary] == [1, 1, 1]
    assert [s["cumulative_count"] for s in summary] == [1, 2, 3]


def test_failure_velocity_falls_when_the_cascade_decelerates():
    """A fan-out then a narrowing: velocity must go up, then come back down.

    Summing cumulative sets instead would give 1, 4, 5 -- never decreasing --
    and report a dying cascade as an accelerating one.
    """
    waves = [
        {"wave": 0, "failed_node_ids": ["a"]},
        {"wave": 1, "failed_node_ids": ["b", "c", "d"]},
        {"wave": 2, "failed_node_ids": ["e"]},
    ]
    summary = summarize_cascade_waves(waves)

    assert [s["marginal_count"] for s in summary] == [1, 3, 1]
    assert [s["cumulative_count"] for s in summary] == [1, 4, 5]
    assert [s["failure_velocity"] for s in summary] == [1, 2, -2]


def test_legacy_records_without_the_new_keys_are_accumulated():
    summary = summarize_cascade_waves(
        [{"wave": 0, "failed_node_ids": ["a"]}, {"wave": 1, "failed_node_ids": ["b", "c"]}]
    )
    assert [s["cumulative_count"] for s in summary] == [1, 3]


def test_a_legacy_record_storing_cumulative_lists_does_not_double_count():
    """Defensive: subtracting nodes already seen keeps marginal counts honest."""
    summary = summarize_cascade_waves(
        [{"wave": 0, "failed_node_ids": ["a"]}, {"wave": 1, "failed_node_ids": ["a", "b"]}]
    )
    assert [s["marginal_count"] for s in summary] == [1, 1]
