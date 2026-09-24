"""A completed run's recommendations are computed once, then served from Redis.

Each computation reruns the cascade once per candidate inside the request, and
several frontend panels ask for the same run at once. The cache must return
exactly what a direct computation would (limit slicing included), and must
degrade to a direct computation when Redis is unavailable.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import recommendation_cache as cache
from app.services.recommendations import MitigationRecommendation


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: dict[str, str] = {}
        self.fail = fail
        self.setex_ttls: list[int] = []

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        if self.fail:
            raise ConnectionError("redis down")
        self.setex_ttls.append(ttl)
        self.store[key] = value


def _rec(rank: int) -> MitigationRecommendation:
    node = uuid.uuid4()
    return MitigationRecommendation(
        rank=rank,
        node_id=node,
        node_name=f"Node {rank}",
        failures_prevented=10 - rank,
        raw_population_saved=100,
        efficiency_gain=0.01,
        scenario_payload={"network_id": str(uuid.uuid4()), "modifications": [], "initial_failures": []},
    )


@pytest.fixture
def ranked(monkeypatch):
    """Stand-in engine that records how often it is actually invoked."""
    calls: list[int] = []
    full = [_rec(i + 1) for i in range(12)]

    def fake_get_recommendations(simulation, db, limit):
        calls.append(limit)
        return full[:limit]

    monkeypatch.setattr(cache, "get_recommendations", fake_get_recommendations)
    return SimpleNamespace(calls=calls, full=full)


def test_second_request_is_served_from_the_cache(monkeypatch, ranked):
    redis = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis_client", lambda: redis)
    sim = SimpleNamespace(id=uuid.uuid4())

    first = cache.cached_recommendations(sim, db=None, limit=5)
    second = cache.cached_recommendations(sim, db=None, limit=5)

    assert ranked.calls == [cache.MAX_RECOMMENDATIONS], "the engine ran more than once"
    assert [r.model_dump(mode="json") for r in second] == [r.model_dump(mode="json") for r in first]
    assert redis.setex_ttls == [settings.recommendation_cache_ttl_seconds]


def test_every_limit_is_a_prefix_of_one_cached_ranking(monkeypatch, ranked):
    redis = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis_client", lambda: redis)
    sim = SimpleNamespace(id=uuid.uuid4())

    top10 = cache.cached_recommendations(sim, db=None, limit=10)
    top1 = cache.cached_recommendations(sim, db=None, limit=1)

    assert len(top10) == 10 and len(top1) == 1
    assert top1[0].node_id == top10[0].node_id == ranked.full[0].node_id
    assert [r.rank for r in top10] == list(range(1, 11))
    assert len(ranked.calls) == 1


def test_redis_outage_falls_back_to_direct_computation(monkeypatch, ranked):
    monkeypatch.setattr(cache, "get_redis_client", lambda: _FakeRedis(fail=True))
    sim = SimpleNamespace(id=uuid.uuid4())

    recs = cache.cached_recommendations(sim, db=None, limit=3)

    assert [r.rank for r in recs] == [1, 2, 3]


def test_cache_key_changes_with_every_result_affecting_setting(monkeypatch):
    sim_id = uuid.uuid4()
    base = cache.cache_key(sim_id)
    for name, value in [
        ("enforce_edge_semantics", not settings.enforce_edge_semantics),
        ("max_cascade_waves", settings.max_cascade_waves + 1),
        ("max_scenario_modifications", settings.max_scenario_modifications + 1),
    ]:
        with monkeypatch.context() as m:
            m.setattr(settings, name, value)
            assert cache.cache_key(sim_id) != base, f"{name} is not part of the cache key"


def test_zero_ttl_disables_the_cache(monkeypatch, ranked):
    redis = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis_client", lambda: redis)
    monkeypatch.setattr(settings, "recommendation_cache_ttl_seconds", 0)
    sim = SimpleNamespace(id=uuid.uuid4())

    cache.cached_recommendations(sim, db=None, limit=2)
    cache.cached_recommendations(sim, db=None, limit=2)

    assert len(ranked.calls) == 2
    assert not redis.store
