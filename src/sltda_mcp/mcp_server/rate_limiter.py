"""
Per-caller sliding-window rate limiter.

Uses Redis if REDIS_URL is configured (required for multi-instance deployments).
Falls back to in-process collections.deque when Redis is unavailable.

Redis backend:  O(1) per check via INCR + EXPIRE (atomic sliding counter).
In-memory:      O(n) cleanup per check — suitable only for single-instance.
"""

import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60

# In-memory fallback state: caller_id+tool_name -> deque of timestamps
_in_memory_counts: dict[str, deque] = defaultdict(deque)

# Redis client (initialised lazily)
_redis = None
_redis_available: bool | None = None  # None = not yet checked


async def _get_redis():
    """Return Redis client if REDIS_URL is configured, else None."""
    global _redis, _redis_available
    if _redis_available is not None:
        return _redis if _redis_available else None

    from sltda_mcp.config import get_settings
    settings = get_settings()
    if not getattr(settings, "redis_url", ""):
        _redis_available = False
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1)
        await _redis.ping()
        _redis_available = True
        logger.info("Rate limiter using Redis backend: %s", settings.redis_url)
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — falling back to in-memory rate limiter", exc)
        _redis = None
        _redis_available = False

    return _redis if _redis_available else None


async def check_rate_limit(
    caller_id: str,
    tool_name: str,
    limit_per_minute: int,
) -> tuple[bool, int]:
    """
    Check if caller is within rate limit for the given tool.

    Returns:
        (allowed, current_count)
        allowed=False means the caller should receive HTTP 429.
    """
    redis = await _get_redis()

    if redis is not None:
        return await _check_redis(redis, caller_id, tool_name, limit_per_minute)
    return _check_in_memory(caller_id, tool_name, limit_per_minute)


async def _check_redis(redis, caller_id: str, tool_name: str, limit: int) -> tuple[bool, int]:
    key = f"rl:{caller_id}:{tool_name}"
    try:
        pipe = redis.pipeline()
        await pipe.incr(key)
        await pipe.expire(key, _WINDOW_SECONDS)
        results = await pipe.execute()
        count = int(results[0])
        return count <= limit, count
    except Exception as exc:
        logger.warning("Redis rate-limit check failed (%s) — allowing request", exc)
        return True, 0


def _check_in_memory(caller_id: str, tool_name: str, limit: int) -> tuple[bool, int]:
    key = f"{caller_id}:{tool_name}"
    now = time.monotonic()
    window_start = now - _WINDOW_SECONDS
    dq = _in_memory_counts[key]

    # Prune old timestamps
    while dq and dq[0] < window_start:
        dq.popleft()

    dq.append(now)
    return len(dq) <= limit, len(dq)
