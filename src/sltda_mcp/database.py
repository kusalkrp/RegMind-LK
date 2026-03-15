import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from sltda_mcp.config import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    settings = get_settings()
    # asyncpg expects postgresql:// not postgresql+asyncpg://
    url = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://")
    _pool = await asyncpg.create_pool(
        dsn=url,
        min_size=5,
        max_size=15,
        max_inactive_connection_lifetime=300,
        command_timeout=10,
    )
    logger.info("PostgreSQL connection pool initialised (min=5, max=15)")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def pool_stats() -> dict[str, int]:
    pool = get_pool()
    return {
        "pool_size": pool.get_size(),
        "pool_free": pool.get_idle_size(),
        "pool_used": pool.get_size() - pool.get_idle_size(),
    }
