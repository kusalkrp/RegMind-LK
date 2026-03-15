"""
Unit tests for mcp_server/tools/niche.py.
DB and RAG fully mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult
from sltda_mcp.mcp_server.tools.niche import get_niche_categories, get_niche_toolkit


def _mock_acquire(rows=None, row=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_rag_result(answer="RAG answer.") -> RagResult:
    return RagResult(
        answer=answer,
        confidence="medium",
        chunks=[RagChunk("excerpt", "doc1", "Eco Toolkit", "https://url", 0, 0.80)],
        synthesis_used=True,
        query_expanded=False,
    )


_SAMPLE_TOOLKIT = {
    "toolkit_code": "ECO_TOURISM",
    "toolkit_name": "Eco Tourism Toolkit",
    "target_market": "Nature enthusiasts",
    "key_activities": ["bird watching", "trekking"],
    "regulatory_notes": "Forest permit required.",
    "summary": "Sri Lanka eco tourism regulatory summary.",
    "source_text_tokens": 1200,
    "source_pages": 10,
    "extraction_confidence": "high",
    "source_url": "https://sltda.gov.lk/eco.pdf",
}


class TestGetNicheCategories:
    @pytest.mark.asyncio
    async def test_returns_all_categories(self):
        rows = [
            {"toolkit_code": "ECO", "toolkit_name": "Eco Tourism", "target_market": "Nature", "extraction_confidence": "high"},
            {"toolkit_code": "MICE", "toolkit_name": "MICE Tourism", "target_market": "Business", "extraction_confidence": "high"},
        ]
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(rows=rows)):
            result = await get_niche_categories()

        assert result["status"] == "success"
        assert result["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_filter_applied(self):
        rows = [{"toolkit_code": "ECO", "toolkit_name": "Eco Tourism", "target_market": "Nature", "extraction_confidence": "high"}]
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(rows=rows)):
            result = await get_niche_categories(filter="eco")

        assert result["status"] == "success"
        assert result["data"]["filter"] == "eco"

    @pytest.mark.asyncio
    async def test_no_results_returns_not_found(self):
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(rows=[])):
            result = await get_niche_categories(filter="nonexistent_xyz")

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_envelope_fields_present(self):
        rows = [{"toolkit_code": "X", "toolkit_name": "X", "target_market": "Y", "extraction_confidence": "low"}]
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(rows=rows)):
            result = await get_niche_categories()

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result


class TestGetNicheToolkit:
    @pytest.mark.asyncio
    async def test_summary_mode_db_only(self):
        """Summary mode must NOT call run_rag."""
        with (
            patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(row=_SAMPLE_TOOLKIT)),
            patch("sltda_mcp.mcp_server.tools.niche.run_rag", new_callable=AsyncMock) as mock_rag,
        ):
            result = await get_niche_toolkit("ECO_TOURISM", detail_level="summary")

        mock_rag.assert_not_called()
        assert result["status"] == "success"
        assert result["data"]["detail_level"] == "summary"

    @pytest.mark.asyncio
    async def test_full_mode_calls_rag(self):
        """Full mode must call run_rag exactly once."""
        with (
            patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(row=_SAMPLE_TOOLKIT)),
            patch("sltda_mcp.mcp_server.tools.niche.run_rag",
                  new_callable=AsyncMock, return_value=_make_rag_result()) as mock_rag,
        ):
            result = await get_niche_toolkit("ECO_TOURISM", detail_level="full")

        mock_rag.assert_called_once()
        assert "rag_answer" in result["data"]

    @pytest.mark.asyncio
    async def test_extraction_confidence_in_response(self):
        """Issue #8: extraction_confidence must always be present."""
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(row=_SAMPLE_TOOLKIT)):
            result = await get_niche_toolkit("ECO_TOURISM")

        assert result["data"]["extraction_confidence"] == "high"
        assert result["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_unknown_category_returns_not_found(self):
        with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(row=None)):
            result = await get_niche_toolkit("UNKNOWN_XYZ")

        assert result["status"] == "not_found"
