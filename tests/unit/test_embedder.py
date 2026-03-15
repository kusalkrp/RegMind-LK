"""
Unit tests for ingestion/embedder.py.
Gemini API calls are mocked — no real API access.
"""

from unittest.mock import patch
from uuid import UUID

import pytest

from sltda_mcp.exceptions import EmbeddingError
from sltda_mcp.ingestion.chunker import Chunk
from sltda_mcp.ingestion.embedder import BATCH_SIZE, embed_chunks

_DOC_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

FAKE_EMBEDDING = [0.1] * 768


def make_chunk(index: int, doc_id: str | None = None) -> Chunk:
    return Chunk(
        document_id=doc_id or str(_DOC_ID),
        chunk_index=index,
        chunk_text=f"chunk text for index {index}",
        chunk_strategy="paragraph_aware",
        format_family="unknown",
        token_count=10,
    )


def fake_embed(texts: list[str]) -> list[list[float]]:
    return [FAKE_EMBEDDING for _ in texts]


# ─── Checkpoint resume ────────────────────────────────────────────────────────

class TestCheckpointResume:
    @pytest.mark.asyncio
    async def test_checkpoint_resume_skips_embedded_chunks(self):
        """Chunks with index <= checkpoint are skipped."""
        chunks = [make_chunk(i) for i in range(100)]  # indices 0–99

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=fake_embed):
            results = await embed_chunks(chunks, checkpoint=50)

        # Only chunks 51–99 should be embedded
        assert len(results) == 49
        returned_indices = [chunk.chunk_index for chunk, _ in results]
        assert all(idx > 50 for idx in returned_indices)
        assert 50 not in returned_indices
        assert 51 in returned_indices

    @pytest.mark.asyncio
    async def test_no_checkpoint_embeds_all(self):
        """Default checkpoint=-1 embeds all chunks."""
        chunks = [make_chunk(i) for i in range(10)]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=fake_embed):
            results = await embed_chunks(chunks)

        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_checkpoint_at_last_skips_all(self):
        """Checkpoint at last index → nothing to embed."""
        chunks = [make_chunk(i) for i in range(5)]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=fake_embed):
            results = await embed_chunks(chunks, checkpoint=4)

        assert results == []


# ─── Deduplication ────────────────────────────────────────────────────────────

class TestDeduplication:
    @pytest.mark.asyncio
    async def test_duplicate_chunks_rejected(self):
        """Two chunks with same (document_id, chunk_index) → only one embedded."""
        chunk_a = make_chunk(0)
        chunk_b = make_chunk(0)  # same index, same doc_id — duplicate
        chunk_c = make_chunk(1)

        call_texts: list[list[str]] = []

        def recording_embed(texts):
            call_texts.append(texts)
            return [FAKE_EMBEDDING for _ in texts]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=recording_embed):
            results = await embed_chunks([chunk_a, chunk_b, chunk_c])

        # Only 2 unique chunks (idx 0 and idx 1)
        assert len(results) == 2
        all_sent = [t for batch in call_texts for t in batch]
        assert len(all_sent) == 2

    @pytest.mark.asyncio
    async def test_different_doc_same_index_not_deduped(self):
        """Same chunk_index but different document_id → not duplicates."""
        chunk_a = make_chunk(0, doc_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        chunk_b = make_chunk(0, doc_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=fake_embed):
            results = await embed_chunks([chunk_a, chunk_b])

        assert len(results) == 2


# ─── Rate limit retry ─────────────────────────────────────────────────────────

class TestRateLimitRetry:
    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        """EmbeddingError raised twice then success → result returned."""
        call_count = 0

        def mock_embed_call(texts):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise EmbeddingError("Rate limit exceeded: 429")
            return [FAKE_EMBEDDING for _ in texts]

        chunks = [make_chunk(i) for i in range(5)]

        with patch("sltda_mcp.ingestion.embedder._call_gemini_embed", side_effect=mock_embed_call):
            with patch("time.sleep"):  # skip tenacity backoff waits
                results = await embed_chunks(chunks)

        assert len(results) == 5
        assert call_count == 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_persistent_failure_raises(self):
        """EmbeddingError on all 5 attempts → re-raises."""
        def always_fail(texts):
            raise EmbeddingError("Persistent API failure")

        chunks = [make_chunk(0)]

        with patch("sltda_mcp.ingestion.embedder._call_gemini_embed", side_effect=always_fail):
            with patch("time.sleep"):
                with pytest.raises(EmbeddingError):
                    await embed_chunks(chunks)


# ─── Batch size ───────────────────────────────────────────────────────────────

class TestBatchSize:
    @pytest.mark.asyncio
    async def test_batch_size_respected(self):
        """250 chunks → 3 Gemini API calls (100 + 100 + 50)."""
        call_sizes: list[int] = []

        def recording_embed(texts):
            call_sizes.append(len(texts))
            return [FAKE_EMBEDDING for _ in texts]

        chunks = [make_chunk(i) for i in range(250)]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=recording_embed):
            results = await embed_chunks(chunks)

        assert call_sizes == [100, 100, 50]
        assert len(results) == 250

    @pytest.mark.asyncio
    async def test_single_batch_for_small_input(self):
        """< BATCH_SIZE chunks → exactly 1 API call."""
        call_count = 0

        def counting_embed(texts):
            nonlocal call_count
            call_count += 1
            return [FAKE_EMBEDDING for _ in texts]

        chunks = [make_chunk(i) for i in range(50)]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=counting_embed):
            results = await embed_chunks(chunks)

        assert call_count == 1
        assert len(results) == 50

    @pytest.mark.asyncio
    async def test_embedding_vector_preserved(self):
        """Returned tuples contain the original Chunk and its embedding."""
        custom_embedding = [0.42] * 768

        def custom_embed(texts):
            return [custom_embedding for _ in texts]

        chunks = [make_chunk(0)]

        with patch("sltda_mcp.ingestion.embedder._embed_batch_with_retry", side_effect=custom_embed):
            results = await embed_chunks(chunks)

        assert len(results) == 1
        returned_chunk, returned_embedding = results[0]
        assert returned_chunk.chunk_index == 0
        assert returned_embedding == custom_embedding
