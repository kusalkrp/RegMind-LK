"""
Unit tests for mcp_server/rag.py and mcp_server/query_expansion.py.
Gemini and Qdrant are fully mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.http import models as qdrant_models

from sltda_mcp.config import Settings
from sltda_mcp.mcp_server.query_expansion import ExpandedQuery, expand_query
from sltda_mcp.mcp_server.rag import (
    RagChunk,
    _coherence_rerank,
    _map_confidence,
    _truncate_chunk,
    run_rag,
)


# ─── query_expansion ─────────────────────────────────────────────────────────

class TestQueryExpansion:
    def test_acronym_replaced(self):
        result = expand_query("what is the TDL rate?")
        assert "TDL" in result.acronyms_replaced
        assert result.acronyms_replaced["TDL"] == "Tourism Development Levy"
        assert "Tourism Development Levy" in result.full_query

    def test_sltda_acronym_replaced(self):
        result = expand_query("SLTDA registration requirements")
        assert "SLTDA" in result.acronyms_replaced

    def test_synonym_expansion_airbnb(self):
        result = expand_query("do I need a permit for my airbnb?")
        assert "rented home" in result.expanded_terms
        assert "short-term rental" in result.expanded_terms

    def test_synonym_expansion_tax(self):
        result = expand_query("what are the tax rates?")
        assert "Tourism Development Levy" in result.expanded_terms

    def test_no_expansion_for_unmatched_query(self):
        result = expand_query("hello world random query xyz")
        assert result.expanded_terms == []
        assert result.acronyms_replaced == {}

    def test_full_query_includes_original_and_expanded(self):
        result = expand_query("airbnb regulations")
        assert result.original in result.full_query
        assert all(t in result.full_query for t in result.expanded_terms)

    def test_original_preserved(self):
        query = "How do I register a boutique hotel?"
        result = expand_query(query)
        assert result.original == query


# ─── RAG helpers ─────────────────────────────────────────────────────────────

class TestRagHelpers:
    def test_confidence_high(self):
        assert _map_confidence(0.90) == "high"
        assert _map_confidence(0.86) == "high"

    def test_confidence_medium(self):
        assert _map_confidence(0.75) == "medium"
        assert _map_confidence(0.70) == "medium"

    def test_confidence_low(self):
        assert _map_confidence(0.69) == "low"
        assert _map_confidence(0.50) == "low"

    def test_truncate_short_text_unchanged(self):
        text = "Short text"
        assert _truncate_chunk(text) == text

    def test_chunk_text_truncated_at_500_chars(self):
        text = "x" * 1000
        truncated = _truncate_chunk(text)
        assert len(truncated) <= 503  # 500 chars + "..."
        assert truncated.endswith("...")

    def test_coherence_rerank_no_change_under_threshold(self):
        """3 or fewer distinct docs → no reranking."""
        chunks = [
            RagChunk("text", "doc_A", "Doc A", None, 0, 0.90),
            RagChunk("text", "doc_B", "Doc B", None, 1, 0.85),
            RagChunk("text", "doc_A", "Doc A", None, 2, 0.80),
        ]
        result = _coherence_rerank(chunks)
        assert len(result) == 3

    def test_coherence_rerank_focuses_on_best_doc(self):
        """6 chunks from 6 docs → top-3 from the highest-scoring doc."""
        chunks = [
            RagChunk("t", f"doc_{i}", f"Doc {i}", None, 0, 0.60 + i * 0.05)
            for i in range(6)
        ]
        # doc_5 has highest score (0.85)
        result = _coherence_rerank(chunks)
        assert all(c.document_id == "doc_5" for c in result)
        assert len(result) <= 3


# ─── run_rag ─────────────────────────────────────────────────────────────────

def _make_scored_point(
    doc_id: str,
    score: float,
    chunk_text: str = "Sample text.",
    superseded: bool = False,
) -> qdrant_models.ScoredPoint:
    return qdrant_models.ScoredPoint(
        id=f"{doc_id}-{score}",
        version=1,
        score=score,
        payload={
            "document_id": doc_id,
            "document_name": f"Doc {doc_id}",
            "source_url": f"https://sltda.gov.lk/{doc_id}.pdf",
            "chunk_index": 0,
            "chunk_text": chunk_text,
            "section_name": "General",
            "page_numbers": [1],
            "superseded": superseded,
        },
        vector=None,
    )


_MOCK_SETTINGS = Settings(
    postgres_url="postgresql://test:test@localhost/test",
    gemini_api_key="test-key",
    qdrant_url="http://localhost:6333",
)


class TestRunRag:
    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("sltda_mcp.mcp_server.rag.get_settings", return_value=_MOCK_SETTINGS):
            yield

    @pytest.mark.asyncio
    async def test_no_results_returns_not_found(self):
        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=[]),
        ):
            result = await run_rag("completely unknown topic")

        assert result.answer == "Not found in available documents."
        assert result.confidence == "low"
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_synthesis_called_when_chunks_found(self):
        hits = [_make_scored_point("docA", 0.92)]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits),
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, return_value="Synthesised answer.") as mock_synth,
        ):
            result = await run_rag("TDL rate?")

        mock_synth.assert_called_once()
        assert result.synthesis_used is True
        assert result.answer == "Synthesised answer."

    @pytest.mark.asyncio
    async def test_synthesis_fallback_on_rate_limit(self):
        """Gemini 429 after retries → raw chunks returned, synthesis_used=False."""
        hits = [_make_scored_point("docA", 0.88, chunk_text="Raw excerpt about TDL.")]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits),
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, side_effect=Exception("429 rate limit")),
        ):
            result = await run_rag("TDL rate")

        assert result.synthesis_used is False
        assert "Synthesis unavailable" in result.answer
        assert result.confidence == "low"

    @pytest.mark.asyncio
    async def test_chunk_text_truncated_in_result(self):
        long_text = "A" * 1000
        hits = [_make_scored_point("docA", 0.90, chunk_text=long_text)]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits),
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, return_value="Answer."),
        ):
            result = await run_rag("query")

        # All returned chunks must have text ≤ 503 chars (500 + "...")
        for chunk in result.chunks:
            assert len(chunk.chunk_text) <= 503

    @pytest.mark.asyncio
    async def test_query_expanded_flag_set(self):
        """TDL in query → acronym expanded → query_expanded=True."""
        hits = [_make_scored_point("docA", 0.88)]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits),
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, return_value="ok"),
        ):
            result = await run_rag("TDL clearance process")

        assert result.query_expanded is True

    @pytest.mark.asyncio
    async def test_top_k_hard_capped_at_7(self):
        """top_k=20 must be silently capped to 7."""
        hits = [_make_scored_point(f"doc{i}", 0.80) for i in range(3)]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits) as mock_search,
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, return_value="ok"),
        ):
            await run_rag("query", top_k=20)

        # The top_k passed to _search_collection must be ≤ 7
        called_top_k = mock_search.call_args[1]["top_k"] if mock_search.call_args[1] else mock_search.call_args[0][1]
        assert called_top_k <= 7

    @pytest.mark.asyncio
    async def test_confidence_high_above_085(self):
        hits = [_make_scored_point("docA", 0.92)]

        with (
            patch("sltda_mcp.mcp_server.rag._embed_text", new_callable=AsyncMock, return_value=[0.1] * 768),
            patch("sltda_mcp.mcp_server.rag._search_collection", new_callable=AsyncMock, return_value=hits),
            patch("sltda_mcp.mcp_server.rag._synthesise", new_callable=AsyncMock, return_value="ok"),
        ):
            result = await run_rag("query")

        assert result.confidence == "high"
