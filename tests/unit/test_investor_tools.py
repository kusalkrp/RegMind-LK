"""
Unit tests for mcp_server/tools/investor.py.
DB and RAG fully mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult
from sltda_mcp.mcp_server.tools.investor import (
    get_investment_process,
    search_sltda_resources,
)


def _mock_acquire(rows=None, second_rows=None):
    """Return a mock acquire() context manager.

    If second_rows is provided, the first fetch() call returns `rows`
    and the second returns `second_rows` (simulates two fetch calls).
    """
    conn = AsyncMock()
    if second_rows is not None:
        conn.fetch = AsyncMock(side_effect=[rows or [], second_rows])
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_rag_result(
    answer: str = "RAG answer.",
    confidence: str = "high",
    chunks: list | None = None,
    synthesis_used: bool = True,
    query_expanded: bool = False,
) -> RagResult:
    if chunks is None:
        chunks = [
            RagChunk(
                chunk_text="Investor unit contact: +94-11-2345678.",
                document_id="doc-inv",
                document_name="SLTDA Investor Guide",
                source_url="https://sltda.gov.lk/invest.pdf",
                chunk_index=0,
                score=0.92,
            )
        ]
    return RagResult(
        answer=answer,
        confidence=confidence,
        chunks=chunks,
        synthesis_used=synthesis_used,
        query_expanded=query_expanded,
    )


_SAMPLE_STEPS = [
    {
        "step_number": 1,
        "step_title": "Submit Application",
        "step_description": "Submit to SLTDA investor unit",
        "required_documents": ["NIC", "Business plan"],
        "fees": "LKR 5000",
    }
]

_SAMPLE_FORM_DOCS = [
    {"document_name": "Investor Application Form", "source_url": "https://sltda.gov.lk/inv_form.pdf"}
]


class TestGetInvestmentProcess:
    @pytest.mark.asyncio
    async def test_happy_path_returns_success(self):
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(
                    rows=_SAMPLE_STEPS, second_rows=_SAMPLE_FORM_DOCS
                ),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(),
            ),
        ):
            result = await get_investment_process()

        assert result["status"] == "success"
        assert result["tool"] == "get_investment_process"

    @pytest.mark.asyncio
    async def test_structured_steps_in_data(self):
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(
                    rows=_SAMPLE_STEPS, second_rows=_SAMPLE_FORM_DOCS
                ),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(),
            ),
        ):
            result = await get_investment_process(project_type="hotel")

        assert result["data"]["project_type"] == "hotel"
        assert isinstance(result["data"]["structured_steps"], list)
        assert len(result["data"]["structured_steps"]) == 1

    @pytest.mark.asyncio
    async def test_rag_answer_in_data(self):
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(rows=[], second_rows=[]),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(answer="Contact the investor unit."),
            ),
        ):
            result = await get_investment_process()

        assert result["data"]["investor_guidance"] == "Contact the investor unit."

    @pytest.mark.asyncio
    async def test_rag_always_called(self):
        """Investment process must always call RAG (investor unit details live in docs)."""
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(rows=[], second_rows=[]),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(),
            ) as mock_rag,
        ):
            await get_investment_process()

        mock_rag.assert_called_once()

    @pytest.mark.asyncio
    async def test_envelope_fields_present(self):
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(rows=[], second_rows=[]),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(),
            ),
        ):
            result = await get_investment_process()

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result

    @pytest.mark.asyncio
    async def test_source_excerpts_present(self):
        with (
            patch(
                "sltda_mcp.mcp_server.tools.investor.acquire",
                return_value=_mock_acquire(rows=[], second_rows=[]),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result(),
            ),
        ):
            result = await get_investment_process()

        assert "source_excerpts" in result["data"]
        assert len(result["data"]["source_excerpts"]) == 1


class TestSearchSltdaResources:
    @pytest.mark.asyncio
    async def test_happy_path_returns_results(self):
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_make_rag_result(),
        ):
            result = await search_sltda_resources("hotel registration")

        assert result["status"] == "success"
        assert result["tool"] == "search_sltda_resources"
        assert result["data"]["total_results"] == 1

    @pytest.mark.asyncio
    async def test_top_k_capped_at_7(self):
        """Issue #14: top_k must be hard-capped at 7."""
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_make_rag_result(),
        ) as mock_rag:
            await search_sltda_resources("query", top_k=20)

        _, kwargs = mock_rag.call_args
        assert kwargs.get("top_k", mock_rag.call_args[0][3] if mock_rag.call_args[0] else 7) <= 7

    @pytest.mark.asyncio
    async def test_top_k_capped_value_in_data(self):
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_make_rag_result(),
        ):
            result = await search_sltda_resources("query", top_k=20)

        assert result["data"]["top_k_requested"] == 20
        assert result["data"]["top_k_used"] == 7

    @pytest.mark.asyncio
    async def test_no_chunks_returns_not_found(self):
        empty = _make_rag_result(chunks=[])
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=empty,
        ):
            result = await search_sltda_resources("obscure topic xyz")

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_results_include_score_and_section(self):
        chunk = RagChunk(
            chunk_text="Tourist arrivals data.",
            document_id="doc-stats",
            document_name="Annual Report 2023",
            source_url="https://sltda.gov.lk/ar2023.pdf",
            chunk_index=0,
            score=0.87,
            section_name="Statistics",
        )
        rag = _make_rag_result(chunks=[chunk])
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=rag,
        ):
            result = await search_sltda_resources("arrivals")

        item = result["data"]["results"][0]
        assert item["score"] == 0.87
        assert item["section"] == "Statistics"
        assert item["document_name"] == "Annual Report 2023"

    @pytest.mark.asyncio
    async def test_filters_passed_to_rag(self):
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_make_rag_result(),
        ) as mock_rag:
            await search_sltda_resources(
                "hotel registration",
                section_filter="Registration",
                document_type_filter="gazette",
            )

        call_kwargs = mock_rag.call_args[1]
        assert call_kwargs.get("section_filter") == "Registration"
        assert call_kwargs.get("document_type_filter") == "gazette"

    @pytest.mark.asyncio
    async def test_query_field_echoed_in_data(self):
        with patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_make_rag_result(),
        ):
            result = await search_sltda_resources("eco tourism permits")

        assert result["data"]["query"] == "eco tourism permits"
