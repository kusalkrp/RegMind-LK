"""
Unit tests for ingestion/change_detector.py.
"""

import json
from pathlib import Path

import pytest

from sltda_mcp.ingestion.change_detector import (
    ChangeType,
    detect_changes,
    write_manifest,
)
from sltda_mcp.ingestion.downloader import DownloadResult, DownloadStatus
from sltda_mcp.ingestion.scraper import CandidateDocument


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_candidate(url: str, filename: str = "doc.pdf") -> CandidateDocument:
    return CandidateDocument(
        section_id=1,
        section_name="Registration & Renewal",
        document_name="Test Document",
        source_url=url,
        filename=filename,
    )


def make_result(
    url: str,
    filename: str,
    sha256: str = "abc123",
    status: DownloadStatus = DownloadStatus.SUCCESS,
    local_path: str | None = None,
    file_size_kb: int = 100,
) -> DownloadResult:
    return DownloadResult(
        candidate=make_candidate(url, filename),
        status=status,
        local_path=Path(local_path) if local_path else Path(f"/tmp/{filename}"),
        content_hash=sha256 if status == DownloadStatus.SUCCESS else None,
        file_size_kb=file_size_kb,
    )


def write_manifest_fixture(manifests_dir: Path, documents: list[dict]) -> None:
    """Helper to write a previous manifest for testing."""
    manifests_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": "2026-02-01",
        "pipeline_run_id": "test-run-id",
        "total_documents": len(documents),
        "documents": documents,
    }
    with open(manifests_dir / "2026-02-01_manifest.json", "w") as f:
        json.dump(manifest, f)


# ─── detect_changes ───────────────────────────────────────────────────────────

class TestDetectChanges:
    def test_no_manifest_all_new(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        results = [
            make_result("https://sltda.gov.lk/a.pdf", "a.pdf", sha256="hash_a"),
            make_result("https://sltda.gov.lk/b.pdf", "b.pdf", sha256="hash_b"),
        ]
        manifests_dir = tmp_path / "manifests"  # does not exist yet

        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        assert len(changed) == 2
        assert all(c.change_type == ChangeType.NEW for c in changed)
        assert len(removed) == 0
        get_settings.cache_clear()

    def test_same_hash_classified_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        write_manifest_fixture(manifests_dir, [
            {"url": "https://sltda.gov.lk/a.pdf", "sha256": "hash_a", "filename": "a.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Doc A",
             "file_size_kb": 100, "local_path": "/tmp/a.pdf"},
        ])

        results = [make_result("https://sltda.gov.lk/a.pdf", "a.pdf", sha256="hash_a")]
        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        assert len(changed) == 0  # unchanged docs don't appear in changed list
        assert len(removed) == 0
        get_settings.cache_clear()

    def test_different_hash_classified_modified(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        write_manifest_fixture(manifests_dir, [
            {"url": "https://sltda.gov.lk/a.pdf", "sha256": "old_hash", "filename": "a.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Doc A",
             "file_size_kb": 100, "local_path": "/tmp/a.pdf"},
        ])

        results = [make_result("https://sltda.gov.lk/a.pdf", "a.pdf", sha256="new_hash")]
        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        assert len(changed) == 1
        assert changed[0].change_type == ChangeType.MODIFIED
        assert changed[0].previous_hash == "old_hash"
        assert changed[0].current_hash == "new_hash"
        get_settings.cache_clear()

    def test_missing_url_classified_removed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        write_manifest_fixture(manifests_dir, [
            {"url": "https://sltda.gov.lk/old.pdf", "sha256": "hash_old", "filename": "old.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Old Doc",
             "file_size_kb": 100, "local_path": "/tmp/old.pdf"},
        ])

        # Current run has a different document — old one is removed
        results = [make_result("https://sltda.gov.lk/new.pdf", "new.pdf", sha256="hash_new")]
        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        assert len(removed) == 1
        assert removed[0].change_type == ChangeType.REMOVED
        assert removed[0].previous_hash == "hash_old"
        get_settings.cache_clear()

    def test_failed_downloads_excluded_from_change_detection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"

        results = [
            make_result("https://sltda.gov.lk/a.pdf", "a.pdf", sha256=None,
                        status=DownloadStatus.FAILED_MAGIC_BYTES),
        ]
        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        # Failed downloads are not tracked — no manifest means 0 changed
        assert len(changed) == 0
        get_settings.cache_clear()

    def test_mixed_scenario(self, tmp_path, monkeypatch):
        """New + modified + unchanged + removed all in one run."""
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        write_manifest_fixture(manifests_dir, [
            {"url": "https://sltda.gov.lk/unchanged.pdf", "sha256": "hash_u", "filename": "unchanged.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Unchanged",
             "file_size_kb": 100, "local_path": "/tmp/unchanged.pdf"},
            {"url": "https://sltda.gov.lk/modified.pdf", "sha256": "old_hash", "filename": "modified.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Modified",
             "file_size_kb": 100, "local_path": "/tmp/modified.pdf"},
            {"url": "https://sltda.gov.lk/removed.pdf", "sha256": "hash_r", "filename": "removed.pdf",
             "section_id": 1, "section_name": "Reg", "document_name": "Removed",
             "file_size_kb": 100, "local_path": "/tmp/removed.pdf"},
        ])

        results = [
            make_result("https://sltda.gov.lk/unchanged.pdf", "unchanged.pdf", sha256="hash_u"),
            make_result("https://sltda.gov.lk/modified.pdf", "modified.pdf", sha256="new_hash"),
            make_result("https://sltda.gov.lk/new.pdf", "new.pdf", sha256="hash_n"),
        ]
        changed, removed = detect_changes(results, manifests_dir=manifests_dir)

        change_types = {c.candidate.filename: c.change_type for c in changed}
        assert change_types["modified.pdf"] == ChangeType.MODIFIED
        assert change_types["new.pdf"] == ChangeType.NEW
        assert "unchanged.pdf" not in change_types
        assert len(removed) == 1
        assert removed[0].previous_hash == "hash_r"
        get_settings.cache_clear()


# ─── write_manifest ───────────────────────────────────────────────────────────

class TestWriteManifest:
    def test_manifest_file_created(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        results = [make_result("https://sltda.gov.lk/a.pdf", "a.pdf")]

        path = write_manifest(results, run_id="test-run", manifests_dir=manifests_dir)

        assert path.exists()
        assert path.suffix == ".json"
        get_settings.cache_clear()

    def test_manifest_content_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        results = [make_result("https://sltda.gov.lk/a.pdf", "a.pdf", sha256="abc")]

        path = write_manifest(results, manifests_dir=manifests_dir)
        data = json.loads(path.read_text())

        assert "generated_at" in data
        assert "pipeline_run_id" in data
        assert "documents" in data
        assert data["total_documents"] == 1
        assert data["documents"][0]["sha256"] == "abc"
        get_settings.cache_clear()

    def test_failed_downloads_excluded_from_manifest(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        manifests_dir = tmp_path / "manifests"
        results = [
            make_result("https://sltda.gov.lk/good.pdf", "good.pdf"),
            make_result("https://sltda.gov.lk/bad.pdf", "bad.pdf",
                        status=DownloadStatus.FAILED_MAGIC_BYTES),
        ]

        path = write_manifest(results, manifests_dir=manifests_dir)
        data = json.loads(path.read_text())

        assert data["total_documents"] == 1
        assert data["documents"][0]["filename"] == "good.pdf"
        get_settings.cache_clear()
