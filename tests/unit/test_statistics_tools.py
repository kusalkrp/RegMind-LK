"""
Unit tests for mcp_server/tools/statistics.py.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.tools.statistics import (
    get_annual_report,
    get_latest_arrivals_report,
)


def _mock_conn(fetch=None, fetchrow=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── get_latest_arrivals_report ───────────────────────────────────────────────

class TestGetLatestArrivalsReport:
    @pytest.mark.asyncio
    async def test_happy_path_monthly(self):
        rows = [
            {
                "document_name": "Monthly Arrivals January 2024",
                "source_url": "https://sltda.gov.lk/arrivals_jan24.pdf",
                "content_as_of": date(2024, 1, 31),
                "format_family": "data_table_report",
            }
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_latest_arrivals_report("monthly")

        assert result["status"] == "success"
        assert result["data"]["latest"]["document_name"] == "Monthly Arrivals January 2024"
        assert result["data"]["report_type"] == "monthly"

    @pytest.mark.asyncio
    async def test_not_found_returns_not_found(self):
        conn = _mock_conn(fetch=[])

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_latest_arrivals_report("monthly", year=1900)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_year_filter_included_in_response(self):
        rows = [
            {"document_name": "Monthly Report 2023", "source_url": "https://sltda.gov.lk/r.pdf",
             "content_as_of": date(2023, 12, 31), "format_family": "data_table_report"}
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_latest_arrivals_report("monthly", year=2023)

        assert result["data"]["year_filter"] == 2023

    @pytest.mark.asyncio
    async def test_source_documents_populated(self):
        rows = [
            {"document_name": "Report", "source_url": "https://sltda.gov.lk/r.pdf",
             "content_as_of": None, "format_family": "data_table_report"}
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_latest_arrivals_report()

        assert len(result["source"]["documents"]) == 1
        assert result["source"]["documents"][0]["url"] == "https://sltda.gov.lk/r.pdf"

    @pytest.mark.asyncio
    async def test_envelope_has_required_fields(self):
        rows = [{"document_name": "X", "source_url": None, "content_as_of": None,
                 "format_family": "data_table_report"}]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_latest_arrivals_report()

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result


# ─── get_annual_report ────────────────────────────────────────────────────────

class TestGetAnnualReport:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        row = {
            "document_name": "SLTDA Annual Report 2022",
            "source_url": "https://sltda.gov.lk/ar2022.pdf",
            "content_as_of": date(2022, 12, 31),
            "format_family": "annual_report",
            "file_size_kb": 4200,
        }
        conn = _mock_conn(fetchrow=row)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_annual_report(2022)

        assert result["status"] == "success"
        assert result["data"]["year"] == 2022
        assert result["data"]["download_url"] == "https://sltda.gov.lk/ar2022.pdf"

    @pytest.mark.asyncio
    async def test_not_found_for_missing_year(self):
        conn = _mock_conn(fetchrow=None)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_annual_report(1990)

        assert result["status"] == "not_found"
        assert "1990" in result["data"]["message"]

    @pytest.mark.asyncio
    async def test_language_param_passed(self):
        row = {"document_name": "Annual Report 2021", "source_url": "https://sltda.gov.lk/ar21.pdf",
               "content_as_of": date(2021, 12, 31), "format_family": "annual_report",
               "file_size_kb": 3800}
        conn = _mock_conn(fetchrow=row)

        with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
            result = await get_annual_report(2021, language="english")

        assert result["data"]["language"] == "english"
