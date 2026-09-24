"""
Seed data ingestion service.
Reads the generated GeoJSON seed data, populates PostgreSQL/PostGIS,
and then syncs to Neo4j.
"""

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.redis import get_redis_client
from app.models.network import Edge, Network, Node
from app.services.geo import to_ewkt_point
from app.services.graph_sync import clear_network_from_neo4j, sync_network_to_neo4j
from app.simulation.population import SERVICE_RADIUS_DEFAULTS_M

# In Docker, the data directory is mounted at /data
SEED_DIR = Path("/data/seed")
if not SEED_DIR.exists():
    SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "seed"
SEED_NETWORK_NAME = "Manipal Demo Network"
#: Metrics supported by app.services.analytics.calculate_centrality. Each is
#: cached under its own key, so all of them must be invalidated on re-seed.
CENTRALITY_CACHE_METRICS = ("betweenness", "pagerank")
logger = logging.getLogger(__name__)


def resolve_seed_dir(source: str | None = None) -> Path:
    """Return the directory to ingest the baseline topology from.

    ``synthetic`` uses the committed seed fixture. ``osm`` pulls the real road
    network for the configured place through the OSMnx pipeline, caching the
    result so a re-seed does not re-download it.

    Everything downstream is unchanged either way. The graph is still built from
    the database, so the runner, the API, the map and the recommendation engine
    need no knowledge of where the topology came from -- which is what lets the
    Motter-Lai resimulations run against real-world geometry without a second
    code path.
    """
    resolved = source or settings.topology_source
    if resolved != "osm":
        return SEED_DIR

    from app.services.osm_ingestion import download_osm_road_data

    cache_dir = Path(settings.osm_cache_dir)
    if (cache_dir / "nodes.geojson").exists() and (cache_dir / "edges.json").exists():
        logger.info("using cached OSM topology at %s", cache_dir)
        return cache_dir

    place = settings.osm_place
    network_type = settings.osm_network_type
    logger.info("downloading OSM topology for %r into %s", place, cache_dir)
    download_osm_road_data(place, cache_dir, network_type)
    return cache_dir


def ingest_seed_data(db: Session, force: bool = False, source: str | None = None) -> str:
    """
    Ingests seed data if no networks exist (or if force=True).
    ``source`` overrides the configured topology source for this call.
    Returns the network_id.
    """
    existing = db.query(Network).filter(Network.name == SEED_NETWORK_NAME).first()
    if existing and not force:
        logger.info("network %s already exists; skipping seed ingestion", existing.id)
        return str(existing.id)

    seed_dir = resolve_seed_dir(source)
    logger.info("reading seed data from %s", seed_dir)
    with open(seed_dir / "nodes.geojson", "r") as f:
        nodes_data = json.load(f)

    with open(seed_dir / "edges.json", "r") as f:
        edges_data = json.load(f)

    # Keep relational ingestion atomic. Neo4j is a read-optimized mirror, so
    # it is synchronized only after the canonical transaction commits.
    replaced_network_id = str(existing.id) if existing else None
    try:
        if existing:
            db.delete(existing)
            db.flush()

        network = Network(
            name=SEED_NETWORK_NAME,
            description="Synthetic seed dataset for the Ripple demonstration",
        )
        db.add(network)
        db.flush()
        net_id = network.id

        node_objects = []
        for feature in nodes_data["features"]:
            props = feature["properties"]
            # GeoJSON positions are [longitude, latitude] (RFC 7946 3.1.1).
            coords = feature["geometry"]["coordinates"]
            node_type = props["node_type"]
            node_objects.append(
                Node(
                    id=props["id"],
                    network_id=net_id,
                    name=props["name"],
                    display_name=props.get("display_name") or props["name"],
                    node_type=props["node_type"],
                    lat=coords[1],
                    lng=coords[0],
                    geom=to_ewkt_point(coords[1], coords[0]),
                    capacity=props["capacity"],
                    current_load=props["current_load"],
                    failure_threshold=props["failure_threshold"],
                    population_served=props["population_served"],
                    # Service areas are what make population impact
                    # deduplicable; fall back to the per-type planning radius
                    # when the dataset does not declare one.
                    service_radius_m=props.get(
                        "service_radius_m", SERVICE_RADIUS_DEFAULTS_M.get(node_type)
                    ),
                    status=props["status"],
                    is_synthetic=props.get("is_synthetic", True),
                    data_source=props.get("data_source", "synthetic"),
                    name_source=props.get("name_source", "synthetic"),
                    data_quality=props.get("data_quality", "estimated"),
                )
            )
        db.add_all(node_objects)

        edge_objects = [
            Edge(
                id=edge["id"],
                network_id=net_id,
                source_id=edge["source_id"],
                target_id=edge["target_id"],
                edge_type=edge["edge_type"],
                weight=edge["weight"],
                capacity=edge["capacity"],
                is_bidirectional=edge["is_bidirectional"],
            )
            for edge in edges_data
        ]
        db.add_all(edge_objects)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Synchronize the read-optimized mirror only after canonical data commits.
    if replaced_network_id:
        clear_network_from_neo4j(replaced_network_id)
    sync_network_to_neo4j(db, str(net_id))
    try:
        # Cache keys are "centrality:{id}:{metric}", so each metric must be
        # dropped explicitly; the old unsuffixed delete matched nothing and left
        # stale scores referencing node IDs that no longer exist after a re-seed.
        stale_keys = [f"centrality:{net_id}:{metric}" for metric in CENTRALITY_CACHE_METRICS]
        if replaced_network_id:
            stale_keys += [
                f"centrality:{replaced_network_id}:{metric}"
                for metric in CENTRALITY_CACHE_METRICS
            ]
        get_redis_client().delete(*stale_keys)
    except Exception:
        logger.warning("could not invalidate centrality cache for %s", net_id, exc_info=True)

    logger.info("seed ingestion complete: network=%s", net_id)
    return str(net_id)


if __name__ == "__main__":
    import argparse

    from app.db.postgres import SessionLocal

    parser = argparse.ArgumentParser(description="Ingest the Ripple baseline network")
    parser.add_argument("--force", action="store_true", help="replace the existing named demo network")
    parser.add_argument(
        "--source",
        choices=("synthetic", "osm"),
        default=None,
        help="baseline topology source; defaults to the TOPOLOGY_SOURCE setting",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ingest_seed_data(db, force=args.force, source=args.source)
    finally:
        db.close()
