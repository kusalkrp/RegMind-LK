"""
Gemini Embedder.
Embeds document chunks using text-embedding-004 (768 dimensions).
Features: checkpoint resume (Issue #4), deduplication, batching (100/call),
rate-limit retry with exponential backoff.
"""

import logging
from uuid import UUID

import google.generativeai as genai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sltda_mcp.config import get_settings
from sltda_mcp.exceptions import EmbeddingError
from sltda_mcp.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "models/text-embedding-004"
EMBEDDING_DIM = 768
BATCH_SIZE = 100


def _call_gemini_embed(texts: list[str]) -> list[list[float]]:
    """
    Synchronous Gemini embedding call. Separated for easy mocking in tests.
    Wraps API errors as EmbeddingError to trigger tenacity retry.
    """
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key)
    try:
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=texts,
            task_type="retrieval_document",
        )
        return result["embedding"]
    except Exception as exc:
        raise EmbeddingError(f"Gemini embedding failed: {exc}") from exc


@retry(
    retry=retry_if_exception_type(EmbeddingError),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _embed_batch_with_retry(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with automatic retry on EmbeddingError."""
    return _call_gemini_embed(texts)


def _dedup_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Remove duplicate (document_id, chunk_index) pairs — Issue #4 mitigation."""
    seen: set[tuple[str, int]] = set()
    unique: list[Chunk] = []
    for chunk in chunks:
        key = (chunk.document_id, chunk.chunk_index)
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
        else:
            logger.warning(
                "Duplicate chunk skipped: doc=%s idx=%d", chunk.document_id, chunk.chunk_index
            )
    return unique


async def embed_chunks(
    chunks: list[Chunk],
    checkpoint: int = -1,
) -> list[tuple[Chunk, list[float]]]:
    """
    Embed chunks using Gemini text-embedding-004.

    Args:
        chunks: All chunks to embed.
        checkpoint: Last successfully embedded chunk_index.
            Chunks with index <= checkpoint are skipped (Issue #4 resume).

    Returns:
        List of (Chunk, embedding_vector) pairs for newly embedded chunks.
    """
    # Resume: skip already-embedded chunks
    to_embed = [c for c in chunks if c.chunk_index > checkpoint]
    skipped = len(chunks) - len(to_embed)
    if skipped:
        logger.info("Embedder: skipping %d already-embedded chunks (checkpoint=%d)", skipped, checkpoint)

    # Deduplicate
    to_embed = _dedup_chunks(to_embed)

    if not to_embed:
        logger.info("Embedder: nothing to embed")
        return []

    results: list[tuple[Chunk, list[float]]] = []

    for batch_start in range(0, len(to_embed), BATCH_SIZE):
        batch = to_embed[batch_start : batch_start + BATCH_SIZE]
        texts = [c.chunk_text for c in batch]

        logger.debug(
            "Embedder: embedding batch %d–%d (%d chunks)",
            batch_start,
            batch_start + len(batch) - 1,
            len(batch),
        )
        embeddings = _embed_batch_with_retry(texts)
        results.extend(zip(batch, embeddings))

    logger.info("Embedder: embedded %d chunks (%d batches)", len(results), -(-len(to_embed) // BATCH_SIZE))
    return results
