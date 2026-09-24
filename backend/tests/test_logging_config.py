"""Tests for the structured-logging / correlation-id module.

app.logging_config has no transitive dependency on the router chain
app.main pulls in (app.db.postgres, app.services.recommendations,
app.simulation.cascade, ...) -- deliberately, so these tests can import it
directly rather than risk the cross-file shim pollution test_health.py's
docstring documents for importing app.main in-process.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment shims are installed once for the whole suite by
# tests/conftest.py, before this file is collected.
from app.logging_config import (
    REQUEST_ID_HEADER,
    CorrelationIdFilter,
    JsonFormatter,
    add_correlation_id,
    correlation_id_var,
)


def _make_record(message: str = "hello", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.example",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=exc_info,
    )


class _FakeHeaders(dict):
    def get(self, key, default=None):
        # Case-insensitive, matching real HTTP header lookup, since the
        # middleware is written against that expectation.
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = _FakeHeaders(headers or {})


class _FakeResponse:
    def __init__(self):
        self.headers: dict[str, str] = {}


# ---------------------------------------------------------------------------
# correlation_id_var
# ---------------------------------------------------------------------------


def test_correlation_id_defaults_to_a_dash_outside_any_request_or_task():
    assert correlation_id_var.get() == "-"


# ---------------------------------------------------------------------------
# CorrelationIdFilter
# ---------------------------------------------------------------------------


def test_filter_attaches_the_current_correlation_id():
    token = correlation_id_var.set("req-123")
    try:
        record = _make_record()
        assert CorrelationIdFilter().filter(record) is True
        assert record.correlation_id == "req-123"
    finally:
        correlation_id_var.reset(token)


def test_filter_attaches_dash_when_nothing_is_set():
    record = _make_record()
    CorrelationIdFilter().filter(record)
    assert record.correlation_id == "-"


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_produces_the_expected_fields():
    record = _make_record("cascade settled after %s waves")
    record.msg = "cascade settled after %s waves"
    record.args = (3,)
    CorrelationIdFilter().filter(record)  # formatter reads record.correlation_id, set by the filter in real use

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.example"
    assert payload["message"] == "cascade settled after 3 waves"
    assert payload["correlation_id"] == "-"
    assert "timestamp" in payload
    assert "exc_info" not in payload


def test_json_formatter_includes_exception_text_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=sys.exc_info())

    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]


# ---------------------------------------------------------------------------
# add_correlation_id middleware
# ---------------------------------------------------------------------------


async def test_middleware_mints_an_id_when_none_supplied():
    seen_during_call = {}

    async def call_next(_request):
        seen_during_call["correlation_id"] = correlation_id_var.get()
        return _FakeResponse()

    response = await add_correlation_id(_FakeRequest(), call_next)

    minted = response.headers[REQUEST_ID_HEADER]
    assert uuid.UUID(minted)  # a real UUID was minted, not an empty/placeholder value
    assert seen_during_call["correlation_id"] == minted


async def test_middleware_reuses_an_incoming_request_id():
    async def call_next(_request):
        return _FakeResponse()

    response = await add_correlation_id(_FakeRequest({REQUEST_ID_HEADER: "caller-supplied-id"}), call_next)

    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


@pytest.mark.parametrize(
    "hostile",
    [
        "x" * 129,  # unbounded length lands in every log line for the request
        "id with spaces",
        "id\u2028with-line-separator",
        '{"injected": true}',
        "",
    ],
)
async def test_middleware_replaces_an_implausible_request_id(hostile):
    async def call_next(_request):
        return _FakeResponse()

    response = await add_correlation_id(_FakeRequest({REQUEST_ID_HEADER: hostile}), call_next)

    replaced = response.headers[REQUEST_ID_HEADER]
    assert replaced != hostile
    assert uuid.UUID(replaced)


@pytest.mark.parametrize("plausible", ["caller-supplied-id", "a" * 128, "svc:trace.01_AB-9", str(uuid.uuid4())])
async def test_middleware_keeps_a_plausible_request_id(plausible):
    async def call_next(_request):
        return _FakeResponse()

    response = await add_correlation_id(_FakeRequest({REQUEST_ID_HEADER: plausible}), call_next)

    assert response.headers[REQUEST_ID_HEADER] == plausible


async def test_middleware_resets_the_context_var_after_the_request():
    async def call_next(_request):
        return _FakeResponse()

    await add_correlation_id(_FakeRequest(), call_next)

    # Must not leak into whatever runs next on this task -- a later request,
    # or a log line from code that is not handling any request at all.
    assert correlation_id_var.get() == "-"
