"""
Unit and schema tests for scenario API and UpgradeNodeModification.
Verifies:
1. Pydantic validation of UpgradeNodeModification and ScenarioCreate discriminated union.
2. In-memory graph modification via apply_scenario_modifications.
3. Network node existence validation in create_scenario endpoint.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx
import pytest

# Ensure backend directory is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment module shims (sqlalchemy, pydantic, fastapi, ...) are
# installed once for the whole suite by tests/conftest.py, before this file
# is collected.

from app.api.scenarios import (
    ScenarioCreate,
    UpgradeNodeModification,
    create_scenario,
)
from app.simulation.runner import apply_scenario_modifications
from fastapi import HTTPException


def test_upgrade_node_modification_capacity():
    node_id = uuid.uuid4()
    mod = UpgradeNodeModification(type="upgrade_node", node_id=node_id, capacity=160.0)
    assert mod.type == "upgrade_node"
    assert mod.node_id == node_id
    assert mod.capacity == 160.0
    dumped = mod.model_dump(exclude_none=True)
    assert dumped["type"] == "upgrade_node"
    assert dumped["capacity"] == 160.0


def test_upgrade_node_modification_multiplier_and_threshold():
    node_id = uuid.uuid4()
    mod = UpgradeNodeModification(
        type="upgrade_node",
        node_id=node_id,
        capacity_multiplier=2.0,
        failure_threshold=1.5,
    )
    assert mod.capacity_multiplier == 2.0
    assert mod.failure_threshold == 1.5


def test_apply_scenario_modifications_upgrade_capacity():
    G = nx.DiGraph()
    G.add_node("n1", capacity=100.0, failure_threshold=1.0)
    G.add_node("n2", capacity=50.0, failure_threshold=1.0)

    modifications = [{"type": "upgrade_node", "node_id": "n1", "capacity": 200.0}]
    G_mod = apply_scenario_modifications(G, modifications)

    # Immutability
    assert G.nodes["n1"]["capacity"] == 100.0
    # Modified graph has updated capacity
    assert G_mod.nodes["n1"]["capacity"] == 200.0
    assert G_mod.nodes["n2"]["capacity"] == 50.0


def test_apply_scenario_modifications_upgrade_multiplier():
    G = nx.DiGraph()
    G.add_node("n1", capacity=80.0, failure_threshold=1.0)

    modifications = [{"type": "upgrade_node", "node_id": "n1", "capacity_multiplier": 1.5}]
    G_mod = apply_scenario_modifications(G, modifications)

    assert abs(G_mod.nodes["n1"]["capacity"] - 120.0) < 1e-6
    assert G.nodes["n1"]["capacity"] == 80.0


def test_apply_scenario_modifications_upgrade_threshold():
    G = nx.DiGraph()
    G.add_node("n1", capacity=100.0, failure_threshold=1.0)

    modifications = [{"type": "upgrade_node", "node_id": "n1", "failure_threshold": 1.8}]
    G_mod = apply_scenario_modifications(G, modifications)

    assert G_mod.nodes["n1"]["failure_threshold"] == 1.8


def test_apply_scenario_modifications_unknown_node_raises():
    G = nx.DiGraph()
    G.add_node("n1", capacity=100.0)

    modifications = [{"type": "upgrade_node", "node_id": "nonexistent", "capacity": 200.0}]
    with pytest.raises(ValueError, match="unknown node"):
        apply_scenario_modifications(G, modifications)


def test_apply_scenario_modifications_unsupported_type_raises():
    G = nx.DiGraph()
    G.add_node("n1", capacity=100.0)

    modifications = [{"type": "invalid_type", "node_id": "n1"}]
    with pytest.raises(ValueError, match="unsupported scenario modification"):
        apply_scenario_modifications(G, modifications)


def test_create_scenario_endpoint_validation():
    network_id = uuid.uuid4()
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()
    outside_node = uuid.uuid4()

    # Mock DB session
    db = MagicMock()
    mock_network = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = mock_network
    db.query.return_value.filter.return_value.all.return_value = [(node_a,), (node_b,)]

    # 1. Valid upgrade scenario referencing known node
    req_valid = ScenarioCreate(
        network_id=network_id,
        name="Test Upgrade Scenario",
        description="Testing upgrade_node validation",
        modifications=[UpgradeNodeModification(type="upgrade_node", node_id=node_a, capacity=160.0)],
        initial_failures=[node_b],
    )
    res = create_scenario(req=req_valid, db=db)
    assert res.name == "Test Upgrade Scenario"
    assert db.add.called
    assert db.commit.called

    # 2. Invalid scenario referencing node outside network
    req_invalid = ScenarioCreate(
        network_id=network_id,
        name="Invalid Scenario",
        description="References outside node",
        modifications=[UpgradeNodeModification(type="upgrade_node", node_id=outside_node, capacity=160.0)],
        initial_failures=[node_a],
    )
    with pytest.raises(HTTPException) as exc_info:
        create_scenario(req=req_invalid, db=db)
    assert exc_info.value.status_code == 422
    assert "outside this network" in exc_info.value.detail
