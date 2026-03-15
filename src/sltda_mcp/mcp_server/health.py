"""
Health check endpoint — GET /health.
Returns DB pool stats, Qdrant status, and last pipeline refresh time.
"""

import logging
from datetime import datetime, timezone

from sltda_mcp.database import acquire, pool_stats
from sltda_mcp.qdrant_client import get_client

logger = logging.getLogger(__name__)


async def health_check() -> dict:
    """Return service health. Used by load balancers and monitoring."""
    status: dict = {
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": {},
    }

    # PostgreSQL
    try:
        stats = await pool_stats()
        async with acquire() as conn:
            await conn.fetchval("SELECT 1")
        status["components"]["postgres"] = {"status": "ok", **stats}
    except Exception as exc:
        status["status"] = "degraded"
        status["components"]["postgres"] = {"status": "error", "error": str(exc)}

    # Qdrant
    try:
        client = get_client()
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        status["components"]["qdrant"] = {
            "status": "ok",
            "collections": names,
        }
    except Exception as exc:
        status["status"] = "degraded"
        status["components"]["qdrant"] = {"status": "error", "error": str(exc)}

    # Last refresh
    try:
        async with acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_refresh_at, cutover_status FROM system_metadata LIMIT 1"
            )
        if row:
            status["last_refresh_at"] = (
                row["last_refresh_at"].isoformat() if row["last_refresh_at"] else None
            )
            status["cutover_status"] = row["cutover_status"]
    except Exception:
        pass  # non-critical

    return status
