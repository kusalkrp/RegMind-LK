"""
Unit tests for ingestion/cutover.py.
All DB and Qdrant calls are mocked.
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from sltda_mcp.exceptions import CutoverError
from sltda_mcp.ingestion.cutover import (
    LIVE_ALIAS,
    PREVIOUS_COLLECTION,
    STAGING_COLLECTION,
    _SWAP_TABLES,
    cleanup_old_data,
    execute_cutover,
    execute_rollback,
)


def make_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=None),
        __aexit__=AsyncMock(return_value=False),
    ))
    return conn


# ─── execute_cutover ──────────────────────────────────────────────────────────

class TestExecuteCutover:
    @pytest.mark.asyncio
    async def test_qdrant_alias_reassigned_before_postgres(self):
        """Qdrant reassign_alias must be called before any PG execute."""
        conn = make_conn()
        call_order = []

        async def mock_reassign(alias, target):
            call_order.append("qdrant")

        conn.execute.side_effect = lambda *a, **kw: call_order.append("pg") or AsyncMock()()

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", side_effect=mock_reassign):
            await execute_cutover(conn)

        assert call_order[0] == "qdrant", "Qdrant must be called first"
        assert "pg" in call_order, "PG must also be called"

    @pytest.mark.asyncio
    async def test_postgres_transaction_never_attempted_on_qdrant_failure(self):
        """If Qdrant raises, PG transaction must not be touched."""
        conn = make_conn()

        async def failing_reassign(alias, target):
            raise RuntimeError("Qdrant unreachable")

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", side_effect=failing_reassign):
            with pytest.raises(CutoverError, match="Qdrant alias reassignment failed"):
                await execute_cutover(conn)

        conn.transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_cutover_status_progression(self):
        """Status must progress: (implicit none) → qdrant_done → postgres_done → complete."""
        conn = make_conn()
        statuses: list[str] = []

        original_execute = conn.execute

        async def capture_execute(sql, *args, **kwargs):
            if "cutover_status" in sql and "UPDATE system_metadata" in sql:
                statuses.append(args[0])

        conn.execute.side_effect = capture_execute

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", new_callable=AsyncMock):
            await execute_cutover(conn)

        assert "qdrant_done" in statuses
        assert "complete" in statuses
        # qdrant_done must precede complete
        assert statuses.index("qdrant_done") < statuses.index("complete")

    @pytest.mark.asyncio
    async def test_all_swap_tables_renamed(self):
        """Every table in _SWAP_TABLES must appear in RENAME statements."""
        conn = make_conn()
        executed_sql: list[str] = []

        async def capture(sql, *args, **kwargs):
            executed_sql.append(sql)

        conn.execute.side_effect = capture

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", new_callable=AsyncMock):
            await execute_cutover(conn)

        rename_sql = " ".join(executed_sql)
        for table in _SWAP_TABLES:
            assert table in rename_sql, f"Table '{table}' missing from rename statements"

    @pytest.mark.asyncio
    async def test_pg_failure_attempts_qdrant_revert(self):
        """If PG transaction fails, cutover tries to revert the Qdrant alias."""
        conn = make_conn()
        reassign_calls: list[tuple] = []

        async def track_reassign(alias, target):
            reassign_calls.append((alias, target))

        # Make the PG transaction fail
        conn.transaction.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("PG boom"))

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", side_effect=track_reassign):
            with pytest.raises(CutoverError):
                await execute_cutover(conn)

        # First call: forward reassign; second call: revert attempt
        assert len(reassign_calls) >= 2
        # Revert must point to PREVIOUS_COLLECTION
        revert_targets = [t for _, t in reassign_calls[1:]]
        assert PREVIOUS_COLLECTION in revert_targets


# ─── execute_rollback ─────────────────────────────────────────────────────────

class TestExecuteRollback:
    @pytest.mark.asyncio
    async def test_rollback_reverses_both_systems(self):
        """Rollback reverses Qdrant alias AND PG table names."""
        conn = make_conn()
        reassign_args: list[tuple] = []

        async def track_reassign(alias, target):
            reassign_args.append((alias, target))

        executed_sql: list[str] = []
        conn.execute.side_effect = lambda sql, *a, **kw: executed_sql.append(sql) or AsyncMock()()

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", side_effect=track_reassign):
            await execute_rollback(conn)

        # Qdrant must point back to previous collection
        assert any(t == PREVIOUS_COLLECTION for _, t in reassign_args)

        # PG must rename documents → documents_staging and documents_old → documents
        combined = " ".join(executed_sql)
        assert "documents_staging" in combined
        assert "documents_old" in combined

    @pytest.mark.asyncio
    async def test_rollback_resets_metadata_flags(self):
        """rollback_available = FALSE and cutover_status = 'none' after rollback."""
        conn = make_conn()
        executed_sql: list[str] = []

        async def capture(sql, *args, **kwargs):
            executed_sql.append(sql)

        conn.execute.side_effect = capture

        with patch("sltda_mcp.ingestion.cutover.reassign_alias", new_callable=AsyncMock):
            await execute_rollback(conn)

        metadata_sql = " ".join(executed_sql)
        assert "rollback_available = FALSE" in metadata_sql
        assert "cutover_status = 'none'" in metadata_sql

    @pytest.mark.asyncio
    async def test_rollback_raises_on_qdrant_failure(self):
        async def failing(alias, target):
            raise RuntimeError("Qdrant down")

        conn = make_conn()
        with patch("sltda_mcp.ingestion.cutover.reassign_alias", side_effect=failing):
            with pytest.raises(CutoverError, match="Qdrant alias rollback failed"):
                await execute_rollback(conn)

        conn.transaction.assert_not_called()


# ─── cleanup_old_data ─────────────────────────────────────────────────────────

class TestCleanupOldData:
    @pytest.mark.asyncio
    async def test_no_cleanup_before_rollback_window_expires(self):
        """If rollback_expires_at is in the future, DROP TABLE must NOT be called."""
        conn = make_conn()
        future_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        conn.fetchrow.return_value = {
            "rollback_available": True,
            "rollback_expires_at": future_expiry,
        }

        await cleanup_old_data(conn)

        drop_calls = [str(c) for c in conn.execute.call_args_list if "DROP" in str(c)]
        assert len(drop_calls) == 0

    @pytest.mark.asyncio
    async def test_cleanup_drops_old_tables_after_window(self):
        """After rollback_expires_at, all *_old tables must be dropped."""
        conn = make_conn()
        past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        conn.fetchrow.return_value = {
            "rollback_available": True,
            "rollback_expires_at": past_expiry,
        }

        executed_sql: list[str] = []
        conn.execute.side_effect = lambda sql, *a, **kw: executed_sql.append(sql) or AsyncMock()()

        await cleanup_old_data(conn)

        drop_stmts = [s for s in executed_sql if "DROP TABLE" in s]
        assert len(drop_stmts) == len(_SWAP_TABLES)
        for table in _SWAP_TABLES:
            assert any(f"{table}_old" in s for s in drop_stmts), \
                f"DROP TABLE for {table}_old not found"

    @pytest.mark.asyncio
    async def test_cleanup_skipped_when_no_rollback_window(self):
        """rollback_available=False → no DROP TABLE."""
        conn = make_conn()
        conn.fetchrow.return_value = {
            "rollback_available": False,
            "rollback_expires_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }

        await cleanup_old_data(conn)

        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_skipped_when_no_metadata_row(self):
        """Missing system_metadata row → no DROP TABLE."""
        conn = make_conn()
        conn.fetchrow.return_value = None

        await cleanup_old_data(conn)

        conn.execute.assert_not_called()
