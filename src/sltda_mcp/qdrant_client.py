"""
Qdrant client wrapper.
Provides collection management, upsert, and search helpers.
"""

import logging
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sltda_mcp.config import get_settings
from sltda_mcp.exceptions import QdrantError

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None

VECTOR_DIMENSIONS = 768
DISTANCE_METRIC = qdrant_models.Distance.COSINE


def get_client() -> AsyncQdrantClient:
    if _client is None:
        raise RuntimeError("Qdrant client not initialised — call init_client() first")
    return _client


async def init_client() -> None:
    global _client
    settings = get_settings()
    _client = AsyncQdrantClient(url=settings.qdrant_url)
    logger.info("Qdrant client initialised: %s", settings.qdrant_url)


async def close_client() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
        logger.info("Qdrant client closed")


async def collection_exists(name: str) -> bool:
    try:
        client = get_client()
        collections = await client.get_collections()
        return any(c.name == name for c in collections.collections)
    except Exception as exc:
        raise QdrantError(f"Failed to list collections: {exc}") from exc


async def create_collection(name: str, *, on_disk_payload: bool = True) -> None:
    """Create a Qdrant collection idempotently."""
    client = get_client()
    if await collection_exists(name):
        logger.info("Collection '%s' already exists — skipping creation", name)
        return

    await client.create_collection(
        collection_name=name,
        vectors_config=qdrant_models.VectorParams(
            size=VECTOR_DIMENSIONS,
            distance=DISTANCE_METRIC,
        ),
        hnsw_config=qdrant_models.HnswConfigDiff(on_disk=True),
        on_disk_payload=on_disk_payload,
    )
    logger.info("Created Qdrant collection '%s'", name)


async def create_collections() -> None:
    """Create all required collections (idempotent)."""
    settings = get_settings()
    await create_collection(settings.qdrant_collection)
    await create_collection(settings.qdrant_exemplars_collection)
    logger.info("All Qdrant collections ready")


async def delete_collection(name: str) -> None:
    """Delete a collection if it exists."""
    client = get_client()
    if await collection_exists(name):
        await client.delete_collection(name)
        logger.info("Deleted Qdrant collection '%s'", name)
    else:
        logger.debug("Collection '%s' does not exist — nothing to delete", name)


async def get_collection_point_count(name: str) -> int:
    client = get_client()
    info = await client.get_collection(name)
    return info.points_count or 0


async def upsert_points(
    collection_name: str,
    points: list[qdrant_models.PointStruct],
) -> None:
    """Upsert a batch of points into a collection."""
    client = get_client()
    try:
        await client.upsert(collection_name=collection_name, points=points)
    except Exception as exc:
        raise QdrantError(f"Upsert failed for collection '{collection_name}': {exc}") from exc


@retry(
    retry=retry_if_exception_type(QdrantError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def search(
    collection_name: str,
    query_vector: list[float],
    top_k: int,
    score_threshold: float,
    query_filter: qdrant_models.Filter | None = None,
) -> list[qdrant_models.ScoredPoint]:
    """Semantic search with automatic retry on transient failures."""
    client = get_client()
    try:
        results = await client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )
        return results
    except Exception as exc:
        raise QdrantError(f"Search failed in '{collection_name}': {exc}") from exc


async def reassign_alias(alias_name: str, target_collection: str) -> None:
    """Atomically reassign a Qdrant alias to a new collection."""
    client = get_client()
    try:
        await client.update_collection_aliases(
            change_aliases_operations=[
                qdrant_models.CreateAliasOperation(
                    create_alias=qdrant_models.CreateAlias(
                        collection_name=target_collection,
                        alias_name=alias_name,
                    )
                )
            ]
        )
        logger.info("Alias '%s' → '%s'", alias_name, target_collection)
    except Exception as exc:
        raise QdrantError(f"Alias reassignment failed: {exc}") from exc


async def warmup_query(collection_name: str) -> None:
    """Issue a dummy search to warm up HNSW index after cold start."""
    dummy_vector = [0.0] * VECTOR_DIMENSIONS
    client = get_client()
    try:
        await client.search(
            collection_name=collection_name,
            query_vector=dummy_vector,
            limit=1,
            score_threshold=0.0,
        )
        logger.info("Qdrant warm-up complete for collection '%s'", collection_name)
    except Exception:
        logger.warning("Qdrant warm-up query failed (collection may be empty)")
