"""
Change Detector.
Compares the candidate document list against the previous manifest
to determine which documents are new, modified, unchanged, or removed.
Only new/modified documents need re-ingestion.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from uuid import uuid4

from sltda_mcp.config import get_settings
from sltda_mcp.ingestion.downloader import DownloadResult, DownloadStatus
from sltda_mcp.ingestion.scraper import CandidateDocument

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"
    REMOVED = "removed"


@dataclass
class DocumentChange:
    candidate: CandidateDocument | None  # None for REMOVED documents
    change_type: ChangeType
    previous_hash: str | None = None
    current_hash: str | None = None
    local_path: str | None = None
    file_size_kb: int | None = None


def _load_latest_manifest(manifests_dir: Path) -> dict:
    """Load the most recent manifest file. Returns empty dict if none exists."""
    if not manifests_dir.exists():
        return {}
    manifest_files = sorted(manifests_dir.glob("*.json"))
    if not manifest_files:
        return {}
    latest = manifest_files[-1]
    logger.info("Loading manifest: %s", latest.name)
    with open(latest) as f:
        return json.load(f)


def _build_manifest_index(manifest: dict) -> dict[str, dict]:
    """Build a URL → manifest_entry lookup from a manifest."""
    return {entry["url"]: entry for entry in manifest.get("documents", [])}


def detect_changes(
    candidates: list[CandidateDocument],
    manifests_dir: Path | None = None,
) -> tuple[list[DocumentChange], list[DocumentChange]]:
    """
    Compare candidate URL list against the previous manifest (URL-based).
    Downloads happen after this call; content hash comparison is deferred.

    Returns:
        changed: DocumentChange list with type NEW — need ingestion
        removed: DocumentChange list with type REMOVED — mark inactive in DB
    """
    settings = get_settings()
    base = Path(settings.documents_base_path)
    manifests_path = manifests_dir or (base / "manifests")

    previous_manifest = _load_latest_manifest(manifests_path)
    previous_index = _build_manifest_index(previous_manifest)

    changed: list[DocumentChange] = []
    unchanged_count = 0
    current_urls: set[str] = set()

    for candidate in candidates:
        url = candidate.source_url
        current_urls.add(url)
        prev_entry = previous_index.get(url)

        if prev_entry is None:
            changed.append(DocumentChange(
                candidate=candidate,
                change_type=ChangeType.NEW,
                previous_hash=None,
                current_hash=None,
                local_path=None,
            ))
        else:
            unchanged_count += 1

    # Detect removed documents (in previous manifest but no longer scraped)
    removed: list[DocumentChange] = []
    for url, entry in previous_index.items():
        if url not in current_urls:
            removed.append(DocumentChange(
                candidate=None,
                change_type=ChangeType.REMOVED,
                previous_hash=entry.get("sha256"),
                current_hash=None,
                local_path=entry.get("local_path"),
            ))

    logger.info(
        "Change detection: %d new, %d unchanged, %d removed",
        len(changed),
        unchanged_count,
        len(removed),
    )
    return changed, removed


def write_manifest(
    download_results: list[DownloadResult],
    manifests_dir: Path | None = None,
    run_id: str | None = None,
) -> Path:
    """
    Write a new manifest file for this ingestion run.
    Returns the path of the written manifest.
    """
    settings = get_settings()
    base = Path(settings.documents_base_path)
    manifests_path = manifests_dir or (base / "manifests")
    manifests_path.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    manifest_file = manifests_path / f"{today}_manifest.json"

    documents = []
    for result in download_results:
        if result.status != DownloadStatus.SUCCESS:
            continue
        documents.append({
            "url": result.candidate.source_url,
            "filename": result.candidate.filename,
            "sha256": result.content_hash,
            "section_id": result.candidate.section_id,
            "section_name": result.candidate.section_name,
            "document_name": result.candidate.document_name,
            "file_size_kb": result.file_size_kb,
            "local_path": str(result.local_path),
        })

    manifest = {
        "generated_at": date.today().isoformat(),
        "pipeline_run_id": run_id or str(uuid4()),
        "total_documents": len(documents),
        "documents": documents,
    }

    with open(manifest_file, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Manifest written: %s (%d documents)", manifest_file.name, len(documents))
    return manifest_file
