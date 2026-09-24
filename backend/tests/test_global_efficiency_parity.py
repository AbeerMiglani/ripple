"""Parity test: the Rust extension and the pure-Python fallback must agree.

`calculate_global_efficiency` dispatches to `ripple_graph_rs` when it is
built, else stays on `_calculate_global_efficiency_py`. Each path in
isolation only proves it runs -- this proves both compute the *same*
algorithm, which is the actual correctness guarantee the dispatch relies on.

Skipped when `ripple_graph_rs` has not been built (`maturin develop` /
`maturin build` + `pip install`), since without it there is no second
implementation to compare against.
"""

from __future__ import annotations

import random

import networkx as nx
import pytest

from app.simulation import cascade

pytestmark = pytest.mark.skipif(
    cascade._rust_global_efficiency is None,
    reason="ripple_graph_rs not built; nothing to compare the Python fallback against",
)


def _random_digraph(num_nodes: int, edge_probability: float, seed: int) -> nx.DiGraph:
    rng = random.Random(seed)
    G = nx.DiGraph()
    G.add_nodes_from(range(num_nodes))
    for u in range(num_nodes):
        for v in range(num_nodes):
            if u != v and rng.random() < edge_probability:
                G.add_edge(u, v)
    return G


@pytest.mark.parametrize("seed", range(20))
def test_rust_and_python_agree_on_random_graphs(seed):
    G = _random_digraph(num_nodes=12, edge_probability=0.25, seed=seed)

    expected = cascade._calculate_global_efficiency_py(G)
    actual = cascade.calculate_global_efficiency(G)

    assert actual == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("seed", range(10))
def test_rust_and_python_agree_with_explicit_n_baseline(seed):
    """N_baseline != len(G): the survivors-only scoring run_cascade actually uses."""
    G = _random_digraph(num_nodes=10, edge_probability=0.3, seed=seed)
    # Drop a few nodes to emulate scoring a surviving subgraph against the
    # pre-cascade node count.
    survivors = G.subgraph([n for n in G.nodes if n % 3 != 0])

    expected = cascade._calculate_global_efficiency_py(survivors, N_baseline=len(G))
    actual = cascade.calculate_global_efficiency(survivors, N_baseline=len(G))

    assert actual == pytest.approx(expected, abs=1e-9)


def test_rust_and_python_agree_on_string_node_labels():
    """run_cascade's real graphs use string node ids, not integers."""
    G = nx.DiGraph()
    G.add_edge("substation-a", "substation-b")
    G.add_edge("substation-b", "hospital-1")
    G.add_edge("hospital-1", "substation-a")

    expected = cascade._calculate_global_efficiency_py(G)
    actual = cascade.calculate_global_efficiency(G)

    assert actual == pytest.approx(expected, abs=1e-9)


def test_disconnected_and_empty_graphs_agree():
    G = nx.DiGraph()
    G.add_nodes_from(["A", "B", "C"])

    assert (
        cascade.calculate_global_efficiency(G)
        == cascade._calculate_global_efficiency_py(G)
        == 0.0
    )


def test_out_of_range_edge_raises_value_error_not_a_panic():
    """A bad index used to panic inside Rust, surfacing as PanicException --
    a BaseException subclass that ordinary `except Exception` handlers miss."""
    with pytest.raises(ValueError, match="outside 0..2"):
        cascade._rust_global_efficiency(2, [(0, 1), (1, 2)], None)


def test_computation_releases_the_gil():
    """Another Python thread must make progress while a large graph is scored.

    Holding the GIL for the whole rayon computation froze every other thread in
    the process, the API's event loop included. The counter thread records a
    timestamp per iteration; at least one must fall strictly inside the call.
    The edges of the window are excluded, since a GIL handoff just before or
    after the call can land either way whether or not the GIL is released.
    """
    import bisect
    import threading
    import time

    rng = random.Random(7)
    n = 4000
    edges = [(i, (i + 1) % n) for i in range(n)]
    edges += [(rng.randrange(n), rng.randrange(n)) for _ in range(7 * n)]

    stamps: list[float] = []
    done = threading.Event()

    def tick():
        while not done.is_set():
            stamps.append(time.perf_counter())

    counter = threading.Thread(target=tick)
    counter.start()
    try:
        start = time.perf_counter()
        cascade._rust_global_efficiency(n, edges, None)
        end = time.perf_counter()
    finally:
        done.set()
        counter.join()

    margin = min(0.01, (end - start) / 4)
    lo, hi = start + margin, end - margin
    inside = bisect.bisect_left(stamps, hi) - bisect.bisect_right(stamps, lo)
    assert inside > 0, "no other Python thread ran while the extension computed"
