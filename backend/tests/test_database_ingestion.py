"""
Unit and schema integrity tests for database ingestion and graph sync.
Verifies that:
1. Instantiating Node(name="Test Node") automatically sets display_name="Test Node".
2. Seeding 120 nodes from data/seed/nodes.geojson succeeds with non-null display_name on all nodes.
3. Graph sync correctly extracts and delivers display_name and provenance attributes to Neo4j.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend directory is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
SEED_NODES_PATH = PROJECT_ROOT / "data" / "seed" / "nodes.geojson"
SEED_EDGES_PATH = PROJECT_ROOT / "data" / "seed" / "edges.json"

# Lean-environment module shims (sqlalchemy, geoalchemy2, neo4j, ...) are
# installed once for the whole suite by tests/conftest.py, before this file
# is collected.

from app.models.network import Node
from app.services.graph_sync import sync_network_to_neo4j


def test_node_model_auto_sets_display_name():
    """
    Test that instantiating Node(name="Test Node") automatically sets display_name="Test Node".
    Also verifies defensive fallback behavior when display_name is omitted, None, or blank.
    """
    # 1. Standard instantiation with name only
    node1 = Node(name="Test Node")
    assert node1.name == "Test Node"
    assert node1.display_name == "Test Node"

    # 2. Instantiation with explicit display_name=None falls back to name
    node2 = Node(name="Substation Alpha", display_name=None)
    assert node2.display_name == "Substation Alpha"

    # 3. Instantiation with empty string display_name falls back to name
    node3 = Node(name="Hospital Beta", display_name="")
    assert node3.display_name == "Hospital Beta"

    # 4. Explicit non-empty display_name is preserved
    node4 = Node(name="PS-01", display_name="Manipal Main Substation")
    assert node4.name == "PS-01"
    assert node4.display_name == "Manipal Main Substation"

    # 5. Setting display_name to None on an existing instance falls back to name
    node1.display_name = None
    assert node1.display_name == "Test Node"


def test_seed_ingestion_120_nodes_succeeds_with_non_null_display_names():
    """
    Test that seeding 120 nodes from data/seed/nodes.geojson succeeds with
    non-null display_name on all nodes under strict NOT NULL schema constraints.
    """
    with open(SEED_NODES_PATH, "r", encoding="utf-8") as f:
        nodes_data = json.load(f)

    assert len(nodes_data["features"]) == 120, "Seed dataset must contain exactly 120 features"

    # Create in-memory SQLite table reflecting PostgreSQL schema after migration d2e3f4a5b6c7
    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            network_id TEXT NOT NULL,
            name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            geom TEXT NOT NULL,
            capacity REAL NOT NULL,
            current_load REAL NOT NULL,
            failure_threshold REAL NOT NULL,
            population_served INTEGER NOT NULL,
            status TEXT NOT NULL,
            is_synthetic BOOLEAN NOT NULL DEFAULT 1,
            data_source TEXT NOT NULL DEFAULT 'synthetic',
            name_source TEXT NOT NULL DEFAULT 'synthetic',
            data_quality TEXT NOT NULL DEFAULT 'estimated'
        )
    """)

    # 1. Negative Test: Confirm that omitting display_name fails NOT NULL constraint
    first_feature = nodes_data["features"][0]
    p0 = first_feature["properties"]
    c0 = first_feature["geometry"]["coordinates"]
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed: nodes.display_name"):
        cur.execute("""
            INSERT INTO nodes (
                id, network_id, name, node_type, lat, lng, geom,
                capacity, current_load, failure_threshold, population_served,
                status, is_synthetic, data_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p0["id"], "test-network-id", p0["name"], p0["node_type"],
            c0[1], c0[0], f"SRID=4326;POINT({c0[0]} {c0[1]})",
            p0["capacity"], p0["current_load"], p0["failure_threshold"],
            p0["population_served"], p0["status"],
            p0.get("is_synthetic", True), p0.get("data_source", "synthetic")
        ))

    # 2. Positive Test: Seeding with updated ingestion logic succeeds for all 120 nodes
    net_id = "test-network-id"
    node_objects = []
    for feature in nodes_data["features"]:
        props = feature["properties"]
        coords = feature["geometry"]["coordinates"]
        display_name = props.get("display_name") or props["name"]
        name_source = props.get("name_source", "synthetic")
        data_quality = props.get("data_quality", "estimated")

        # Create Node model instance
        node = Node(
            id=props["id"],
            network_id=net_id,
            name=props["name"],
            display_name=display_name,
            node_type=props["node_type"],
            lat=coords[1],
            lng=coords[0],
            geom=f"SRID=4326;POINT({coords[0]} {coords[1]})",
            capacity=props["capacity"],
            current_load=props["current_load"],
            failure_threshold=props["failure_threshold"],
            population_served=props["population_served"],
            status=props["status"],
            is_synthetic=props.get("is_synthetic", True),
            data_source=props.get("data_source", "synthetic"),
            name_source=name_source,
            data_quality=data_quality,
        )
        node_objects.append(node)

        # Insert into strict NOT NULL SQLite table
        cur.execute("""
            INSERT INTO nodes (
                id, network_id, name, display_name, node_type, lat, lng, geom,
                capacity, current_load, failure_threshold, population_served,
                status, is_synthetic, data_source, name_source, data_quality
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            node.id, node.network_id, node.name, node.display_name, node.node_type,
            node.lat, node.lng, node.geom, node.capacity, node.current_load,
            node.failure_threshold, node.population_served, node.status,
            node.is_synthetic, node.data_source, node.name_source, node.data_quality
        ))

    con.commit()

    # Verify all 120 nodes were successfully inserted
    assert len(node_objects) == 120
    cur.execute("SELECT count(*) FROM nodes")
    assert cur.fetchone()[0] == 120

    # Verify zero null or empty display_names in database
    cur.execute("SELECT count(*) FROM nodes WHERE display_name IS NULL OR display_name = ''")
    assert cur.fetchone()[0] == 0

    # Verify every node's display_name matches expected value
    for node in node_objects:
        assert node.display_name is not None
        assert len(node.display_name) > 0
        assert node.display_name == node.name
        assert node.name_source == "synthetic"
        assert node.data_quality == "estimated"


