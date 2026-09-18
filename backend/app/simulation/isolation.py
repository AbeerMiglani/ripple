"""Graph isolation for simulation runs.

Every simulation entry point already works on a copy of the network graph, but
nothing enforced it. A single ``G.nodes[x]["current_load"] += ...`` against a
shared baseline would silently degrade every subsequent scenario in the same
process, and the symptom -- a second run scoring worse than the first for no
reason -- would read as a modelling result rather than a bug.

This module makes the guarantee explicit at the call site and, outside
production, verifies it on the way out instead of trusting it.

It deliberately imports no database driver: the simulation package stays
runnable standalone, and ``app.services.graph_sync`` re-exports these names for
callers that already live on the persistence side.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


def graph_fingerprint(G: nx.DiGraph) -> tuple[Any, ...]:
    """A comparable snapshot of every simulation-relevant attribute."""
    return (
        tuple(
            (
                str(n),
                data.get("capacity"),
                data.get("current_load"),
                data.get("failure_threshold"),
                data.get("population_served"),
                data.get("status"),
                data.get("has_failed"),
            )
            for n, data in sorted(G.nodes(data=True), key=lambda item: str(item[0]))
        ),
        tuple(
            (str(u), str(v), data.get("weight"), data.get("capacity"), data.get("edge_type"))
            for u, v, data in sorted(G.edges(data=True), key=lambda e: (str(e[0]), str(e[1])))
        ),
    )


@contextmanager
def isolated_graph(G: nx.DiGraph, *, verify: bool = True) -> Iterator[nx.DiGraph]:
    """Yield a private copy of ``G`` and check the original comes back untouched.

    The copy is what the block mutates; the caller's graph is the baseline and
    must survive unchanged so the next scenario starts from clean state rather
    than from the wreckage of the last one.

    A violation is logged loudly rather than raised: the run that just finished
    is still valid (it worked on the copy). What is compromised is every *later*
    run against the same baseline, and the log names the block responsible
    instead of leaving a mystery three scenarios downstream.
    """
    before = graph_fingerprint(G) if verify else None
    working = G.copy()
    try:
        yield working
    finally:
        if before is not None and graph_fingerprint(G) != before:
            logger.error(
                "graph isolation violated: the baseline graph was mutated inside a "
                "simulation block; subsequent runs would evaluate a degraded "
                "baseline rather than a clean one."
            )
