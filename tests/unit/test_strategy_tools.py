"""
Unit tests for mcp_server/tools/strategy.py.
RAG pipeline fully mocked.
"""

from unittest.mock import AsyncMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult
from sltda_mcp.mcp_server.tools.strategy import (
    get_strategic_plan,
    get_tourism_act_provisions,
)


def _make_rag_result(
    answer: str = "Strategic answer.",
    confidence: str = "high",
    chunks: list | None = None,
) -> RagResult:
    if chunks is None:
        chunks = [
            RagChunk(
                chunk_text="Tourism targets for 2025 include 4 million arrivals.",
                document_id="doc-1",
                document_name="Strategic Plan 2022-2025",
                source_url="https://sltda.gov.lk/strategic_plan.pdf",
                chunk_index=0,
                score=0.91,
                section_name="Strategic Plans",
                page_numbers=[5],
            )
        ]
    return RagResult(
        answer=answer,
        confidence=confidence,
        chunks=chunks,
        synthesis_used=True,
        query_expanded=False,
    )


class TestGetStrategicPlan:
    @pytest.mark.asyncio
    async def test_happy_path_returns_answer(self):
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=_make_rag_result()):
            result = await get_strategic_plan("What are the tourism targets for 2025?")

        assert result["status"] == "success"
        assert result["tool"] == "get_strategic_plan"
        assert result["data"]["answer"] == "Strategic answer."
        assert result["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_no_chunks_returns_not_found(self):
        empty = _make_rag_result(answer="Not found in available documents.", chunks=[])
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=empty):
            result = await get_strategic_plan("obscure query xyz")

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_envelope_has_required_fields(self):
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=_make_rag_result()):
            result = await get_strategic_plan("strategic goals")

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result

    @pytest.mark.asyncio
    async def test_source_excerpts_in_data(self):
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=_make_rag_result()):
            result = await get_strategic_plan("query")

        assert "source_excerpts" in result["data"]
        assert len(result["data"]["source_excerpts"]) == 1

    @pytest.mark.asyncio
    async def test_synthesis_used_flag_present(self):
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=_make_rag_result()):
            result = await get_strategic_plan("query")

        assert result["data"]["synthesis_used"] is True


class TestGetTourismActProvisions:
    @pytest.mark.asyncio
    async def test_happy_path_returns_answer(self):
        rag = _make_rag_result(answer="Section 12 of the Tourism Act states...")
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=rag):
            result = await get_tourism_act_provisions("registration penalties")

        assert result["status"] == "success"
        assert result["tool"] == "get_tourism_act_provisions"

    @pytest.mark.asyncio
    async def test_legal_disclaimer_added(self):
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=_make_rag_result()):
            result = await get_tourism_act_provisions("offences")

        assert "legal advice" in result["disclaimer"].lower() or \
               "attorney" in result["disclaimer"].lower()

    @pytest.mark.asyncio
    async def test_sections_cited_extracted(self):
        chunk = RagChunk(
            chunk_text="Section 24 — Penalties.",
            document_id="doc-act",
            document_name="Tourism Act No. 38",
            source_url="https://sltda.gov.lk/act.pdf",
            chunk_index=0,
            score=0.88,
            page_numbers=[24],
        )
        rag = _make_rag_result(chunks=[chunk])
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=rag):
            result = await get_tourism_act_provisions("penalties")

        assert result["data"]["sections_cited"] == ["p.24"]

    @pytest.mark.asyncio
    async def test_not_found_on_empty_chunks(self):
        empty = _make_rag_result(chunks=[])
        with patch("sltda_mcp.mcp_server.tools.strategy.run_rag",
                   new_callable=AsyncMock, return_value=empty):
            result = await get_tourism_act_provisions("unknown provision xyz")

        assert result["status"] == "not_found"
