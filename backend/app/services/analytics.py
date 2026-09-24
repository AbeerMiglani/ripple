"""
Analytics service using Neo4j Graph Data Science (GDS) with NetworkX Brandes fallback.
Implements Betweenness Centrality as primary criticality metric and PageRank as secondary.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal

import networkx as nx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.neo4j import neo4j_session
from app.db.postgres import SessionLocal
from app.db.redis import get_redis_client
from app.models.network import Edge, Node

logger = logging.getLogger(__name__)


#: Provenance fields carried on every ranked row, with the value to report when
#: a node does not record one. The shipped dataset is synthetic, so the
#: fallbacks describe it honestly rather than claiming observed data.
_PROVENANCE_DEFAULTS: dict[str, Any] = {
    "is_synthetic": True,
    "data_source": "synthetic",
    "name_source": "synthetic",
    "data_quality": "estimated",
}

#: Projects one network's assets for a GDS algorithm. Every relationship type
#: counts: centrality is structural, not specific to any one service.
_GDS_NODE_QUERY = "MATCH (n:Asset {network_id: $network_id}) RETURN id(n) AS id"
_GDS_REL_QUERY = """
MATCH (s:Asset {network_id: $network_id})-[r]->(t:Asset {network_id: $network_id})
RETURN id(s) AS source, id(t) AS target
"""

#: Streams an algorithm's scores with the metadata a ranked row needs. The
#: algorithm name is interpolated from a fixed allowlist, never from input.
_GDS_STREAM_QUERY = """
CALL gds.{algorithm}.stream($graph_name)
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
RETURN n.id AS node_id,
       n.name AS name,
       n.display_name AS display_name,
       n.type AS node_type,
       n.is_synthetic AS is_synthetic,
       n.data_source AS data_source,
       n.name_source AS name_source,
       n.data_quality AS data_quality,
       score
