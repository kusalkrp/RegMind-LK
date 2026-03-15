"""
Health check endpoint.
Implements the full response schema from Section 10.2 of the design doc.

Status rules:
  healthy   — all components OK, pool > 20% free, cutover complete/none
  degraded  — any component warning (high pool usage, unusual cutover state,
               disk > 80%, Gemini unreachable)
  unhealthy — postgres or qdrant unreachable

Issue #19 mitigation: disk_usage_percent included; > 80% → degraded.
"""

import logging
import os
import shutil
import time
from datetime import datetime, timezone

import google.generativeai as genai

from sltda_mcp.config import get_settings
from sltda_mcp.database import acquire, pool_stats
from sltda_mcp.qdrant_client import get_client

logger = logging.getLogger(__name__)

_START_TIME = time.monotonic()

_POOL_FREE_THRESHOLD = 0.20
_DISK_WARN_THRESHOLD = 80.0
_INCOMPLETE_CUTOVER_STATES = {"qdrant_done", "postgres_done"}


async def health_check() -> dict:
    """
    Return the full health status of all server components.
    Used by load balancers, monitoring, and the server_health MCP tool.
    """
    settings = get_settings()
    overall = "healthy"
    components: dict = {}

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    pg_ok = False
    try:
        stats = await pool_stats()
        async with acquire() as conn:
            await conn.fetchval("SELECT 1")
        components["postgres"] = {"status": "connected", **stats}
        pg_ok = True

        total = stats.get("pool_size", 1)
        free = stats.get("pool_free", total)
        if total > 0 and (free / total) < _POOL_FREE_THRESHOLD:
            components["postgres"]["warning"] = "pool_low"
            overall = _degrade(overall)
    except Exception as exc:
        components["postgres"] = {"status": "error", "error": str(exc)}
        overall = "unhealthy"

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_ok = False
    total_vectors = 0
    try:
        client = get_client()
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        try:
            info = await client.get_collection(settings.qdrant_collection)
            total_vectors = info.points_count or 0
        except Exception:
            total_vectors = 0
        components["qdrant"] = {"status": "connected", "collections": names}
        qdrant_ok = True
    except Exception as exc:
        components["qdrant"] = {"status": "error", "error": str(exc)}
        overall = "unhealthy"

    # ── Gemini API ────────────────────────────────────────────────────────────
    try:
        genai.configure(api_key=settings.gemini_api_key)
        models = list(genai.list_models())
        components["gemini_api"] = {"status": "reachable", "model_count": len(models)}
    except Exception as exc:
        components["gemini_api"] = {"status": "error", "error": str(exc)}
        overall = _degrade(overall)

    # ── Document store ────────────────────────────────────────────────────────
    docs_base = settings.documents_base_path
    raw_dir = os.path.join(docs_base, "raw")
    if os.path.isdir(raw_dir):
        components["document_store"] = {"status": "ok", "raw_dir": raw_dir}
    else:
        components["document_store"] = {"status": "missing_files", "raw_dir": raw_dir}
        overall = _degrade(overall)

    # ── Disk usage (Issue #19) ────────────────────────────────────────────────
    try:
        usage = shutil.disk_usage(docs_base if os.path.exists(docs_base) else ".")
        disk_pct = (usage.used / usage.total) * 100
        components["disk"] = {
            "status": "ok" if disk_pct <= _DISK_WARN_THRESHOLD else "warning",
            "used_gb": round(usage.used / 1e9, 1),
            "total_gb": round(usage.total / 1e9, 1),
            "percent": round(disk_pct, 1),
        }
        if disk_pct > _DISK_WARN_THRESHOLD:
            overall = _degrade(overall)
    except Exception:
        components["disk"] = {"status": "unknown"}

    # ── Memory ────────────────────────────────────────────────────────────────
    memory_rss_mb = _get_rss_mb()

    # ── system_metadata ───────────────────────────────────────────────────────
    last_refresh_at = None
    cutover_status = "unknown"
    total_documents = 0
    ingestion_status = "unknown"

    if pg_ok:
        try:
            async with acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT last_refresh_at, cutover_status,
                              ingestion_status
                       FROM system_metadata LIMIT 1"""
                )
                doc_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM documents WHERE is_active = TRUE"
                )
            if row:
                last_refresh_at = (
                    row["last_refresh_at"].isoformat()
                    if row["last_refresh_at"] else None
                )
                cutover_status = row["cutover_status"] or "none"
                ingestion_status = row.get("ingestion_status") or "idle"
            total_documents = doc_count or 0

            if cutover_status in _INCOMPLETE_CUTOVER_STATES:
                overall = _degrade(overall)
        except Exception:
            pass  # non-critical metadata

    # ── Invocation stats (last 24 h) ──────────────────────────────────────────
    invocation_stats: dict = {}
    if pg_ok:
        try:
            async with acquire() as conn:
                summary = await conn.fetchrow(
                    """SELECT COUNT(*) AS total_calls,
                              COUNT(*) FILTER (WHERE result_status = 'error') AS error_calls
                       FROM tool_invocation_log
                       WHERE called_at > NOW() - INTERVAL '24 hours'"""
                )
                top_row = await conn.fetchrow(
                    """SELECT tool_name, COUNT(*) AS call_count
                       FROM tool_invocation_log
                       WHERE called_at > NOW() - INTERVAL '24 hours'
                       GROUP BY tool_name
                       ORDER BY call_count DESC
                       LIMIT 1"""
                )
            invocation_stats = {
                "last_24h_calls": int(summary["total_calls"]) if summary else 0,
                "last_24h_errors": int(summary["error_calls"]) if summary else 0,
                "top_tool": top_row["tool_name"] if top_row else None,
                "top_tool_calls": int(top_row["call_count"]) if top_row else 0,
            }
        except Exception:
            invocation_stats = {"error": "stats_unavailable"}

    # ── Assemble response ─────────────────────────────────────────────────────
    uptime = int(time.monotonic() - _START_TIME)

    return {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime,
        "components": components,
        "last_refresh": last_refresh_at,
        "cutover_status": cutover_status,
        "ingestion_status": ingestion_status,
        "total_documents": total_documents,
        "total_vectors": total_vectors,
        "pool_available": components.get("postgres", {}).get("pool_free"),
        "pool_total": components.get("postgres", {}).get("pool_size"),
        "memory_rss_mb": memory_rss_mb,
        "disk_usage_percent": components.get("disk", {}).get("percent"),
        "invocation_stats": invocation_stats,
    }


def _degrade(current: str) -> str:
    """Downgrade status: healthy → degraded, unhealthy stays unhealthy."""
    if current == "unhealthy":
        return "unhealthy"
    return "degraded"


def _get_rss_mb() -> float | None:
    """Return process RSS memory in MB. Returns None if psutil not available."""
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1e6, 1)
    except Exception:
        return None
