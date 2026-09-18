"""Tests for run_simulation_task's transient-infra retry path.

A dropped Postgres/Redis connection mid-task should retry the deterministic
computation with backoff rather than immediately -- and permanently --
marking the simulation failed; a genuine error (a bad scenario, a bug) should
still fail immediately with no retry.

run_simulation_task is a Celery @shared_task(bind=True); `task.run` is bound
to the real registered task instance, which has no active request context to
retry against outside a worker. `type(task).run` is the original, still-
undecorated function -- calling it directly with a hand-built `self` exercises
the task body with no Celery machinery involved, the same principle
test_api_scenarios.py already uses (call the endpoint function directly with
a MagicMock db rather than spin up a real request).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment shims (including redis.exceptions) are installed once for
# the whole suite by tests/conftest.py, before this file is collected.
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from app.models.network import Edge, Node, Scenario, SimulationResult
from app.simulation.runner import run_simulation_task

# shared_task hands back a lazily-bound Proxy, not the Task instance itself;
# _get_current_object() resolves it to the real, already-registered task.
RUN = type(run_simulation_task._get_current_object()).run


class _FakeRequest:
    def __init__(self, retries: int):
        self.retries = retries


class _FakeTask:
    """Stands in for the bound Celery task instance `self` receives."""

    def __init__(self, retries: int = 0, max_retries: int = 3):
        self.request = _FakeRequest(retries)
        self.max_retries = max_retries
        self.retry_calls: list[dict] = []

    def retry(self, exc=None, countdown=None):
        self.retry_calls.append({"exc": exc, "countdown": countdown})
        # Real Celery raises Retry from inside retry(); the task body does
        # `raise self.retry(...)`, so the fake must do the same to exercise
        # that control flow rather than returning a value to be raised.
        raise Retry(str(exc))


def _fake_db(sim, *, nodes_side_effect=None, scenario=None):
    """A MagicMock db dispatching by the model class each .query(...) call names."""
    db = MagicMock()

    def query_side_effect(model, *_args, **_kwargs):
        m = MagicMock()
        if model is SimulationResult:
            m.filter.return_value.first.return_value = sim
        elif model is Node:
            if nodes_side_effect is not None:
                m.filter.return_value.all.side_effect = nodes_side_effect
            else:
                m.filter.return_value.all.return_value = []
        elif model is Edge:
            m.filter.return_value.all.return_value = []
        elif model is Scenario:
            m.filter.return_value.first.return_value = scenario
        return m

    db.query.side_effect = query_side_effect
    return db


def _sim(simulation_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=simulation_id, status="pending", error_message=None)


def test_transient_error_with_retries_left_retries_and_leaves_status_alone(monkeypatch):
    simulation_id = str(uuid.uuid4())
    sim = _sim(simulation_id)
    db = _fake_db(sim, nodes_side_effect=OperationalError("SELECT", {}, Exception("connection reset")))
    monkeypatch.setattr("app.simulation.runner.SessionLocal", lambda: db)

    fake_self = _FakeTask(retries=0, max_retries=3)

    with pytest.raises(Retry):
        RUN(fake_self, simulation_id, str(uuid.uuid4()), [str(uuid.uuid4())], None)

    assert len(fake_self.retry_calls) == 1
    assert isinstance(fake_self.retry_calls[0]["exc"], OperationalError)
    assert fake_self.retry_calls[0]["countdown"] == 1  # min(2**0, 30)
    # Left at "running" -- a caller polling it sees an in-progress run, not a
    # false failure that then un-fails itself moments later.
    assert sim.status == "running"
    assert sim.error_message is None


def test_transient_error_backoff_grows_with_attempt_number(monkeypatch):
    simulation_id = str(uuid.uuid4())
    sim = _sim(simulation_id)
    db = _fake_db(sim, nodes_side_effect=OperationalError("SELECT", {}, Exception("timeout")))
    monkeypatch.setattr("app.simulation.runner.SessionLocal", lambda: db)

    fake_self = _FakeTask(retries=2, max_retries=3)

    with pytest.raises(Retry):
        RUN(fake_self, simulation_id, str(uuid.uuid4()), [str(uuid.uuid4())], None)

    assert fake_self.retry_calls[0]["countdown"] == 4  # min(2**2, 30)


def test_transient_error_after_exhausting_retries_marks_failed(monkeypatch):
    simulation_id = str(uuid.uuid4())
    sim = _sim(simulation_id)
    db = _fake_db(sim, nodes_side_effect=OperationalError("SELECT", {}, Exception("still down")))
    monkeypatch.setattr("app.simulation.runner.SessionLocal", lambda: db)

    fake_self = _FakeTask(retries=3, max_retries=3)

    with pytest.raises(OperationalError):
        RUN(fake_self, simulation_id, str(uuid.uuid4()), [str(uuid.uuid4())], None)

    assert fake_self.retry_calls == []
    assert sim.status == "failed"
    assert "repeated transient infrastructure errors" in sim.error_message


def test_non_transient_error_fails_immediately_without_retrying(monkeypatch):
    """A bad scenario (or any genuine bug) must not be retried -- it will
    fail the exact same way every time, so retrying only delays the correct
    "failed" state the caller is waiting on."""
    simulation_id = str(uuid.uuid4())
    network_id = str(uuid.uuid4())
    sim = _sim(simulation_id)
    scenario_id = str(uuid.uuid4())
    scenario = SimpleNamespace(
        id=scenario_id,
        network_id=network_id,
        modifications=[{"type": "not_a_real_modification"}],
    )
    db = _fake_db(sim, scenario=scenario)
    monkeypatch.setattr("app.simulation.runner.SessionLocal", lambda: db)

    fake_self = _FakeTask(retries=0, max_retries=3)

    with pytest.raises(ValueError, match="unsupported scenario modification type"):
        RUN(fake_self, simulation_id, network_id, [str(uuid.uuid4())], scenario_id)

    assert fake_self.retry_calls == []
    assert sim.status == "failed"
    assert sim.error_message == "Simulation execution failed. Consult server logs with the simulation ID."
