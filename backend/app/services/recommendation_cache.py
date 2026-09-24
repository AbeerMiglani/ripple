"""Redis cache for a completed simulation's ranked recommendations.

Generating recommendations reruns the whole cascade once per candidate, inside
a request handler, and the answer for a completed run never changes: the run,
its network and its scenario are all immutable once written. Several panels
ask for the same run's recommendations at once, and a browser refetches on
focus. Without a cache each of those requests paid the full cost again.

The full ranked list is computed once, at the endpoint's maximum limit, and
sliced per request. Ranking is deterministic, so the first N of the top 50 are
exactly what a request for N would have computed. Every setting that changes
the result is part of the key, so a reconfigured deployment never serves an
answer computed under different rules.

The cache is an optimization only. If Redis is unavailable, recommendations
are computed directly, the same way the centrality cache degrades.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db.redis import get_redis_client
from app.services.recommendations import MitigationRecommendation, get_recommendations

logger = logging.getLogger(__name__)

#: The endpoint's upper bound on ``limit``; the cached list is this long.
MAX_RECOMMENDATIONS = 50

#: Bump when the cached shape or ranking rules change, so stale entries from a
#: previous deployment are ignored rather than served.
_CACHE_VERSION = 1


def cache_key(simulation_id: Any) -> str:
    return (
        f"recs:v{_CACHE_VERSION}:{simulation_id}"
        f":sem{int(settings.enforce_edge_semantics)}"
        f":waves{settings.max_cascade_waves}"
        f":mods{settings.max_scenario_modifications}"
    )


def cached_recommendations(
    simulation: Any,
    db: Session,
    limit: int,
) -> list[MitigationRecommendation]:
    """``get_recommendations`` for a completed run, memoized in Redis."""
    ttl = settings.recommendation_cache_ttl_seconds
    key = cache_key(simulation.id)

    if ttl:
        try:
            cached = get_redis_client().get(key)
            if cached:
                rows = json.loads(str(cached))
                return [MitigationRecommendation.model_validate(row) for row in rows[:limit]]
        except Exception:
            logger.warning("recommendation cache read failed for %s", simulation.id, exc_info=True)

    ranked = get_recommendations(simulation=simulation, db=db, limit=MAX_RECOMMENDATIONS)

    if ttl:
        try:
            payload = json.dumps([rec.model_dump(mode="json") for rec in ranked])
            get_redis_client().setex(key, ttl, payload)
        except Exception:
            logger.warning("recommendation cache write failed for %s", simulation.id, exc_info=True)

    return ranked[:limit]
