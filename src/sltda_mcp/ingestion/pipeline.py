"""
13-Step Ingestion Pipeline Orchestrator.
Maps to Section 5.1 of the design doc.
Abort conditions: > 10% parse failures, smoke test failure,
Qdrant point count deviation > 5%, cutover transaction failure.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import asyncpg
from qdrant_client import QdrantClient

from sltda_mcp.config import get_settings
from sltda_mcp.database import acquire, init_pool
from sltda_mcp.exceptions import CutoverError, IngestionError
from sltda_mcp.notifications import notify_ingestion_complete, notify_ingestion_failed
from sltda_mcp.ingestion.change_detector import ChangeType, detect_changes, write_manifest
from sltda_mcp.ingestion.chunker import chunk_document
from sltda_mcp.ingestion.cutover import execute_cutover
from sltda_mcp.ingestion.downloader import DownloadStatus, download_documents
from sltda_mcp.ingestion.embedder import embed_chunks
from sltda_mcp.ingestion.extractors.annual_report import AnnualReportExtractor
from sltda_mcp.ingestion.extractors.checklist import ChecklistExtractor
from sltda_mcp.ingestion.extractors.circular import CircularExtractor
from sltda_mcp.ingestion.extractors.data_table import DataTableExtractor
from sltda_mcp.ingestion.extractors.fallback import FallbackExtractor
from sltda_mcp.ingestion.extractors.form import FormExtractor
from sltda_mcp.ingestion.extractors.gazette import GazetteExtractor
from sltda_mcp.ingestion.extractors.legislation import LegislationExtractor
from sltda_mcp.ingestion.extractors.narrative import NarrativeExtractor
from sltda_mcp.ingestion.extractors.steps import StepsExtractor
from sltda_mcp.ingestion.extractors.toolkit import ToolkitExtractor
from sltda_mcp.ingestion.format_identifier import extract_features, identify_format
from sltda_mcp.ingestion.pg_sync import (
    sync_business_categories,
    sync_document,
    sync_document_sections,
    sync_financial_concessions,
    sync_niche_toolkit,
    sync_registration_steps,
)
from sltda_mcp.ingestion.qdrant_upsert import ensure_staging_collection, upsert_chunks
from sltda_mcp.ingestion.scraper import scrape_document_list
from sltda_mcp.ingestion.validator import validate_extraction

logger = logging.getLogger(__name__)

def _strip_nulls(obj):
    """Recursively strip null bytes from strings in a nested dict/list structure."""
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_nulls(i) for i in obj]
    return obj


# Parse-failure abort threshold (Issue #3)
_ABORT_THRESHOLD = 0.10
# Qdrant point count tolerance (5%)
_QDRANT_TOLERANCE = 0.05

_EXTRACTOR_REGISTRY: dict[str, type] = {
    "ChecklistExtractor": ChecklistExtractor,
    "StepsExtractor": StepsExtractor,
    "GazetteExtractor": GazetteExtractor,
    "LegislationExtractor": LegislationExtractor,
    "ToolkitExtractor": ToolkitExtractor,
    "DataTableExtractor": DataTableExtractor,
    "AnnualReportExtractor": AnnualReportExtractor,
    "CircularExtractor": CircularExtractor,
    "NarrativeExtractor": NarrativeExtractor,
    "FormExtractor": FormExtractor,
    "FallbackExtractor": FallbackExtractor,
}


# ─── Pipeline state helpers ────────────────────────────────────────────────────


async def _update_pipeline_state(
    conn: asyncpg.Connection, run_id: str, step: int, status: str, details: str = ""
) -> None:
    await conn.execute(
        """INSERT INTO pipeline_state (run_id, step, status, details, updated_at)
           VALUES ($1, $2, $3, $4, NOW())
           ON CONFLICT (run_id, step) DO UPDATE SET
               status = EXCLUDED.status,
               details = EXCLUDED.details,
               updated_at = NOW()""",
        run_id, step, status, details,
    )


async def _smoke_tests(conn: asyncpg.Connection) -> list[str]:
    """
    Run basic staging sanity checks.
    Returns list of failure messages (empty = all pass).
    """
    failures: list[str] = []

    doc_count = await conn.fetchval("SELECT COUNT(*) FROM documents_staging")
    if not doc_count or doc_count == 0:
        failures.append("documents_staging is empty")

    # Ensure at least some content was synced (sections or steps)
    section_count = await conn.fetchval("SELECT COUNT(*) FROM document_sections_staging")
    step_count = await conn.fetchval("SELECT COUNT(*) FROM registration_steps_staging")
    if (not section_count or section_count == 0) and (not step_count or step_count == 0):
        failures.append("both document_sections_staging and registration_steps_staging are empty")

    # Ensure no NULL category_names slipped through
    bad_cats = await conn.fetchval(
        "SELECT COUNT(*) FROM business_categories_staging WHERE category_name IS NULL OR category_name = ''"
    )
    if bad_cats and bad_cats > 0:
        failures.append(f"business_categories_staging has {bad_cats} rows with NULL/empty category_name")

    return failures


# ─── Per-document extraction + PG sync ────────────────────────────────────────


async def _process_document(
    conn: asyncpg.Connection,
    qdrant_client: QdrantClient,
    doc_change,
    run_id: str,
) -> tuple[int, list]:
    """
    Steps 4–10 for a single document.
    Returns (chunks_produced, chunk_list) on success, raises on hard failure.
    """
    doc = doc_change.candidate
    document_id = str(uuid.uuid4())
    pdf_path = Path(doc_change.local_path)

    # Step 4: Format identification (Tier 0 uses section_id + document_name)
    features = extract_features(pdf_path)
    classification, strategy = await identify_format(
        features,
        section_id=doc_change.candidate.section_id,
        document_name=doc_change.candidate.document_name,
    )

    # Step 5: Extraction
    extractor_cls = _EXTRACTOR_REGISTRY.get(strategy.extractor_class, FallbackExtractor)
    extractor = extractor_cls()
    result = extractor.extract(pdf_path, uuid.UUID(document_id))
    # Strip null bytes — PostgreSQL UTF8 rejects \x00
    if result.text:
        result.text = result.text.replace("\x00", "")
    if result.structured_data:
        result.structured_data = _strip_nulls(result.structured_data)

    # Step 6: Pandera validation
    if result.structured_data:
        validate_extraction(result, classification.format_family)

    # Step 7: Chunking
    chunks = chunk_document(
        result=result,
        format_family=classification.format_family,
        chunk_strategy=strategy.chunk_strategy,
        document_id=uuid.UUID(document_id),
    )

    # Step 10: PG sync
    file_stat = pdf_path.stat() if pdf_path.exists() else None
    doc_meta = {
        "id": document_id,
        "section_id": doc.section_id,
        "section_name": doc.section_name,
        "document_name": doc.document_name,
        "source_url": doc.source_url,
        "local_path": str(pdf_path),
        "file_size_kb": round(file_stat.st_size / 1024) if file_stat else None,
        "content_hash": doc_change.current_hash,
        "language": doc.language,
        "format_family": classification.format_family,
        "format_confidence": classification.confidence,
        "ocr_extracted": result.ocr_used,
        "extraction_yield_tokens": sum(c.token_count for c in chunks),
        "is_indexed": True,
        "is_active": True,
    }
    await sync_document(conn, doc_meta)

    output_table = strategy.output_table
    sd = result.structured_data or {}

    if output_table == "registration_steps_staging" and sd.get("steps"):
        await sync_registration_steps(
            conn, document_id,
            sd.get("category_code", "UNKNOWN"),
            sd.get("action_type", "registration"),
            sd["steps"],
        )
    elif output_table == "financial_concessions_staging" and sd.get("concessions"):
        await sync_financial_concessions(conn, document_id, sd["concessions"])
    elif output_table == "business_categories_staging" and sd.get("categories"):
        await sync_business_categories(conn, document_id, sd["categories"])
    elif output_table == "niche_toolkits_staging" and sd.get("toolkit"):
        await sync_niche_toolkit(
            conn, document_id, sd["toolkit"],
            full_text=result.text,
            confidence=classification.confidence,
            token_count=sum(c.token_count for c in chunks),
            page_count=result.page_count,
        )
    else:
        sections = sd.get("sections") or [{"heading": doc.document_name, "text": result.text}]
        await sync_document_sections(conn, document_id, sections)

    return len(chunks), chunks


# ─── Main orchestrator ────────────────────────────────────────────────────────


async def run_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """
    Execute the 13-step ingestion pipeline.

    Args:
        dry_run: If True, skip cutover (steps 12-13). Useful for testing.

    Returns:
        Summary dict with counts and final status.
    """
    settings = get_settings()
    run_id = str(uuid.uuid4())
    logger.info("Pipeline run %s started (dry_run=%s)", run_id, dry_run)

    await init_pool()

    qdrant_sync_client = QdrantClient(url=settings.qdrant_url)

    # Acquire advisory lock to prevent concurrent pipeline runs
    # Lock ID 7734265 is a fixed arbitrary constant for this pipeline
    _PIPELINE_LOCK_ID = 7734265

    async with acquire() as conn:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", _PIPELINE_LOCK_ID
        )
        if not acquired:
            logger.warning("Another ingestion pipeline is already running — aborting duplicate run")
            return {"run_id": run_id, "status": "skipped_duplicate", "processed": 0}
        try:
            # ── Step 1: Scrape ────────────────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 1, "running", "scraping document list")
            candidates = await scrape_document_list()
            await _update_pipeline_state(conn, run_id, 1, "done", f"{len(candidates)} candidates")
            logger.info("Step 1 done: %d candidate documents", len(candidates))

            # ── Step 2: Change detection ──────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 2, "running")
            manifests_dir = Path(settings.documents_base_path) / "manifests"
            changes, _removed = detect_changes(candidates, manifests_dir)
            to_process = [c for c in changes if c.change_type in (ChangeType.NEW, ChangeType.MODIFIED)]
            await _update_pipeline_state(conn, run_id, 2, "done", f"{len(to_process)} to process")
            logger.info("Step 2 done: %d new/modified documents", len(to_process))

            if not to_process:
                logger.info("No changes detected — pipeline complete")
                return {"run_id": run_id, "status": "no_changes", "processed": 0}

            # ── Step 3: Download ──────────────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 3, "running")
            batch_result = await download_documents([c.candidate for c in to_process])
            successful = batch_result.successful
            await _update_pipeline_state(
                conn, run_id, 3, "done",
                f"{len(successful)}/{len(to_process)} downloaded"
            )
            logger.info("Step 3 done: %d/%d downloaded", len(successful), len(to_process))

            # Populate local_path and content_hash from download results onto changes
            successful_by_url = {r.candidate.source_url: r for r in successful}
            for change in to_process:
                result = successful_by_url.get(change.candidate.source_url)
                if result:
                    change.local_path = str(result.local_path)
                    change.current_hash = result.content_hash
                    change.file_size_kb = result.file_size_kb
            to_process = [c for c in to_process if c.local_path is not None]

            # ── Steps 4–10: Format/Extract/Chunk/Embed/Upsert/PGSync ─────────────
            await _update_pipeline_state(conn, run_id, 4, "running", "extract/chunk/embed loop")

            # Wipe + recreate staging collection before any upserts (Issue #5)
            ensure_staging_collection(qdrant_sync_client)

            all_chunks = []
            parse_failures = 0

            for change in to_process:
                try:
                    _count, doc_chunks = await _process_document(conn, qdrant_sync_client, change, run_id)
                    all_chunks.extend(doc_chunks)
                except Exception as exc:
                    parse_failures += 1
                    logger.error(
                        "Document processing failed for %s: %s",
                        change.candidate.filename, exc, exc_info=True,
                    )

            failure_rate = parse_failures / max(len(to_process), 1)
            if failure_rate > _ABORT_THRESHOLD:
                msg = (
                    f"Abort: {failure_rate:.1%} parse failure rate exceeds "
                    f"{_ABORT_THRESHOLD:.0%} threshold"
                )
                await _update_pipeline_state(conn, run_id, 4, "aborted", msg)
                exc = IngestionError(msg)
                await notify_ingestion_failed(exc, run_id)
                raise exc

            await _update_pipeline_state(
                conn, run_id, 4, "done",
                f"{len(all_chunks)} chunks, {parse_failures} failures"
            )

            # ── Step 8: Embed ─────────────────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 8, "running", "embedding chunks")
            embedded = await embed_chunks(all_chunks)
            await _update_pipeline_state(conn, run_id, 8, "done", f"{len(embedded)} embedded")

            # ── Step 9: Qdrant upsert ─────────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 9, "running", "upserting to Qdrant staging")
            try:
                actual = upsert_chunks(embedded, qdrant_sync_client, expected_count=len(embedded))
            except Exception as exc:
                await _update_pipeline_state(conn, run_id, 9, "aborted", str(exc))
                await notify_ingestion_failed(exc, run_id)
                raise

            await _update_pipeline_state(conn, run_id, 9, "done", f"{actual} points in staging")

            # ── Step 11: Smoke tests ──────────────────────────────────────────────
            await _update_pipeline_state(conn, run_id, 11, "running", "running smoke tests")
            failures = await _smoke_tests(conn)
            if failures:
                msg = f"Smoke tests failed: {'; '.join(failures)}"
                await _update_pipeline_state(conn, run_id, 11, "aborted", msg)
                exc = IngestionError(msg)
                await notify_ingestion_failed(exc, run_id)
                raise exc
            await _update_pipeline_state(conn, run_id, 11, "done", "all smoke tests passed")

            # ── Step 12: Cutover ──────────────────────────────────────────────────
            if dry_run:
                logger.info("dry_run=True — skipping cutover")
                await _update_pipeline_state(conn, run_id, 12, "skipped", "dry_run")
            else:
                await _update_pipeline_state(conn, run_id, 12, "running", "atomic cutover")
                try:
                    await execute_cutover(conn)
                except CutoverError as exc:
                    await _update_pipeline_state(conn, run_id, 12, "aborted", str(exc))
                    await notify_ingestion_failed(exc, run_id)
                    raise
                await _update_pipeline_state(conn, run_id, 12, "done")

            # ── Step 13: Write manifest ───────────────────────────────────────────
            write_manifest(successful, manifests_dir, run_id)
            await _update_pipeline_state(conn, run_id, 13, "done", "manifest written")

            summary = {
                "run_id": run_id,
                "status": "complete",
                "candidates": len(candidates),
                "downloaded": len(successful),
                "processed": len(to_process) - parse_failures,
                "parse_failures": parse_failures,
                "chunks_embedded": len(embedded),
                "qdrant_points": actual,
                "dry_run": dry_run,
            }
            logger.info("Pipeline run %s complete: %s", run_id, summary)
            await notify_ingestion_complete(summary)
            return summary
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _PIPELINE_LOCK_ID)


if __name__ == "__main__":
    import sys
    from sltda_mcp.config import configure_logging

    configure_logging()
    dry = "--dry-run" in sys.argv
    asyncio.run(run_pipeline(dry_run=dry))
