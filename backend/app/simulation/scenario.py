"""Apply a scenario's structural modifications to an in-memory graph.

Shared by the Celery runner, which applies a scenario before running its
cascade, and by the recommendation engine, which must score candidates against
the same modified topology a scenario run actually used. Like the rest of
``app.simulation`` it imports no database driver, so both callers can use it
without pulling in the other's dependencies.
"""

from __future__ import annotations

from typing import Any

import networkx as nx


def apply_scenario_modifications(
    G: nx.DiGraph,
    modifications: list[dict[str, Any]],
) -> nx.DiGraph:
    """Return a copy of ``G`` with every ``add_edge`` / ``upgrade_node`` applied.

    The input graph is never mutated. Raises ``ValueError`` for a modification
    that references a node outside the graph or has an unknown type.
    """
    G_mod = G.copy()
    for mod in modifications:
        mod_type = mod.get("type")
        if mod_type == "add_edge":
            src = str(mod["source"])
            tgt = str(mod["target"])
            if src not in G_mod or tgt not in G_mod or src == tgt:
                raise ValueError("scenario references invalid graph endpoints")
            weight = float(mod.get("weight", 1.0))
            capacity = float(mod.get("capacity", 100.0))
            edge_type = mod.get("edge_type", "power_supply")
            is_bi = bool(mod.get("is_bidirectional", False))

            G_mod.add_edge(src, tgt, weight=weight, capacity=capacity, edge_type=edge_type)
            if is_bi:
                G_mod.add_edge(tgt, src, weight=weight, capacity=capacity, edge_type=edge_type)

        elif mod_type == "upgrade_node":
            nid = str(mod["node_id"])
            if nid not in G_mod:
                raise ValueError(f"scenario upgrade references unknown node {nid}")

            if mod.get("capacity") is not None:
                G_mod.nodes[nid]["capacity"] = float(mod["capacity"])
            elif mod.get("capacity_multiplier") is not None:
                G_mod.nodes[nid]["capacity"] *= float(mod["capacity_multiplier"])
            elif mod.get("capacity_add") is not None:
                G_mod.nodes[nid]["capacity"] += float(mod["capacity_add"])

            if mod.get("failure_threshold") is not None:
                G_mod.nodes[nid]["failure_threshold"] = float(mod["failure_threshold"])
            elif mod.get("failure_threshold_add") is not None:
                G_mod.nodes[nid]["failure_threshold"] += float(mod["failure_threshold_add"])

        else:
            raise ValueError(f"unsupported scenario modification type: {mod_type}")

    return G_mod
