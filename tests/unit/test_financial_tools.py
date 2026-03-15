"""
Unit tests for mcp_server/tools/financial.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.tools.financial import (
    get_financial_concessions,
    get_tax_rate,
    get_tdl_information,
)


def _mock_conn(fetch=None, fetchrow=None, fetchval=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetchval = AsyncMock(return_value=fetchval)
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── get_financial_concessions ────────────────────────────────────────────────

class TestGetFinancialConcessions:
    @pytest.mark.asyncio
    async def test_returns_concessions_no_filter(self):
        rows = [
            {
                "concession_name": "Low-interest tourism loan",
                "concession_type": "banking",
                "applicable_to": ["hotel", "guest_house"],
                "rate_or_terms": "6% per annum",
                "conditions": "New construction only",
                "circular_reference": "CB/2021/01",
                "circular_url": "https://sltda.gov.lk/circ.pdf",
            }
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_financial_concessions()

        assert result["status"] == "success"
        assert result["data"]["total"] == 1
        assert len(result["data"]["concessions"]) == 1

    @pytest.mark.asyncio
    async def test_no_results_returns_not_found(self):
        conn = _mock_conn(fetch=[])

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_financial_concessions(business_type="nonexistent_biz")

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_source_docs_deduplicated(self):
        """Multiple concessions with same circular_url → only one source doc."""
        rows = [
            {"concession_name": "A", "concession_type": "banking",
             "applicable_to": [], "rate_or_terms": "5%", "conditions": None,
             "circular_reference": "CB/1", "circular_url": "https://sltda.gov.lk/same.pdf"},
            {"concession_name": "B", "concession_type": "banking",
             "applicable_to": [], "rate_or_terms": "4%", "conditions": None,
             "circular_reference": "CB/1", "circular_url": "https://sltda.gov.lk/same.pdf"},
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_financial_concessions()

        assert len(result["source"]["documents"]) == 1

    @pytest.mark.asyncio
    async def test_envelope_has_required_fields(self):
        rows = [{"concession_name": "X", "concession_type": "banking",
                 "applicable_to": [], "rate_or_terms": "3%", "conditions": None,
                 "circular_reference": None, "circular_url": None}]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_financial_concessions()

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result


# ─── get_tdl_information ─────────────────────────────────────────────────────

class TestGetTdlInformation:
    @pytest.mark.asyncio
    async def test_returns_levy_records(self):
        levy_rows = [
            {"concession_name": "TDL Rate", "rate_or_terms": "1% of revenue",
             "conditions": None, "circular_reference": "TDL/01",
             "doc_url": "https://sltda.gov.lk/tdl.pdf", "document_name": "TDL Circular"}
        ]
        conn = _mock_conn(fetch=levy_rows)
        # second fetch (form_docs) returns []
        conn.fetch.side_effect = [levy_rows, []]

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_tdl_information("rate")

        assert result["status"] == "success"
        assert len(result["data"]["levy_records"]) == 1

    @pytest.mark.asyncio
    async def test_not_found_when_no_records(self):
        conn = _mock_conn()
        conn.fetch.side_effect = [[], []]

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_tdl_information()

        assert result["status"] == "not_found"


# ─── get_tax_rate ─────────────────────────────────────────────────────────────

class TestGetTaxRate:
    @pytest.mark.asyncio
    async def test_returns_tax_rates(self):
        rows = [
            {"concession_name": "Income Tax Exemption", "rate_or_terms": "5 year exemption",
             "conditions": "New hotel", "applicable_to": ["hotel"],
             "doc_url": "https://sltda.gov.lk/tax.pdf"}
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_tax_rate("hotel")

        assert result["status"] == "success"
        assert result["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_no_filter_returns_all_tax_records(self):
        rows = [
            {"concession_name": "A", "rate_or_terms": "10%", "conditions": None,
             "applicable_to": ["hotel"], "doc_url": None},
            {"concession_name": "B", "rate_or_terms": "5%", "conditions": None,
             "applicable_to": ["villa"], "doc_url": None},
        ]
        conn = _mock_conn(fetch=rows)

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_tax_rate()

        assert result["data"]["business_type_filter"] is None
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_not_found_when_no_records(self):
        conn = _mock_conn(fetch=[])

        with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
            result = await get_tax_rate("unknown_type")

        assert result["status"] == "not_found"
