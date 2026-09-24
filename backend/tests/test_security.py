"""The API's authentication, role and rate-limit boundary.

tests/conftest.py replaces ``app.security`` in sys.modules with a MagicMock so
the router modules import in any environment, which left this module, the
one thing between the network and the API, with no coverage at all. The
real file is loaded here under a private name, with its settings and Redis
client swapped for local stand-ins.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("starlette", reason="the real FastAPI stack is not installed")

from fastapi import HTTPException  # noqa: E402

_SECURITY_PATH = Path(__file__).resolve().parent.parent / "app" / "security.py"

OPERATOR_KEY = "op-key-0123456789"
VIEWER_KEY = "viewer-key-abcdef"
ADMIN_KEY = "admin-key-zyxwvu"


def _load_security():
    spec = importlib.util.spec_from_file_location("_real_app_security", _SECURITY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(**overrides):
    values = {
        "api_key_auth_enabled": True,
        "api_keys": {OPERATOR_KEY: "operator", VIEWER_KEY: "viewer", ADMIN_KEY: "admin"},
        "environment": "development",
        "rate_limit_per_minute": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def security(monkeypatch):
    module = _load_security()
    monkeypatch.setattr(module, "settings", _settings())
    return module


def _request(headers=None, host="10.0.0.5", route_path="/api/simulations/{sim_id}"):
    return SimpleNamespace(
        headers={k.lower(): v for k, v in (headers or {}).items()},
        client=SimpleNamespace(host=host),
        scope={"route": SimpleNamespace(path=route_path)},
        url=SimpleNamespace(path="/api/simulations/123"),
    )


class _FakePipeline:
    def __init__(self, store, fail):
        self.store, self.fail, self.ops = store, fail, []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds, nx=False):
        self.ops.append(("expire", key, seconds, nx))

    def execute(self):
        if self.fail:
            raise ConnectionError("redis down")
        results = []
        for op in self.ops:
            if op[0] == "incr":
                self.store[op[1]] = self.store.get(op[1], 0) + 1
                results.append(self.store[op[1]])
            else:
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self, fail=False):
        self.store: dict[str, int] = {}
        self.fail = fail

    def pipeline(self):
        return _FakePipeline(self.store, self.fail)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_auth_disabled_grants_the_development_principal(security, monkeypatch):
    monkeypatch.setattr(security, "settings", _settings(api_key_auth_enabled=False))
    principal = await security.require_principal(_request())
    assert principal.role == "admin"


async def test_missing_key_is_rejected(security):
    with pytest.raises(HTTPException) as exc:
        await security.require_principal(_request())
    assert exc.value.status_code == 401


async def test_unknown_key_is_rejected(security):
    with pytest.raises(HTTPException) as exc:
        await security.require_principal(_request({"X-API-Key": "not-a-real-key"}))
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": OPERATOR_KEY},
        {"Authorization": f"Bearer {OPERATOR_KEY}"},
        {"Authorization": f"bearer   {OPERATOR_KEY}  "},
        {"Authorization": OPERATOR_KEY},
    ],
)
async def test_key_is_accepted_from_either_header(security, headers):
    principal = await security.require_principal(_request(headers))
    assert principal.role == "operator"


async def test_fingerprint_never_contains_the_whole_key(security):
    principal = await security.require_principal(_request({"X-API-Key": OPERATOR_KEY}))
    assert OPERATOR_KEY not in principal.key_fingerprint
    assert principal.key_fingerprint.startswith(OPERATOR_KEY[:4])


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "gate", "allowed"),
    [
        (VIEWER_KEY, "require_viewer", True),
        (VIEWER_KEY, "require_operator", False),
        (VIEWER_KEY, "require_admin", False),
        (OPERATOR_KEY, "require_operator", True),
        (OPERATOR_KEY, "require_admin", False),
        (ADMIN_KEY, "require_operator", True),
        (ADMIN_KEY, "require_admin", True),
    ],
)
async def test_role_gates(security, key, gate, allowed):
    check = getattr(security, gate)(_request({"X-API-Key": key}))
    if allowed:
        await check
    else:
        with pytest.raises(HTTPException) as exc:
            await check
        assert exc.value.status_code == 403


def test_websocket_accepts_the_key_as_a_query_parameter(security):
    ws = SimpleNamespace(headers={}, query_params={"api_key": VIEWER_KEY})
    assert security.websocket_principal(ws).role == "viewer"


def test_websocket_without_a_key_is_rejected(security):
    ws = SimpleNamespace(headers={}, query_params={})
    with pytest.raises(HTTPException):
        security.websocket_principal(ws)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_requests_over_the_limit_get_429(security, monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(security, "get_redis_client", lambda: redis)
    request = _request()

    for _ in range(3):
        security.enforce_rate_limit(request)
    with pytest.raises(HTTPException) as exc:
        security.enforce_rate_limit(request)

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "60"}


def test_buckets_are_per_client_and_per_route_template(security, monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(security, "get_redis_client", lambda: redis)

    for _ in range(3):
        security.enforce_rate_limit(_request(host="10.0.0.5"))
    # A different client, and a different route, each have their own budget.
    security.enforce_rate_limit(_request(host="10.0.0.6"))
    security.enforce_rate_limit(_request(route_path="/api/networks"))

    assert redis.store == {
        "rate:10.0.0.5:/api/simulations/{sim_id}": 3,
        "rate:10.0.0.6:/api/simulations/{sim_id}": 1,
        "rate:10.0.0.5:/api/networks": 1,
    }


def test_limiter_outage_fails_open_outside_production(security, monkeypatch):
    monkeypatch.setattr(security, "get_redis_client", lambda: _FakeRedis(fail=True))
    security.enforce_rate_limit(_request())  # does not raise


def test_limiter_outage_fails_closed_in_production(security, monkeypatch):
    monkeypatch.setattr(security, "settings", _settings(environment="production"))
    monkeypatch.setattr(security, "get_redis_client", lambda: _FakeRedis(fail=True))
    with pytest.raises(HTTPException) as exc:
        security.enforce_rate_limit(_request())
    assert exc.value.status_code == 503
