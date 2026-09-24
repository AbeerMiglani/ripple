"""Tests for the simulation WebSocket's completion-event race fix.

Redis pub/sub does not replay missed messages. A simulation that settles
between the "does this id exist" check and the pub/sub subscribe actually
taking effect -- entirely possible for a fast cascade, or just a client slow
to connect -- used to leave the connection subscribed to a channel that would
never publish again, with no idle timeout to ever notice. The fix: check the
durable row immediately after accepting, and fall back to it on every idle
poll tick inside the wait loop too, so a missed publish is caught within
about a second either way.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment shims are installed once for the whole suite by
# tests/conftest.py, before this file is collected.
from app.api.ws import _current_status, simulation_websocket


class _FakeWebSocket:
    def __init__(self):
        self.headers: dict = {}
        self.query_params: dict = {}
        self.sent: list[str] = []
        self.closed: tuple | None = None
        self.accepted = False

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)


class _FakePubSub:
    def __init__(self, messages=None):
        self._messages = list(messages or [])

    async def subscribe(self, _channel):
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        return self._messages.pop(0) if self._messages else None

    async def unsubscribe(self, _channel):
        pass

    async def close(self):
        pass


class _FakeAsyncRedis:
    def __init__(self, pubsub: _FakePubSub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub


def _db_returning(row):
    """A db whose query().filter().first() answers with a fixed row."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    return db


def _session_factory(db):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=db)
    cm.__exit__ = MagicMock(return_value=False)
    return lambda: cm


# ---------------------------------------------------------------------------
# _current_status
# ---------------------------------------------------------------------------


def test_current_status_reads_the_status_column(monkeypatch):
    monkeypatch.setattr("app.api.ws.SessionLocal", _session_factory(_db_returning(("completed",))))
    assert _current_status(uuid.uuid4()) == "completed"


def test_current_status_none_when_simulation_missing(monkeypatch):
    monkeypatch.setattr("app.api.ws.SessionLocal", _session_factory(_db_returning(None)))
    assert _current_status(uuid.uuid4()) is None


async def test_status_lookups_run_off_the_event_loop(monkeypatch):
    """The lookup is a blocking DB round trip; on the loop thread it would
    stall every other request and socket this process is serving."""
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []

    def lookup(_sid):
        seen.append(threading.get_ident())
        return "completed"

    monkeypatch.setattr("app.api.ws.websocket_principal", MagicMock())
    monkeypatch.setattr("app.api.ws._current_status", lookup)

    await simulation_websocket(_FakeWebSocket(), str(uuid.uuid4()))

    assert seen and all(tid != loop_thread for tid in seen)


# ---------------------------------------------------------------------------
# simulation_websocket
# ---------------------------------------------------------------------------


async def test_unknown_simulation_closes_without_accepting(monkeypatch):
    monkeypatch.setattr("app.api.ws.websocket_principal", MagicMock())
    monkeypatch.setattr("app.api.ws.SessionLocal", _session_factory(_db_returning(None)))

    ws = _FakeWebSocket()
    await simulation_websocket(ws, str(uuid.uuid4()))

    assert ws.accepted is False
    assert ws.closed == (1008, "Simulation not found")


async def test_already_settled_status_sends_immediately_with_no_pubsub_subscribe(monkeypatch):
    """The core race: a simulation that finished before this connection was
    ever subscribed must still get a terminal message, not silence."""
    monkeypatch.setattr("app.api.ws.websocket_principal", MagicMock())
    monkeypatch.setattr("app.api.ws.SessionLocal", _session_factory(_db_returning(("completed",))))
    # No redis client patched at all -- reaching for it would be an error,
    # since the already-settled path must return before ever needing it.
    monkeypatch.setattr("app.api.ws.get_async_redis_client", MagicMock(side_effect=AssertionError("should not be called")))

    ws = _FakeWebSocket()
    await simulation_websocket(ws, str(uuid.uuid4()))

    assert ws.accepted is True
    assert json.loads(ws.sent[0]) == {"status": "completed"}


async def test_pubsub_message_delivers_normally_when_not_racing(monkeypatch):
    monkeypatch.setattr("app.api.ws.websocket_principal", MagicMock())
    monkeypatch.setattr("app.api.ws.SessionLocal", _session_factory(_db_returning(("running",))))
    fake_pubsub = _FakePubSub(messages=[{"data": json.dumps({"status": "completed"})}])
    monkeypatch.setattr("app.api.ws.get_async_redis_client", lambda: _FakeAsyncRedis(fake_pubsub))

    ws = _FakeWebSocket()
    await simulation_websocket(ws, str(uuid.uuid4()))

    assert json.loads(ws.sent[0]) == {"status": "completed"}


async def test_idle_poll_falls_back_to_the_row_when_pubsub_stays_silent(monkeypatch):
    """Simulates the message being missed entirely (e.g. published in the gap
    before subscribe took effect): pub/sub never delivers anything, but the
    row itself settles a couple of ticks in, and the fallback must catch it
    without waiting for the full wait budget."""
    monkeypatch.setattr("app.api.ws.websocket_principal", MagicMock())
    statuses = iter(["running", "running", "completed"])
    monkeypatch.setattr("app.api.ws._current_status", lambda _sid: next(statuses))
    fake_pubsub = _FakePubSub(messages=[])  # never publishes
    monkeypatch.setattr("app.api.ws.get_async_redis_client", lambda: _FakeAsyncRedis(fake_pubsub))

    ws = _FakeWebSocket()
    await simulation_websocket(ws, str(uuid.uuid4()))

    assert json.loads(ws.sent[-1]) == {"status": "completed"}
