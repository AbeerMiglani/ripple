"""
PostGIS → Neo4j Sync Service.

This service is responsible for keeping the Neo4j graph database in sync with
the PostgreSQL/PostGIS ground truth. It performs a one-way push:
PostGIS (canonical data) → Neo4j (graph analytics engine).

Relationships:
- power_supply, water_supply → :SUPPLIES
- road_link → :CONNECTS
- depends_on, requires_power, requires_water, requires_transit → :DEPENDS_ON
"""

from sqlalchemy.orm import Session

from app.db.neo4j import neo4j_session
from app.models.network import Edge, Node
from app.simulation.isolation import graph_fingerprint, isolated_graph

#: Neo4j relationship type for every edge type the engine understands. Must
#: cover all of app.simulation.semantics.EDGE_TYPES: an unmapped type is simply
#: never mirrored, so GDS centrality would rank a different graph from the
#: NetworkX fallback without any error to say so. The original property is kept
#: on each relationship as ``type``.
RELATIONSHIP_TYPES: dict[str, str] = {
    "power_supply": "SUPPLIES",
    "water_supply": "SUPPLIES",
    "road_link": "CONNECTS",
    "depends_on": "DEPENDS_ON",
    "requires_power": "DEPENDS_ON",
    "requires_water": "DEPENDS_ON",
    "requires_transit": "DEPENDS_ON",
}

#: Re-exported so persistence-side callers can reach the isolation helpers from
#: the graph service they already import. The implementation lives in
#: app.simulation.isolation so the engine keeps working without a DB driver.
__all__ = [
    "RELATIONSHIP_TYPES",
    "clear_network_from_neo4j",
    "graph_fingerprint",
    "isolated_graph",
    "sync_network_to_neo4j",
]


def clear_network_from_neo4j(network_id: str) -> None:
    """Remove one canonical network's analytics mirror idempotently."""
    with neo4j_session(write=True) as session:
        session.run(
            "MATCH (n:Asset {network_id: $network_id}) DETACH DELETE n",
            network_id=network_id,
        )


def sync_network_to_neo4j(db: Session, network_id: str):
    """
    Sync an entire network from PostgreSQL to Neo4j.
    Wipes existing nodes/edges for this network_id first to ensure a clean state.
    """
    # Fetch all nodes and edges from Postgres
    nodes = db.query(Node).filter(Node.network_id == network_id).all()
    edges = db.query(Edge).filter(Edge.network_id == network_id).all()

    # Convert to simple dictionaries for Neo4j consumption
    node_dicts = [
        {
            "id": str(n.id),
            "network_id": str(n.network_id),
            "name": n.name,
            "display_name": getattr(n, "display_name", None) or n.name,
            "type": n.node_type,
            "lat": n.lat,
            "lng": n.lng,
            "capacity": n.capacity,
            "current_load": n.current_load,
            "failure_threshold": n.failure_threshold,
            "status": n.status,
            "is_synthetic": getattr(n, "is_synthetic", True),
            "data_source": getattr(n, "data_source", "synthetic"),
            "name_source": getattr(n, "name_source", "synthetic") or "synthetic",
            "data_quality": getattr(n, "data_quality", "estimated") or "estimated",
        }
        for n in nodes
    ]

    edge_dicts = [
        {
            "id": str(e.id),
            "network_id": str(e.network_id),
            "source_id": str(e.source_id),
            "target_id": str(e.target_id),
            "type": e.edge_type,
            "weight": e.weight,
            "capacity": e.capacity,
            "is_bidirectional": e.is_bidirectional,
        }
        for e in edges
    ]

    # Run the Neo4j ingestion
    with neo4j_session(write=True) as session:
        # 1. Clear existing network in Neo4j
        session.run(
            "MATCH (n:Asset {network_id: $network_id}) DETACH DELETE n",
            network_id=network_id,
        )

        # 2. Setup indexes (idempotent)
        session.run("CREATE INDEX asset_id_idx IF NOT EXISTS FOR (n:Asset) ON (n.id)")
        session.run("CREATE INDEX asset_network_idx IF NOT EXISTS FOR (n:Asset) ON (n.network_id)")

        # 3. Ingest Nodes (using UNWIND for batch insert)
        session.run(
            """
            UNWIND $nodes AS node
            CREATE (n:Asset {
                id: node.id,
                network_id: node.network_id,
                name: node.name,
                display_name: node.display_name,
                type: node.type,
                lat: node.lat,
                lng: node.lng,
                capacity: node.capacity,
                current_load: node.current_load,
                failure_threshold: node.failure_threshold,
                status: node.status,
                is_synthetic: node.is_synthetic,
                data_source: node.data_source,
                name_source: node.name_source,
                data_quality: node.data_quality
            })
            """,
            nodes=node_dicts,
        )

        # Apply specific labels explicitly without APOC
        node_types = ["power_substation", "water_station", "hospital", "road_junction", "telecom_tower"]
        for ntype in node_types:
            session.run(
                f"""
                MATCH (n:Asset {{network_id: $network_id, type: $type}})
                SET n:{ntype}
                """,
                network_id=network_id,
                type=ntype,
            )

        # 4. Ingest Edges. Neo4j cannot parameterize a relationship type
        # without APOC, so edges are grouped by type and inserted per group.
        for edge_type, rel_type in RELATIONSHIP_TYPES.items():
            type_edges = [e for e in edge_dicts if e["type"] == edge_type]
            if not type_edges:
                continue

            # Insert directed edge
            session.run(
                f"""
                UNWIND $edges AS edge
                MATCH (source:Asset {{id: edge.source_id}})
                MATCH (target:Asset {{id: edge.target_id}})
                CREATE (source)-[r:{rel_type} {{
                    id: edge.id,
                    type: edge.type,
                    weight: edge.weight,
                    capacity: edge.capacity,
                    is_bidirectional: edge.is_bidirectional
                }}]->(target)
                """,
                edges=type_edges,
            )

            # Insert reverse edge if bidirectional (e.g., road_link)
            bidirectional_edges = [e for e in type_edges if e["is_bidirectional"]]
            if bidirectional_edges:
                session.run(
                    f"""
                    UNWIND $edges AS edge
                    MATCH (source:Asset {{id: edge.source_id}})
                    MATCH (target:Asset {{id: edge.target_id}})
                    CREATE (target)-[r:{rel_type} {{
                        id: edge.id,
                        type: edge.type,
                        weight: edge.weight,
                        capacity: edge.capacity,
                        is_bidirectional: edge.is_bidirectional
                    }}]->(source)
                    """,
                    edges=bidirectional_edges,
                )
