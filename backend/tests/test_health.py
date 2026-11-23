"""Tests for the /health/live liveness endpoint.

Runs in a fresh subprocess rather than importing app.main in-process: other
test modules in this suite install lightweight fake modules into
sys.modules (e.g. a stand-in app.db.redis) so they can run without the full
driver set installed, and those shims leak across the whole pytest session
once any test file has imported them. Importing the real app.main in the
same process risks picking up a stale shim instead of the real dependency
depending on test collection order. A subprocess sidesteps that entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_liveness_check_returns_ok_without_touching_datastores():
    """
    /health/live must report ok purely from the process being up. Unlike
    /health it must never call the Postgres/Neo4j/Redis connectivity
    checks, since a liveness probe should not fail (and trigger a restart)
    just because a downstream datastore is briefly unreachable — that is
    what /health (readiness) is for.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from app.main import liveness_check; "
            "print(json.dumps(liveness_check()))",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        if "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr:
            pytest.skip(
                "full backend dependencies not importable in this environment:\n"
                f"{result.stderr}"
            )
        raise AssertionError(f"liveness_check() subprocess failed:\n{result.stderr}")

    assert result.stdout.strip() == '{"status": "ok"}'


_HEALTH_SCRIPT = """
import json
from app import main
main.verify_postgres_connection = lambda: True
main.verify_redis_connection = lambda: True
def neo4j_unreachable():
    raise SystemExit("verify_neo4j_connection must not be called")
main.verify_neo4j_connection = neo4j_unreachable
print(json.dumps(main.health_check()))
"""


def test_health_reports_a_disabled_neo4j_without_degrading():
    """With NEO4J_ENABLED=false (the Vercel deployment) Neo4j is never
    contacted, and its absence does not turn readiness into a 503."""
    import os

    result = subprocess.run(
        [sys.executable, "-c", _HEALTH_SCRIPT],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "NEO4J_ENABLED": "false", "ENVIRONMENT": "development"},
    )
    if result.returncode != 0:
        if "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr:
            pytest.skip(f"full backend dependencies not importable:\n{result.stderr}")
        raise AssertionError(f"health_check() subprocess failed:\n{result.stderr}{result.stdout}")

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "status": "ok",
        "services": {"postgres": "ok", "neo4j": "disabled", "redis": "ok"},
    }
