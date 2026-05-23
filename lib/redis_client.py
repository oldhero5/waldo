"""Shared sync Redis client.

Celery tasks and short-lived API handlers should reach for `get_redis()` here
rather than calling `redis.Redis.from_url(settings.redis_url)` ad-hoc — every
ad-hoc call opens a new TCP connection. This module returns a single client
backed by a process-wide connection pool.

The websocket layer in `app/ws.py` uses `redis.asyncio` and is intentionally
separate; this module is sync-only.
"""

from __future__ import annotations

import redis

from lib.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return a process-wide sync Redis client (lazy, idempotent)."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url)
    return _client
