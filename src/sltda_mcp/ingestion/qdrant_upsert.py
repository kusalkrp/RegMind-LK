"""
Qdrant Staging Upsert.
Upserts embedded chunks into sltda_documents_next (staging collection).
Issue #5 mitigation: staging collection is wiped at pipeline start.
Deterministic point IDs prevent duplicates on re-run.
"""

import json
import logging
from uuid import UUID, uuid5

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from sltda_mcp.exceptions import QdrantError
from sltda_mcp.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

STAGING_COLLECTION = "sltda_documents_next"
VECTOR_SIZE = 768
_ID_NAMESPACE = UUID("12345678-1234-5678-1234-567812345678")


def _make_point_id(document_id: str, chunk_index: int) -> str:
    """Deterministic UUID5 from (document_id, chunk_index) — dedup across runs."""
    return str(uuid5(_ID_NAMESPACE, f"{document_id}:{chunk_index}"))


def ensure_staging_collection(client: QdrantClient) -> None:
    """
    Issue #5 mitigation: delete and recreate staging collection at pipeline start.
    Never touches the live sltda_documents collection.
    """
    existing = {c.name for c in client.get_collections().collections}
    if STAGING_COLLECTION in existing:
        client.delete_collection(STAGING_COLLECTION)
        logger.info("Staging collection deleted: %s", STAGING_COLLECTION)

    client.create_collection(
        collection_name=STAGING_COLLECTION,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    logger.info("Staging collection created: %s", STAGING_COLLECTION)


def upsert_chunks(
    chunks_with_embeddings: list[tuple[Chunk, list[float]]],
    client: QdrantClient,
    expected_count: int,
    ocr_extracted: bool = False,
) -> int:
    """
    Upsert (chunk, embedding) pairs into staging collection.

    Post-upsert: validates actual point count is within 5% of expected.
    Raises QdrantError if the count is outside tolerance.

    Returns:
        Actual point count in staging collection.
    """
    if not chunks_with_embeddings:
        logger.info("Qdrant upsert: nothing to upsert")
        return 0

    points = [
        PointStruct(
            id=_make_point_id(chunk.document_id, chunk.chunk_index),
            vector=embedding,
            payload={
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "chunk_text": chunk.chunk_text,
                "chunk_strategy": chunk.chunk_strategy,
                "format_family": chunk.format_family,
                "token_count": chunk.token_count,
                "chunk_type": chunk.chunk_type,
                "page_numbers": chunk.page_numbers,
                "superseded": False,
                "ocr_extracted": ocr_extracted,
            },
        )
        for chunk, embedding in chunks_with_embeddings
    ]

    client.upsert(collection_name=STAGING_COLLECTION, points=points, wait=True)
    logger.info("Qdrant upsert: %d points upserted to %s", len(points), STAGING_COLLECTION)

    actual = client.count(collection_name=STAGING_COLLECTION).count
    tolerance = max(expected_count * 0.05, 1)
    if abs(actual - expected_count) > tolerance:
        raise QdrantError(
            f"Point count mismatch after upsert: expected {expected_count}, "
            f"got {actual} (tolerance={tolerance:.0f})"
        )

    logger.info("Qdrant upsert: count validated (%d points in staging)", actual)
    return actual
