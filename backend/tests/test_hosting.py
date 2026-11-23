"""Settings and code paths for running without a worker or Neo4j (Vercel).

Every one of these is opt-in: the defaults keep the Docker stack's behaviour,
and the tests below pin both sides of each switch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pydantic_settings = pytest.importorskip("pydantic_settings")
pytest.importorskip("starlette", reason="the real FastAPI stack is not installed")

from fastapi import HTTPException  # noqa: E402
from pydantic import SecretStr, ValidationError  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.api import internal  # noqa: E402
from app.api import simulations as api  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models.network import Edge, Node, Scenario, SimulationResult  # noqa: E402
from app.services import analytics, ingestion, retention  # noqa: E402
from app.simulation import runner  # noqa: E402

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: A production configuration with no Celery and no Neo4j, as on Vercel.
PUBLIC = dict(
    _env_file=None,
    environment="production",
    database_url="postgresql://app:s3cret@db.example.com/ripple",
    redis_url="rediss://default:s3cret@cache.example.com:6379",
    simulation_execution="inline",
    neo4j_enabled=False,
    api_key_auth_enabled=True,
    public_access_role="operator",
    cors_origins=["https://ripple.example.com"],
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("DATABASE_URL", "REDIS_URL", "KV_URL", "ENVIRONMENT", "API_KEYS", "CRON_SECRET"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h/db?sslmode=require", "postgresql+psycopg2://u:p@h/db?sslmode=require"),
        ("postgresql://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
    ],
)
def test_hosted_database_urls_use_the_installed_driver(clean_env, url, expected):
    assert Settings(_env_file=None, database_url=url).database_url == expected


def test_redis_url_falls_back_to_the_integration_name(clean_env, monkeypatch):
    monkeypatch.setenv("KV_URL", "rediss://default:x@kv.example.com:6379")
    assert Settings(_env_file=None).redis_url == "rediss://default:x@kv.example.com:6379"
    monkeypatch.setenv("REDIS_URL", "redis://explicit:6379")
    assert Settings(_env_file=None).redis_url == "redis://explicit:6379"


def test_defaults_keep_the_docker_stack_behaviour(clean_env):
    s = Settings(_env_file=None)
    assert s.simulation_execution == "celery"
    assert s.neo4j_enabled is True
    assert s.public_access_role == "none"
    assert s.client_ip_header is None
    assert s.cron_secret is None


def test_public_deployment_without_worker_or_neo4j_is_valid(clean_env):
    s = Settings(**PUBLIC)
    # Celery and Neo4j still carry their development defaults, unused.
    assert "ripple_dev" in s.celery_broker_url and "ripple_dev" in s.neo4j_password


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"simulation_execution": "celery"}, "unique database, Neo4j, and Redis credentials"),
        ({"neo4j_enabled": True}, "unique database, Neo4j, and Redis credentials"),
        ({"public_access_role": "none"}, "API_KEYS, or PUBLIC_ACCESS_ROLE"),
        ({"api_key_auth_enabled": False}, "API_KEY_AUTH_ENABLED=true"),
        ({"cron_secret": "short"}, "CRON_SECRET must be at least 16 characters"),
    ],
)
def test_production_still_checks_what_is_in_use(clean_env, override, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**{**PUBLIC, **override})


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_cron_secret_means_unset(clean_env, monkeypatch, value):
    monkeypatch.setenv("CRON_SECRET", value)
    assert Settings(_env_file=None).cron_secret is None


def test_cleanup_does_not_exist_with_an_empty_secret(monkeypatch):
    monkeypatch.setattr(internal.settings, "cron_secret", SecretStr(""))
    with pytest.raises(HTTPException) as exc:
        internal.require_cron_secret(_request("Bearer "))
    assert exc.value.status_code == 404


def test_blank_client_ip_header_means_unset(clean_env):
    assert Settings(_env_file=None, client_ip_header="  ").client_ip_header is None
    assert Settings(_env_file=None, client_ip_header="X-Real-IP").client_ip_header == "x-real-ip"


# ---------------------------------------------------------------------------
# Inline execution
# ---------------------------------------------------------------------------


def _db(sim, *, nodes=(), edges=(), nodes_error=None):
    db = MagicMock()

    def query(model, *_args):
        q = MagicMock()
        if model is SimulationResult:
            q.filter.return_value.first.return_value = sim
        elif model is Node:
            if nodes_error:
                q.filter.return_value.all.side_effect = nodes_error
            else:
                q.filter.return_value.all.return_value = list(nodes)
        elif model is Edge:
            q.filter.return_value.all.return_value = list(edges)
        elif model is Scenario:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query
    return db


def _node(node_id):
    return SimpleNamespace(
        id=node_id, name=node_id, node_type="power_substation", capacity=100.0, current_load=10.0,
        failure_threshold=1.0, population_served=0, lat=13.35, lng=74.79, geom=None,
        service_radius_m=None, status="operational",
    )


def _edge(src, tgt):
    return SimpleNamespace(
        id=f"{src}-{tgt}", source_id=src, target_id=tgt, edge_type="power_supply",
        weight=1.0, capacity=100.0, is_bidirectional=False,
    )


@pytest.fixture
def no_redis(monkeypatch):
    def fail():
        raise AssertionError("an inline run must not touch Redis")

    monkeypatch.setattr(runner, "get_redis_client", fail)


def test_inline_run_completes_without_publishing(no_redis):
    sim = SimpleNamespace(id="s", status="pending", error_message=None)
    db = _db(sim, nodes=[_node("A"), _node("B")], edges=[_edge("A", "B")])

    runner.run_simulation_inline(db, "s", "net", ["A"])

    assert sim.status == "completed"
    assert sim.total_failed >= 1
    assert sim.error_message is None


def test_inline_failure_is_recorded_not_raised(no_redis):
    sim = SimpleNamespace(id="s", status="pending", error_message=None)
    db = _db(sim, nodes_error=RuntimeError("boom"))

    runner.run_simulation_inline(db, "s", "net", ["A"])  # does not raise

    assert sim.status == "failed"
    assert sim.error_message == runner.GENERIC_FAILURE_MESSAGE
    db.rollback.assert_called()


def _create_db(node_ids):
    def query(*entities):
        q = MagicMock()
        if entities[0] is api.Network:
            q.filter.return_value.first.return_value = SimpleNamespace(id="net")
        else:
            q.filter.return_value.all.return_value = [(nid,) for nid in node_ids]
        return q

    db = MagicMock()
    db.query.side_effect = query
    return db


def test_create_simulation_runs_inline_when_configured(monkeypatch):
    ran, queued = [], []
    monkeypatch.setattr(api.settings, "simulation_execution", "inline")
    monkeypatch.setattr(api, "run_simulation_inline", lambda db, **kw: ran.append(kw))
    monkeypatch.setattr(api.run_simulation_task, "delay", lambda **kw: queued.append(kw))
    a = uuid.uuid4()
    db = _create_db([a])

    returned = api.create_simulation(api.SimulationCreate(network_id=uuid.uuid4(), initial_failures=[a]), db=db)

    assert queued == []
    assert ran and ran[0]["initial_failures"] == [str(a)]
    # Answered with the row as the run left it, re-read after the run.
    assert returned is db.add.call_args.args[0]
    assert db.refresh.call_count == 2


def test_create_simulation_queues_by_default(monkeypatch):
    queued = []
    monkeypatch.setattr(api, "run_simulation_inline", lambda *a, **kw: pytest.fail("ran inline"))
    monkeypatch.setattr(api.run_simulation_task, "delay", lambda **kw: queued.append(kw))
    a = uuid.uuid4()
    api.create_simulation(api.SimulationCreate(network_id=uuid.uuid4(), initial_failures=[a]), db=_create_db([a]))
    assert len(queued) == 1


# ---------------------------------------------------------------------------
# Neo4j disabled
# ---------------------------------------------------------------------------


def test_centrality_skips_gds_when_neo4j_is_disabled(monkeypatch):
    monkeypatch.setattr(analytics.settings, "neo4j_enabled", False)
    monkeypatch.setattr(analytics.settings, "centrality_cache_ttl_seconds", 0)
    monkeypatch.setattr(analytics, "_run_gds", lambda *a: pytest.fail("called Neo4j"))
    monkeypatch.setattr(analytics, "_run_nx", lambda network_id, metric, db: [{"metric": metric}])
    assert analytics.calculate_centrality("net", "pagerank") == [{"metric": "pagerank"}]
    with pytest.raises(ValueError):
        analytics.calculate_centrality("net", "closeness")  # type: ignore[arg-type]


def test_ingestion_skips_the_neo4j_mirror_when_disabled(monkeypatch):
    monkeypatch.setattr(ingestion.settings, "neo4j_enabled", False)
    monkeypatch.setattr(ingestion, "sync_network_to_neo4j", lambda *a: pytest.fail("synced to Neo4j"))
    monkeypatch.setattr(ingestion, "clear_network_from_neo4j", lambda *a: pytest.fail("cleared Neo4j"))
    monkeypatch.setattr(ingestion, "get_redis_client", MagicMock)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    ingestion.ingest_seed_data(db)

    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def _request(authorization=None):
    headers = {} if authorization is None else {"authorization": authorization}
    return SimpleNamespace(headers=headers)


def test_cleanup_does_not_exist_without_a_secret(monkeypatch):
    monkeypatch.setattr(internal.settings, "cron_secret", None)
    with pytest.raises(HTTPException) as exc:
        internal.require_cron_secret(_request("Bearer anything"))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong-secret-0000000", "correct-secret-1234567"])
def test_cleanup_rejects_anything_but_the_secret(monkeypatch, authorization):
    monkeypatch.setattr(internal.settings, "cron_secret", SecretStr("correct-secret-1234567"))
    with pytest.raises(HTTPException) as exc:
        internal.require_cron_secret(_request(authorization))
    assert exc.value.status_code == 401


def test_cleanup_accepts_the_scheduler(monkeypatch):
    monkeypatch.setattr(internal.settings, "cron_secret", SecretStr("correct-secret-1234567"))
    internal.require_cron_secret(_request("Bearer correct-secret-1234567"))


def test_delete_expired_removes_both_tables_before_the_cutoff():
    db = MagicMock()
    db.execute.side_effect = [SimpleNamespace(rowcount=4), SimpleNamespace(rowcount=2)]
    now = datetime(2026, 11, 23, 12, tzinfo=UTC)

    counts = retention.delete_expired(db, older_than=timedelta(days=7), now=now)

    assert counts == {"simulations_deleted": 4, "scenarios_deleted": 2}
    statements = [call.args[0] for call in db.execute.call_args_list]
    compiled = [s.compile(dialect=postgresql.dialect()) for s in statements]
    assert [str(c).split()[2] for c in compiled] == ["simulation_results", "scenarios"]
    assert all("created_at <" in str(c) for c in compiled)
    assert {c.params["created_at_1"] for c in compiled} == {datetime(2026, 11, 16, 12, tzinfo=UTC)}
    db.commit.assert_called_once()
