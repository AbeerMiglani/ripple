"""
Redis connection.

Used as:
- Celery broker (task queue)
- Celery result backend
- Pub/Sub channel for streaming simulation wave progress to WebSocket clients
"""

import redis
import redis.asyncio as async_redis

from app.config import settings

_pool = None
_async_pool = None


def get_redis_pool():
    """Lazy-initialize and return a Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)
    return _pool


def get_redis_client() -> redis.Redis:
    """Return a Redis client from the shared pool."""
    return redis.Redis(connection_pool=get_redis_pool())


def get_async_redis_client() -> async_redis.Redis:
    """Return a non-blocking Redis client for ASGI/WebSocket paths."""
    global _async_pool
    if _async_pool is None:
        _async_pool = async_redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)
    return async_redis.Redis(connection_pool=_async_pool)


def verify_redis_connection() -> bool:
    """Ping Redis to verify connectivity."""
    try:
        client = get_redis_client()
        return bool(client.ping())
    except Exception:
        return False
