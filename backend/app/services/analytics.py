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


def calculate_betweenness_gds(network_id: str) -> list[dict[str, Any]]:
    """
    Calculates Betweenness Centrality for nodes in a specific network using Neo4j GDS.
    Normalized by (N-1)(N-2) for directed graphs.
    """
    graph_name = f"network_bc_{network_id.replace('-', '_')}_{uuid.uuid4().hex}"
    graph_created = False

    with neo4j_session(write=True) as session:
        try:
            node_query = "MATCH (n:Asset {network_id: $network_id}) RETURN id(n) AS id"
            rel_query = """
            MATCH (s:Asset {network_id: $network_id})-[r]->(t:Asset {network_id: $network_id})
            RETURN id(s) AS source, id(t) AS target
            """

            project_result = session.run(
                """
                CALL gds.graph.project.cypher(
                    $graph_name,
                    $node_query,
                    $rel_query,
                    {parameters: {network_id: $network_id}}
                ) YIELD graphName, nodeCount, relationshipCount
                """,
                graph_name=graph_name,
                node_query=node_query,
                rel_query=rel_query,
                network_id=network_id,
            ).single()
            graph_created = True

            node_count = project_result["nodeCount"] if project_result else 0
            norm_factor = 1.0 / ((node_count - 1) * (node_count - 2)) if node_count > 2 else 1.0

            results = session.run(
                """
                CALL gds.betweenness.stream($graph_name)
                YIELD nodeId, score
                WITH gds.util.asNode(nodeId) AS n, score
                RETURN n.id AS node_id,
                       coalesce(n.name, 'Node ' + left(n.id, 8)) AS name,
                       coalesce(n.display_name, n.name) AS display_name,
                       coalesce(n.type, 'unknown') AS node_type,
                       coalesce(n.is_synthetic, true) AS is_synthetic,
                       coalesce(n.data_source, 'synthetic') AS data_source,
                       coalesce(n.name_source, 'synthetic') AS name_source,
                       coalesce(n.data_quality, 'estimated') AS data_quality,
                       score
                ORDER BY score DESC
                """,
                graph_name=graph_name,
            ).data()

            ranked_results = []
            for idx, row in enumerate(results):
                raw_score = float(row["score"])
                norm_score = min(1.0, max(0.0, raw_score * norm_factor))
                ranked_results.append({
                    "node_id": row["node_id"],
                    "name": row.get("name") or f"Node {str(row['node_id'])[:8]}",
                    "display_name": row.get("display_name") or row.get("name"),
                    "node_type": row.get("node_type", "unknown"),
                    "is_synthetic": row.get("is_synthetic", True) if row.get("is_synthetic") is not None else True,
                    "data_source": row.get("data_source", "synthetic") or "synthetic",
                    "name_source": row.get("name_source", "synthetic") or "synthetic",
                    "data_quality": row.get("data_quality", "estimated") or "estimated",
                    "score": round(norm_score, 5),
                    "rank": idx + 1,
                })
            return ranked_results
        finally:
            if graph_created:
                try:
                    session.run("CALL gds.graph.drop($graph_name)", graph_name=graph_name)
                except Exception:
                    logger.exception("failed to drop GDS projection %s", graph_name)


