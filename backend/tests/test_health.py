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


_FRONTEND_SCRIPT = """
import json, pathlib, sys, tempfile
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.main import serve_frontend

app = FastAPI()
@app.get("/api/thing")
def thing():
    return {"api": True}

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    assert serve_frontend(app, root) is False  # no build: nothing registered
    (root / "assets").mkdir()
    (root / "index.html").write_text("<html>ripple</html>")
    (root / "assets" / "app.js").write_text("console.log(1)")
    (root / "tiles.bin").write_bytes(bytes(range(256)))
    assert serve_frontend(app, root) is True
    client = TestClient(app)
    out = {
        "index": [client.get("/").status_code, client.get("/").text],
        "asset": client.get("/assets/app.js").status_code,
        "range": [client.get("/tiles.bin", headers={"Range": "bytes=0-9"}).status_code,
                  len(client.get("/tiles.bin", headers={"Range": "bytes=0-9"}).content)],
        "api": client.get("/api/thing").json(),
        "unknown_api": [client.get("/api/missing").status_code,
                        client.get("/api/missing").headers["content-type"]],
    }
print(json.dumps(out))
"""


def test_the_frontend_build_is_served_below_the_api():
    """The Vercel deployment serves the built frontend from the app itself
    (Vercel promotes it to its CDN). API routes win, a missing path is still a
    JSON 404 rather than the page, and the map tiles honour Range requests."""
    result = subprocess.run(
        [sys.executable, "-c", _FRONTEND_SCRIPT],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        if "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr:
            pytest.skip(f"full backend dependencies not importable:\n{result.stderr}")
        raise AssertionError(f"frontend subprocess failed:\n{result.stderr}{result.stdout}")

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["index"] == [200, "<html>ripple</html>"]
    assert out["asset"] == 200
    assert out["range"] == [206, 10]
    assert out["api"] == {"api": True}
    assert out["unknown_api"] == [404, "application/json"]
