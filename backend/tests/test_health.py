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
