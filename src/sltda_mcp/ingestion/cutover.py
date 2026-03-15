"""
Atomic Blue/Green Cutover.
Issue #16 mitigation: Qdrant alias reassigned FIRST; PostgreSQL rename
only proceeds if Qdrant succeeds. On PG failure, Qdrant alias is reversed.
Issue #17 mitigation: rollback_available flag set for 48-hour window.
"""

import logging
from datetime import datetime, timezone

import asyncpg

from sltda_mcp.exceptions import CutoverError
from sltda_mcp.qdrant_client import reassign_alias

logger = logging.getLogger(__name__)

LIVE_ALIAS = "sltda_documents"
STAGING_COLLECTION = "sltda_documents_next"
PREVIOUS_COLLECTION = "sltda_documents_prev"

# Tables that participate in the blue/green swap
_SWAP_TABLES = [
    "documents",
    "registration_steps",
    "business_categories",
    "financial_concessions",
    "niche_toolkits",
    "document_sections",
    "tourism_statistics",
    "format_review_queue",
]


async def _set_cutover_status(conn: asyncpg.Connection, status: str) -> None:
    await conn.execute(
        "UPDATE system_metadata SET cutover_status = $1, updated_at = NOW()", status
    )


async def execute_cutover(conn: asyncpg.Connection) -> None:
    """
    Perform atomic blue/green cutover.

    Order (Issue #16):
    1. Qdrant alias  → sltda_documents_next
    2. PG rename transaction (all tables in one BEGIN/COMMIT)
    3. Update system_metadata: complete + rollback window
    """
    logger.info("Cutover: step 1 — reassigning Qdrant alias")
    try:
        await reassign_alias(LIVE_ALIAS, STAGING_COLLECTION)
    except Exception as exc:
        raise CutoverError(f"Qdrant alias reassignment failed: {exc}") from exc

    await _set_cutover_status(conn, "qdrant_done")
    logger.info("Cutover: Qdrant alias done — starting PG rename transaction")

    rename_stmts = []
    for table in _SWAP_TABLES:
        # current live → _old (preserve for 48h rollback)
        rename_stmts.append(
            f"ALTER TABLE IF EXISTS {table} RENAME TO {table}_old;"
        )
        # staging → live name
        rename_stmts.append(
            f"ALTER TABLE IF EXISTS {table}_staging RENAME TO {table};"
        )

    metadata_update = """
        UPDATE system_metadata SET
            active_qdrant_collection = $1,
            cutover_status = 'postgres_done',
            last_refresh_at = NOW(),
            rollback_available = TRUE,
            rollback_expires_at = NOW() + INTERVAL '48 hours',
            updated_at = NOW();
    """

    try:
        async with conn.transaction():
            for stmt in rename_stmts:
                await conn.execute(stmt)
            await conn.execute(metadata_update, STAGING_COLLECTION)
    except Exception as exc:
        # PG failed — try to reverse the Qdrant alias
        logger.error("PG rename failed; attempting Qdrant alias rollback: %s", exc)
        try:
            await reassign_alias(LIVE_ALIAS, PREVIOUS_COLLECTION)
            logger.info("Qdrant alias reverted to %s", PREVIOUS_COLLECTION)
        except Exception as revert_exc:
            logger.critical(
                "MANUAL INTERVENTION REQUIRED — Qdrant alias revert also failed: %s",
                revert_exc,
            )
        raise CutoverError(f"PostgreSQL rename transaction failed: {exc}") from exc

    await _set_cutover_status(conn, "complete")
    logger.info("Cutover complete. Rollback window open for 48 hours.")

    # Invalidate in-process RAG caches so next requests see fresh data
    try:
        from sltda_mcp.mcp_server.rag import invalidate_rag_cache
        invalidate_rag_cache()
    except Exception:
        pass  # mcp_server may not be running in ingestion container


async def execute_rollback(conn: asyncpg.Connection) -> None:
    """
    Reverse a completed cutover.
    Restores Qdrant alias to PREVIOUS_COLLECTION, renames PG tables back.
    """
    logger.warning("Rolling back cutover — restoring previous data set")

    try:
        await reassign_alias(LIVE_ALIAS, PREVIOUS_COLLECTION)
    except Exception as exc:
        raise CutoverError(f"Qdrant alias rollback failed: {exc}") from exc

    restore_stmts = []
    for table in _SWAP_TABLES:
        # live → staging (will be overwritten next run)
        restore_stmts.append(
            f"ALTER TABLE IF EXISTS {table} RENAME TO {table}_staging;"
        )
        # _old → live
        restore_stmts.append(
            f"ALTER TABLE IF EXISTS {table}_old RENAME TO {table};"
        )

    try:
        async with conn.transaction():
            for stmt in restore_stmts:
                await conn.execute(stmt)
            await conn.execute(
                """UPDATE system_metadata SET
                       active_qdrant_collection = $1,
                       cutover_status = 'none',
                       rollback_available = FALSE,
                       updated_at = NOW()""",
                PREVIOUS_COLLECTION,
            )
    except Exception as exc:
        raise CutoverError(f"PostgreSQL rollback transaction failed: {exc}") from exc

    logger.info("Rollback complete. Active collection: %s", PREVIOUS_COLLECTION)


async def cleanup_old_data(conn: asyncpg.Connection) -> None:
    """
    Drop *_old tables after the 48-hour rollback window has expired.
    Called by a scheduled job — checks rollback_expires_at before acting.
    """
    row = await conn.fetchrow(
        "SELECT rollback_available, rollback_expires_at FROM system_metadata LIMIT 1"
    )
    if not row or not row["rollback_available"]:
        logger.debug("cleanup_old_data: no active rollback window")
        return

    expires_at: datetime = row["rollback_expires_at"]
    now = datetime.now(timezone.utc)
    # Make expires_at timezone-aware if it isn't
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now < expires_at:
        logger.info(
            "cleanup_old_data: rollback window still open until %s — skipping",
            expires_at.isoformat(),
        )
        return

    logger.info("cleanup_old_data: rollback window expired — dropping *_old tables")
    for table in _SWAP_TABLES:
        await conn.execute(f"DROP TABLE IF EXISTS {table}_old CASCADE")
    await conn.execute(
        "UPDATE system_metadata SET rollback_available = FALSE, updated_at = NOW()"
    )
    logger.info("Old tables dropped. Rollback window closed.")
