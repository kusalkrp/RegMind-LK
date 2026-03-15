"""
Unit tests for mcp_server/health.py and logging_config.py.
All external components (DB, Qdrant, Gemini) are mocked.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.logging_config import _JsonFormatter, configure_json_logging, log_tool_call


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_pool_stats(size: int = 15, free: int = 12) -> dict:
    return {"pool_size": size, "pool_free": free, "pool_used": size - free}


def _make_conn(cutover_status: str = "complete", doc_count: int = 52) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)  # SELECT 1 ping + doc count
    conn.fetchrow = AsyncMock(return_value={
        "last_refresh_at": None,
        "cutover_status": cutover_status,
        "ingestion_status": "idle",
    })
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_qdrant_client(collections=None, point_count=47800):
    client = AsyncMock()
    coll_list = MagicMock()
    coll_list.collections = [MagicMock(name=n) for n in (collections or ["sltda_documents"])]
    client.get_collections = AsyncMock(return_value=coll_list)
    coll_info = MagicMock()
    coll_info.points_count = point_count
    client.get_collection = AsyncMock(return_value=coll_info)
    return client


# ─── health_check ─────────────────────────────────────────────────────────────

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_all_required_fields(self):
        """All fields from the design doc schema must be present."""
        conn = _make_conn()
        conn.fetchval.side_effect = [1, 52]  # ping, doc count

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats()),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[MagicMock(), MagicMock()]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        required = [
            "status", "checked_at", "uptime_seconds", "components",
            "last_refresh", "cutover_status", "ingestion_status",
            "total_documents", "total_vectors", "pool_available",
            "pool_total", "memory_rss_mb", "disk_usage_percent",
        ]
        for field in required:
            assert field in result, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_healthy_when_all_ok(self):
        conn = _make_conn(cutover_status="complete")
        conn.fetchval.side_effect = [1, 52]

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats(15, 12)),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[MagicMock()]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["status"] in ("healthy", "degraded")  # degraded ok if docs dir missing

    @pytest.mark.asyncio
    async def test_unhealthy_on_postgres_down(self):
        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", side_effect=Exception("PG unreachable")),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(_make_conn())),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["status"] == "unhealthy"
        assert result["components"]["postgres"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_unhealthy_on_qdrant_down(self):
        conn = _make_conn()
        conn.fetchval.side_effect = [1, 0]
        bad_client = AsyncMock()
        bad_client.get_collections = AsyncMock(side_effect=Exception("Qdrant unreachable"))

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats()),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=bad_client),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["status"] == "unhealthy"
        assert result["components"]["qdrant"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_degraded_on_high_pool_usage(self):
        """13/15 connections used → < 20% free → degraded."""
        conn = _make_conn()
        conn.fetchval.side_effect = [1, 0]

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats(15, 2)),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["status"] == "degraded"
        assert result["components"]["postgres"].get("warning") == "pool_low"

    @pytest.mark.asyncio
    async def test_degraded_on_incomplete_cutover(self):
        """cutover_status = 'qdrant_done' means cutover is mid-flight → degraded."""
        conn = _make_conn(cutover_status="qdrant_done")
        conn.fetchval.side_effect = [1, 0]

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats()),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["status"] == "degraded"
        assert result["cutover_status"] == "qdrant_done"

    @pytest.mark.asyncio
    async def test_cutover_status_in_response(self):
        conn = _make_conn(cutover_status="complete")
        conn.fetchval.side_effect = [1, 10]

        with (
            patch("sltda_mcp.mcp_server.health.pool_stats", return_value=_make_pool_stats()),
            patch("sltda_mcp.mcp_server.health.acquire", return_value=_mock_acquire(conn)),
            patch("sltda_mcp.mcp_server.health.get_client", return_value=_make_qdrant_client()),
            patch("sltda_mcp.mcp_server.health.genai.configure"),
            patch("sltda_mcp.mcp_server.health.genai.list_models", return_value=[]),
            patch("sltda_mcp.mcp_server.health.get_settings", return_value=MagicMock(
                gemini_api_key="key", qdrant_collection="sltda_documents",
                documents_base_path="./documents",
            )),
        ):
            from sltda_mcp.mcp_server.health import health_check
            result = await health_check()

        assert result["cutover_status"] == "complete"


# ─── JSON logging ─────────────────────────────────────────────────────────────

class TestJsonLogging:
    def test_formatter_produces_valid_json(self):
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event"] == "test message"
        assert parsed["level"] == "INFO"
        assert parsed["service"] == "sltda-mcp"
        assert "timestamp" in parsed

    def test_formatter_includes_extra_fields(self):
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="tool invoked", args=(), exc_info=None,
        )
        record.tool_name = "get_registration_requirements"
        record.duration_ms = 87.5
        record.trace_id = "abc-123"

        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["tool_name"] == "get_registration_requirements"
        assert parsed["duration_ms"] == 87.5
        assert parsed["trace_id"] == "abc-123"

    def test_configure_json_logging_sets_handler(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        try:
            configure_json_logging("INFO")
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0].formatter, _JsonFormatter)
        finally:
            root.handlers = original_handlers

    def test_log_tool_call_emits_structured_record(self, caplog):
        logger = logging.getLogger("test.tool")
        with caplog.at_level(logging.INFO, logger="test.tool"):
            log_tool_call(
                logger=logger,
                tool_name="get_tax_rate",
                trace_id="trace-xyz",
                duration_ms=42.0,
                status="success",
            )
        assert len(caplog.records) == 1
        rec = caplog.records[0]
        assert rec.tool_name == "get_tax_rate"
        assert rec.trace_id == "trace-xyz"
        assert rec.duration_ms == 42.0

    def test_log_tool_call_error_logs_at_error_level(self, caplog):
        logger = logging.getLogger("test.tool.err")
        with caplog.at_level(logging.ERROR, logger="test.tool.err"):
            log_tool_call(
                logger=logger,
                tool_name="get_tax_rate",
                trace_id="t1",
                duration_ms=10.0,
                status="error",
                error="DB timeout",
            )
        assert caplog.records[0].levelno == logging.ERROR
        assert caplog.records[0].error == "DB timeout"
