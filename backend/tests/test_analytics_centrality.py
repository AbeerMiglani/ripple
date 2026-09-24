"""Centrality rows are the same shape whichever engine produced them.

``calculate_centrality`` tries Neo4j GDS first and falls back to NetworkX. The
API serializes either result straight into ``CentralityScore``, so both paths
must emit every field -- name, type and provenance included -- and rank the
same topology the same way.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.network import Node
from app.services import analytics

ROW_FIELDS = {
    "node_id", "name", "display_name", "node_type", "is_synthetic",
    "data_source", "name_source", "data_quality", "score", "rank",
}


def _node(node_id, name, node_type="road_junction", **extra):
    return SimpleNamespace(id=node_id, name=name, node_type=node_type, **extra)


def _edge(src, tgt, bidirectional=False):
    return SimpleNamespace(source_id=src, target_id=tgt, is_bidirectional=bidirectional)


def _db(nodes, edges):
    """A session whose query(Node) / query(Edge) chains return the given rows."""
    def query(model):
        q = MagicMock()
        q.filter.return_value.all.return_value = nodes if model is Node else edges
        return q

    db = MagicMock()
    db.query.side_effect = query
    return db


@pytest.fixture
def star():
    # A -> HUB -> {B, C}: every shortest path between leaves runs through HUB.
    nodes = [
        _node("A", "Alpha"),
        _node("HUB", "Hub", "power_substation", display_name="Main Hub", data_quality="observed"),
        _node("B", "Bravo"),
        _node("C", "Charlie"),
    ]
    edges = [_edge("A", "HUB"), _edge("HUB", "B"), _edge("HUB", "C")]
    return _db(nodes, edges)


def test_nx_betweenness_rows_are_complete_and_ranked(star):
    rows = analytics.calculate_betweenness_nx("net", db=star)

    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert all(set(r) == ROW_FIELDS for r in rows)
    top = rows[0]
    assert top["node_id"] == "HUB"
    assert top["display_name"] == "Main Hub"
    assert top["node_type"] == "power_substation"
    # A recorded provenance value wins; a missing one falls back honestly.
    assert top["data_quality"] == "observed"
    assert rows[-1]["data_quality"] == "estimated"
    assert rows[-1]["is_synthetic"] is True


def test_nx_pagerank_uses_the_same_row_shape(star):
    rows = analytics.calculate_pagerank_nx("net", db=star)
    assert all(set(r) == ROW_FIELDS for r in rows)
    assert abs(sum(r["score"] for r in rows) - 1.0) < 1e-3


def test_bidirectional_edges_are_traversed_both_ways():
    db = _db([_node("A", "A"), _node("B", "B")], [_edge("A", "B", bidirectional=True)])
    G = analytics._centrality_graph("net", db)
    assert G.has_edge("A", "B") and G.has_edge("B", "A")


def test_ranked_row_falls_back_when_metadata_is_missing():
    row = analytics._ranked_row("0123456789abcdef", {}, 0.123456789, 7)
    assert row["name"] == "Node 01234567"
    assert row["display_name"] == row["name"]
    assert row["node_type"] == "unknown"
    assert row["score"] == 0.12346
    assert row["rank"] == 7


def test_gds_betweenness_is_normalized_and_projection_dropped(monkeypatch):
    session = MagicMock()
    calls = []

    def run(query, **params):
        calls.append(query)
        result = MagicMock()
        if "graph.project" in query:
            result.single.return_value = {"nodeCount": 4}
        elif "betweenness.stream" in query:
            result.data.return_value = [
                {"node_id": "HUB", "name": "Hub", "node_type": "power_substation", "score": 3.0},
                {"node_id": "A", "name": None, "score": 0.0},
            ]
        return result

    session.run.side_effect = run
    cm = MagicMock()
    cm.__enter__.return_value = session
    monkeypatch.setattr(analytics, "neo4j_session", lambda **_kw: cm)

    rows = analytics.calculate_betweenness_gds("net-1")

    # 3 / ((4-1)*(4-2)) = 0.5 -- the directed normalization NetworkX uses.
    assert rows[0]["score"] == 0.5
    assert rows[1]["name"] == "Node A"
    assert all(set(r) == ROW_FIELDS for r in rows)
    assert any("gds.graph.drop" in q for q in calls), "the GDS projection was leaked"