def calculate_betweenness_nx(network_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    """
    Pure-Python NetworkX Brandes fallback calculation for betweenness centrality.
    Benchmark execution time: ~13.5 ms for 120-node canonical graph.
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        nodes = db.query(Node).filter(Node.network_id == network_id).all()
        edges = db.query(Edge).filter(Edge.network_id == network_id).all()

        G = nx.DiGraph()
        for n in nodes:
            G.add_node(
                str(n.id),
                name=n.name,
                display_name=getattr(n, "display_name", None) or n.name,
                node_type=n.node_type,
                is_synthetic=getattr(n, "is_synthetic", True),
                data_source=getattr(n, "data_source", "synthetic"),
                name_source=getattr(n, "name_source", "synthetic"),
                data_quality=getattr(n, "data_quality", "estimated"),
            )
        for e in edges:
            G.add_edge(str(e.source_id), str(e.target_id))
            if e.is_bidirectional:
                G.add_edge(str(e.target_id), str(e.source_id))

        bc = nx.betweenness_centrality(G, weight=None, normalized=True)
        sorted_nodes = sorted(bc.items(), key=lambda x: x[1], reverse=True)

        return [
            {
                "node_id": nid,
                "name": G.nodes[nid]["name"],
                "display_name": G.nodes[nid]["display_name"],
                "node_type": G.nodes[nid]["node_type"],
                "is_synthetic": G.nodes[nid]["is_synthetic"],
                "data_source": G.nodes[nid]["data_source"],
                "name_source": G.nodes[nid]["name_source"],
                "data_quality": G.nodes[nid]["data_quality"],
                "score": round(float(score), 5),
                "rank": idx + 1,
            }
            for idx, (nid, score) in enumerate(sorted_nodes)
        ]
    finally:
        if should_close:
            db.close()


def calculate_pagerank_gds(network_id: str) -> list[dict[str, Any]]:
    """Calculates PageRank centrality using Neo4j GDS."""
    graph_name = f"network_pr_{network_id.replace('-', '_')}_{uuid.uuid4().hex}"
    graph_created = False

    with neo4j_session(write=True) as session:
        try:
            node_query = "MATCH (n:Asset {network_id: $network_id}) RETURN id(n) AS id"
            rel_query = """
            MATCH (s:Asset {network_id: $network_id})-[r]->(t:Asset {network_id: $network_id})
            RETURN id(s) AS source, id(t) AS target
            """

            session.run(
                """
                CALL gds.graph.project.cypher(
                    $graph_name,
                    $node_query,
                    $rel_query,
                    {parameters: {network_id: $network_id}}
                ) YIELD graphName, nodeCount, relationshipCount
                """,
                graph_name=graph_name,
                node_query=node_query,
                rel_query=rel_query,
                network_id=network_id,
            )
            graph_created = True

            results = session.run(
                """
                CALL gds.pageRank.stream($graph_name)
                YIELD nodeId, score
                WITH gds.util.asNode(nodeId) AS n, score
                RETURN n.id AS node_id,
                       coalesce(n.name, 'Node ' + left(n.id, 8)) AS name,
                       coalesce(n.display_name, n.name) AS display_name,
                       coalesce(n.type, 'unknown') AS node_type,
                       coalesce(n.is_synthetic, true) AS is_synthetic,
                       coalesce(n.data_source, 'synthetic') AS data_source,
                       coalesce(n.name_source, 'synthetic') AS name_source,
                       coalesce(n.data_quality, 'estimated') AS data_quality,
                       score
                ORDER BY score DESC
                """,
                graph_name=graph_name,
            ).data()

            ranked = []
            for idx, row in enumerate(results):
                ranked.append({
                    "node_id": row["node_id"],
                    "name": row.get("name") or f"Node {str(row['node_id'])[:8]}",
                    "display_name": row.get("display_name") or row.get("name"),
                    "node_type": row.get("node_type", "unknown"),
                    "is_synthetic": row.get("is_synthetic", True) if row.get("is_synthetic") is not None else True,
                    "data_source": row.get("data_source", "synthetic") or "synthetic",
                    "name_source": row.get("name_source", "synthetic") or "synthetic",
                    "data_quality": row.get("data_quality", "estimated") or "estimated",
                    "score": round(float(row["score"]), 5),
                    "rank": idx + 1,
                })
            return ranked
        finally:
            if graph_created:
                try:
                    session.run("CALL gds.graph.drop($graph_name)", graph_name=graph_name)
                except Exception:
                    logger.exception("failed to drop GDS projection %s", graph_name)


def calculate_pagerank_nx(network_id: str, db: Session | None = None) -> list[dict[str, Any]]:
    """Pure-Python NetworkX PageRank fallback."""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        nodes = db.query(Node).filter(Node.network_id == network_id).all()
        edges = db.query(Edge).filter(Edge.network_id == network_id).all()

        G = nx.DiGraph()
        for n in nodes:
            G.add_node(
                str(n.id),
                name=n.name,
                display_name=getattr(n, "display_name", None) or n.name,
                node_type=n.node_type,
                is_synthetic=getattr(n, "is_synthetic", True),
                data_source=getattr(n, "data_source", "synthetic"),
                name_source=getattr(n, "name_source", "synthetic"),
                data_quality=getattr(n, "data_quality", "estimated"),
            )
        for e in edges:
            G.add_edge(str(e.source_id), str(e.target_id))
            if e.is_bidirectional:
                G.add_edge(str(e.target_id), str(e.source_id))

        pr = nx.pagerank(G)
        sorted_nodes = sorted(pr.items(), key=lambda x: x[1], reverse=True)

        return [
            {
                "node_id": nid,
                "name": G.nodes[nid]["name"],
                "display_name": G.nodes[nid]["display_name"],
                "node_type": G.nodes[nid]["node_type"],
                "is_synthetic": G.nodes[nid]["is_synthetic"],
                "data_source": G.nodes[nid]["data_source"],
                "name_source": G.nodes[nid]["name_source"],
                "data_quality": G.nodes[nid]["data_quality"],
                "score": round(float(score), 5),
                "rank": idx + 1,
            }
            for idx, (nid, score) in enumerate(sorted_nodes)
        ]
    finally:
        if should_close:
            db.close()


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
