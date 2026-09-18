"""Structured (JSON) logging with a correlation id, shared by the API and the
Celery worker.

Every ``app.*`` logger created with ``logging.getLogger(__name__)`` already
propagates to the root logger; nothing previously configured that root
logger, so log lines came out via Python's bare "handler of last resort" (or
whatever the process happened to inherit) with no consistent shape and no way
to tie one request's log lines together, or a request to the background task
it dispatched.

``configure_logging`` replaces the root logger's handler with one that always
emits JSON and always carries ``correlation_id`` -- "-" outside any tracked
request or task. The API sets it per HTTP request (see ``app.main``'s
middleware); the worker sets it per simulation task (see
``app.simulation.runner``), so both processes' logs for one run share it even
though the value itself differs -- an HTTP request id is not meaningful
across a process boundary, but grepping either process's logs for one
simulation still works via the message text, exactly as before.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid

from fastapi import Request

#: "-" is the value read outside any request or task, so filtering
#: `correlation_id: "-"` in an aggregator finds exactly what is otherwise
#: untraceable: startup/shutdown logs, background maintenance, anything not
#: running inside a request or a simulation task.
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-"
)


class CorrelationIdFilter(logging.Filter):
    """Attaches the current correlation id to every log record it sees."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Renders one log record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON handler on the root logger.

    Idempotent-in-effect: called once from each process entrypoint (the API
    on import in app.main, the worker via Celery's setup_logging signal in
    app.celery_app) rather than guarded with a "did I already run" flag,
    since replacing root.handlers with the same single handler again is
    harmless and keeps this function trivial to reason about.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]


#: Standard-ish header name for a request-correlation id, echoed back on the
#: response so a caller can correlate it with server-side logs.
REQUEST_ID_HEADER = "X-Request-ID"


async def add_correlation_id(request: Request, call_next):
    """FastAPI HTTP middleware: tags every log line produced while handling
    this request with one id, and echoes it back on the response.

    Reuses an incoming X-Request-ID rather than always minting a new one, so
    a request forwarded through another service that already assigned an id
    keeps it end to end.
    """
    correlation_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    token = correlation_id_var.set(correlation_id)
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = correlation_id
    return response