def test_graph_sync_provenance_to_neo4j():
    """
    Test that graph_sync extracts display_name, name_source, and data_quality
    and includes them in the Cypher UNWIND query for Neo4j.
    """
    mock_db = MagicMock()
    mock_node = MagicMock()
    mock_node.id = "0db3d452-fff0-49cd-8ca9-0f13bf02ce10"
    mock_node.network_id = "test-net-id"
    mock_node.name = "Grid Substation PS-01"
    mock_node.display_name = "Grid Substation PS-01"
    mock_node.node_type = "power_substation"
    mock_node.lat = 13.35
    mock_node.lng = 74.78
    mock_node.capacity = 100.0
    mock_node.current_load = 85.0
    mock_node.failure_threshold = 1.0
    mock_node.status = "operational"
    mock_node.is_synthetic = True
    mock_node.data_source = "synthetic"
    mock_node.name_source = "synthetic"
    mock_node.data_quality = "verified"

    mock_db.query().filter().all.side_effect = [[mock_node], []]

    with patch("app.services.graph_sync.neo4j_session") as mock_neo4j_session:
        mock_session = MagicMock()
        mock_neo4j_session.return_value.__enter__.return_value = mock_session

        sync_network_to_neo4j(mock_db, "test-net-id")

        # Locate the UNWIND nodes query
        node_call = next(
            call for call in mock_session.run.call_args_list
            if "UNWIND $nodes AS node" in call[0][0]
        )
        cypher = node_call[0][0]
        nodes_payload = node_call[1]["nodes"]

        # 1. Assert Cypher statement contains provenance fields
        assert "display_name: node.display_name" in cypher
        assert "name_source: node.name_source" in cypher
        assert "data_quality: node.data_quality" in cypher

        # 2. Assert nodes payload contains extracted values
        assert len(nodes_payload) == 1
        assert nodes_payload[0]["display_name"] == "Grid Substation PS-01"
        assert nodes_payload[0]["name_source"] == "synthetic"
        assert nodes_payload[0]["data_quality"] == "verified"