ORDER BY score DESC
"""

_GDS_ALGORITHMS = {"betweenness": "betweenness", "pagerank": "pageRank"}


def _ranked_row(node_id: str, attrs: dict[str, Any], score: float, rank: int) -> dict[str, Any]:
    """One centrality result row, the same shape whichever engine scored it."""
    name = attrs.get("name") or f"Node {str(node_id)[:8]}"
    row: dict[str, Any] = {
        "node_id": node_id,
        "name": name,
        "display_name": attrs.get("display_name") or name,
        "node_type": attrs.get("node_type") or "unknown",
    }
    for field, default in _PROVENANCE_DEFAULTS.items():
        value = attrs.get(field)
        row[field] = default if value is None else value
    row["score"] = round(float(score), 5)
    row["rank"] = rank
    return row


def _run_gds(network_id: str, metric: Literal["betweenness", "pagerank"]) -> list[dict[str, Any]]:
    """Project the network into GDS, stream one algorithm, and drop the projection.

    Betweenness is returned by GDS unnormalized; it is divided by (N-1)(N-2),
    the directed-graph normalization NetworkX applies, so the two engines
    report comparable scores.
    """
    graph_name = f"network_{metric}_{network_id.replace('-', '_')}_{uuid.uuid4().hex}"
    graph_created = False

    with neo4j_session(write=True) as session:
        try:
            projection = session.run(
                """
                CALL gds.graph.project.cypher(
                    $graph_name,
                    $node_query,
                    $rel_query,
                    {parameters: {network_id: $network_id}}
                ) YIELD graphName, nodeCount, relationshipCount
                """,
                graph_name=graph_name,
                node_query=_GDS_NODE_QUERY,
                rel_query=_GDS_REL_QUERY,
                network_id=network_id,
            ).single()
            graph_created = True

            node_count = projection["nodeCount"] if projection else 0
            norm_factor = 1.0
            if metric == "betweenness" and node_count > 2:
                norm_factor = 1.0 / ((node_count - 1) * (node_count - 2))

            rows = session.run(
                _GDS_STREAM_QUERY.format(algorithm=_GDS_ALGORITHMS[metric]),
                graph_name=graph_name,
            ).data()

            ranked = []
            for idx, row in enumerate(rows):
                score = float(row["score"]) * norm_factor
                if metric == "betweenness":
                    score = min(1.0, max(0.0, score))
                ranked.append(_ranked_row(row["node_id"], row, score, idx + 1))
            return ranked
        finally:
            if graph_created:
                try:
                    session.run("CALL gds.graph.drop($graph_name)", graph_name=graph_name).consume()
                except Exception:
                    logger.exception("failed to drop GDS projection %s", graph_name)


def _centrality_graph(network_id: str, db: Session | None) -> nx.DiGraph:
    """The structural graph centrality is computed over, with row metadata.

    Unweighted and untyped on purpose -- the same topology the GDS projection
    sees -- so the NetworkX fallback ranks what Neo4j would have ranked.
    """
    should_close = db is None
    session = db if db is not None else SessionLocal()
    try:
        nodes = session.query(Node).filter(Node.network_id == network_id).all()
        edges = session.query(Edge).filter(Edge.network_id == network_id).all()
    finally:
        if should_close:
            session.close()

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(
            str(n.id),
            name=n.name,
            display_name=getattr(n, "display_name", None) or n.name,
            node_type=n.node_type,
            **{field: getattr(n, field, None) for field in _PROVENANCE_DEFAULTS},
        )
    for e in edges:
        G.add_edge(str(e.source_id), str(e.target_id))
        if e.is_bidirectional:
            G.add_edge(str(e.target_id), str(e.source_id))
    return G


def _run_nx(
    network_id: str,
    metric: Literal["betweenness", "pagerank"],
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """In-process NetworkX fallback (Brandes betweenness, or PageRank)."""
    G = _centrality_graph(network_id, db)
    if metric == "betweenness":
        scores = nx.betweenness_centrality(G, weight=None, normalized=True)
    else:
        scores = nx.pagerank(G)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        _ranked_row(node_id, G.nodes[node_id], score, idx + 1)
        for idx, (node_id, score) in enumerate(ranked)
    ]


def calculate_betweenness_gds(network_id: str) -> list[dict[str, Any]]:
    """Betweenness centrality via Neo4j GDS, normalized by (N-1)(N-2)."""
    return _run_gds(network_id, "betweenness")


def calculate_betweenness_nx(network_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    """NetworkX Brandes fallback for betweenness centrality."""
    return _run_nx(network_id, "betweenness", db)


def calculate_pagerank_gds(network_id: str) -> list[dict[str, Any]]:
    """PageRank via Neo4j GDS."""
    return _run_gds(network_id, "pagerank")


def calculate_pagerank_nx(network_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    """NetworkX PageRank fallback."""
    return _run_nx(network_id, "pagerank", db)


def calculate_centrality(
    network_id: str,
    metric: Literal["betweenness", "pagerank"] = "betweenness",
    db: Session | None = None,
) -> list[dict[str, Any]]:
    """
    Calculates centrality scores for nodes in a network.
    Default metric is Betweenness Centrality (primary), keeping PageRank as secondary.
    Uses Neo4j GDS with pure-Python NetworkX Brandes fallback.
    """
    cache_key = f"centrality:{network_id}:{metric}"
    if settings.centrality_cache_ttl_seconds:
        try:
            cached = get_redis_client().get(cache_key)
            if cached:
                return json.loads(str(cached))
        except Exception:
            logger.warning("centrality cache read failed", exc_info=True)

    ranked_results: list[dict[str, Any]] = []

    if metric == "betweenness":
        try:
            ranked_results = calculate_betweenness_gds(network_id)
        except Exception:
            logger.warning("Neo4j GDS betweenness failed; falling back to NetworkX", exc_info=True)
            ranked_results = calculate_betweenness_nx(network_id, db=db)
    elif metric == "pagerank":
        try:
            ranked_results = calculate_pagerank_gds(network_id)
        except Exception:
            logger.warning("Neo4j GDS pagerank failed; falling back to NetworkX", exc_info=True)
            ranked_results = calculate_pagerank_nx(network_id, db=db)
    else:
        raise ValueError(f"Unsupported centrality metric: {metric}")

    if settings.centrality_cache_ttl_seconds and ranked_results:
        try:
            get_redis_client().setex(
                cache_key,
                settings.centrality_cache_ttl_seconds,
                json.dumps(ranked_results),
            )
        except Exception:
            logger.warning("centrality cache write failed", exc_info=True)

    return ranked_results


def summarize_cascade_waves(waves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-wave cascade metrics, derived strictly from marginal deltas.

    Failure velocity is "how many assets went down in this wave", not "how many
    were down by the end of it". Consumers that re-accumulated the wave lists
    themselves kept conflating the two, which inflates the velocity curve into a
    monotonically rising line that can never fall -- so a cascade that is
    visibly slowing still reads as accelerating.

    ``marginal_failed_node_ids`` and ``cumulative_failed_node_ids`` are published
    by the engine, but results persisted before those keys existed carry only
    ``failed_node_ids``. Those are marginal too, so the cumulative set is rebuilt
    by accumulation here; nodes already seen in an earlier wave are subtracted,
    so a legacy record that did store cumulative lists still yields correct
    marginal counts rather than double counting.
    """
    summary: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_marginal = 0

    for index, wave in enumerate(waves):
        raw_marginal = wave.get("marginal_failed_node_ids")
        if raw_marginal is None:
            raw_marginal = wave.get("failed_node_ids", [])
        marginal = {str(nid) for nid in raw_marginal} - seen
        seen |= marginal

        declared_cumulative = wave.get("cumulative_failed_node_ids")
        cumulative_count = (
            len({str(nid) for nid in declared_cumulative})
            if declared_cumulative is not None
            else len(seen)
        )

        marginal_count = len(marginal)
        summary.append({
            "wave": wave.get("wave", index),
            "marginal_count": marginal_count,
            "cumulative_count": cumulative_count,
            # Change in the rate of new failures: positive while the cascade is
            # accelerating, negative once it starts to burn out.
            "failure_velocity": marginal_count - previous_marginal,
        })
        previous_marginal = marginal_count

    return summary
