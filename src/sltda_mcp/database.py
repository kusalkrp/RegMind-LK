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
    url = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://")

    # Fail fast if default password is still in use
    if "changeme" in url.lower():
        raise RuntimeError(
            "POSTGRES_PASSWORD is set to the default 'changeme'. "
            "Set a strong password before starting the server."
        )

    _pool = await asyncpg.create_pool(
        dsn=url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        max_inactive_connection_lifetime=300,
        command_timeout=10,
        statement_cache_size=settings.db_statement_cache_size,
    )
    logger.info(
        "PostgreSQL pool initialised (min=%d, max=%d, stmt_cache=%d)",
        settings.db_pool_min_size,
        settings.db_pool_max_size,
        settings.db_statement_cache_size,
    )


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
