"""One canonical database -> NetworkX graph builder.

There used to be three of these, each setting a different subset of node
attributes: the Celery runner's inline builder omitted ``node_type`` entirely,
the recommendation engine's set it, and the analytics fallbacks set it but no
capacities. That divergence is not cosmetic -- domain-aware cascade propagation
keys off ``node_type``, so on the runner's graph it could never have fired.

Two further details this builder gets right that the inline versions did not:

* ``edge_types`` (plural). A ``DiGraph`` holds one edge per ordered pair, but
  the database's uniqueness key is ``(network, source, target, edge_type)`` --
  it deliberately permits a power link *and* a water link between the same two
  assets. The inline builders let the last row win, silently discarding one of
  them. Here parallel typed links are merged into a set.
* Bidirectional edges get the same treatment in both directions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx

from app.models.network import Edge, Node


def _add_typed_edge(G: nx.DiGraph, src: str, tgt: str, edge: Edge) -> None:
    existing = G.get_edge_data(src, tgt)
    edge_type = edge.edge_type
    if existing is None:
        G.add_edge(
            src,
            tgt,
            weight=float(edge.weight),
            capacity=float(edge.capacity),
            edge_type=edge_type,
            edge_types={edge_type},
        )
        return

    # A second typed link between the same pair: keep both types, and let the
    # link carry their combined capacity rather than whichever row arrived last.
    types = set(existing.get("edge_types") or {existing.get("edge_type")})
    types.discard(None)
    types.add(edge_type)
    existing["edge_types"] = types
    existing["capacity"] = float(existing.get("capacity", 0.0)) + float(edge.capacity)


def build_graph(nodes: Iterable[Node], edges: Iterable[Edge]) -> nx.DiGraph:
    """Build the in-memory simulation graph from ORM rows."""
    G: nx.DiGraph = nx.DiGraph()
    for n in nodes:
        attrs: dict[str, Any] = {
            "name": n.name,
            "display_name": getattr(n, "display_name", None) or n.name,
            "node_type": n.node_type,
            "capacity": float(n.capacity),
            "current_load": float(n.current_load),
            "failure_threshold": float(n.failure_threshold),
            "population_served": int(n.population_served),
            "status": n.status,
            # Geometry, so population impact can resolve overlapping service
            # areas instead of summing them.
            "lat": getattr(n, "lat", None),
            "lng": getattr(n, "lng", None),
            "service_radius_m": getattr(n, "service_radius_m", None),
        }
        G.add_node(str(n.id), **attrs)

    for e in edges:
        src, tgt = str(e.source_id), str(e.target_id)
        _add_typed_edge(G, src, tgt, e)
        if e.is_bidirectional:
            _add_typed_edge(G, tgt, src, e)

    return G
