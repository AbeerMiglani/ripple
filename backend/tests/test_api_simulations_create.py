"""POST /simulations: scenario runs are linked and must replay the scenario's event.

A scenario run becomes that scenario's cached result, and /scenarios/compare
pairs it with a baseline by the *scenario's* initial failures. A run started
from any other failures would be compared as though it had not been.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api import simulations as api
from app.api.simulations import SimulationCreate, create_simulation
from app.models.network import Network, Scenario, SimulationResult


def _db(node_ids, scenario):
    """Answers the three lookups create_simulation makes, and records the add."""
    def query(*entities):
        q = MagicMock()
        first = entities[0]
        if first is Network:
            q.filter.return_value.first.return_value = SimpleNamespace(id="net")
        elif first is Scenario:
            q.filter.return_value.first.return_value = scenario
        else:  # Node.id
            q.filter.return_value.all.return_value = [(nid,) for nid in node_ids]
        return q

    db = MagicMock()
    db.query.side_effect = query
    return db


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(api.run_simulation_task, "delay", lambda **kw: calls.append(kw))
    return calls


def test_scenario_run_records_its_scenario(dispatched):
    network_id, a, scenario_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scenario = SimpleNamespace(id=scenario_id, initial_failures=[str(a)])
    db = _db([a], scenario)

    create_simulation(
        SimulationCreate(network_id=network_id, initial_failures=[a], scenario_id=scenario_id),
        db=db,
    )

    added = db.add.call_args.args[0]
    assert isinstance(added, SimulationResult)
    assert added.scenario_id == scenario_id
    assert dispatched[0]["scenario_id"] == str(scenario_id)


def test_scenario_run_with_different_failures_is_rejected(dispatched):
    network_id, a, b, scenario_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scenario = SimpleNamespace(id=scenario_id, initial_failures=[str(a)])
    db = _db([a, b], scenario)

    with pytest.raises(HTTPException) as exc:
        create_simulation(
            SimulationCreate(network_id=network_id, initial_failures=[b], scenario_id=scenario_id),
            db=db,
        )

    assert exc.value.status_code == 422
    db.add.assert_not_called()
    assert not dispatched


def test_baseline_run_has_no_scenario(dispatched):
    network_id, a = uuid.uuid4(), uuid.uuid4()
    db = _db([a], scenario=None)

    create_simulation(SimulationCreate(network_id=network_id, initial_failures=[a]), db=db)

    assert db.add.call_args.args[0].scenario_id is None
    assert dispatched[0]["scenario_id"] is None
