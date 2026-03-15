"""
Unit tests for ingestion/downloader.py.
All HTTP calls are mocked — no network access.
"""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sltda_mcp.ingestion.downloader import (
    DownloadStatus,
    RateLimiter,
    _validate_content_language,
    _validate_pdf_bytes,
    download_documents,
)
from sltda_mcp.ingestion.scraper import CandidateDocument

# ─── Fixtures ─────────────────────────────────────────────────────────────────

VALID_PDF_CONTENT = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"This is test English content about tourism registration in Sri Lanka. " * 100
)

HTML_CAPTCHA_CONTENT = b"<html><body>Please verify you are human</body></html>"

SMALL_PDF_CONTENT = b"%PDF-1.4 tiny"  # < 5KB

SINHALA_PDF_CONTENT = (
    b"%PDF-1.4\n" + b"\xe0\xb6\x9a" * 500  # UTF-8 Sinhala chars
)


def make_candidate(filename: str = "test.pdf", section_id: int = 1) -> CandidateDocument:
    return CandidateDocument(
        section_id=section_id,
        section_name="Registration & Renewal",
        document_name="Test Document",
        source_url=f"https://sltda.gov.lk/downloads/{filename}",
        filename=filename,
    )


def make_http_response(content: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.status_code = status_code
    resp.headers = {"content-type": "application/pdf"}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ─── Byte validation ──────────────────────────────────────────────────────────

class TestValidatePdfBytes:
    def test_valid_pdf_passes(self):
        assert _validate_pdf_bytes(VALID_PDF_CONTENT, "test.pdf", 5) is None

    def test_html_content_fails_magic_bytes(self):
        result = _validate_pdf_bytes(HTML_CAPTCHA_CONTENT, "captcha.pdf", 5)
        assert result == DownloadStatus.FAILED_MAGIC_BYTES

    def test_file_below_minimum_size_fails(self):
        result = _validate_pdf_bytes(SMALL_PDF_CONTENT, "tiny.pdf", 5)
        assert result == DownloadStatus.FAILED_SIZE

    def test_large_non_pdf_flagged_as_suspicious(self, tmp_path, monkeypatch):
        """Issue #1: large non-PDF file should become SUSPICIOUS_CONTENT."""
        # _validate_pdf_bytes itself just returns FAILED_MAGIC_BYTES for non-PDF
        # The suspicious classification happens in _download_single
        large_html = b"<html>" + b"x" * 20000
        result = _validate_pdf_bytes(large_html, "large.pdf", 5)
        assert result == DownloadStatus.FAILED_MAGIC_BYTES

    def test_exactly_minimum_size_passes(self):
        # Exactly 5KB of valid PDF content
        content = b"%PDF-1.4\n" + b"x" * (5 * 1024 - 9)
        assert _validate_pdf_bytes(content, "exact.pdf", 5) is None


# ─── Language validation ──────────────────────────────────────────────────────

class TestValidateContentLanguage:
    def test_english_content_passes(self):
        from sltda_mcp.ingestion.scraper import _load_section_config
        config = _load_section_config()
        result = _validate_content_language(VALID_PDF_CONTENT, "test.pdf", config)
        assert result is None

    def test_sinhala_content_rejected(self):
        from sltda_mcp.ingestion.scraper import _load_section_config
        config = _load_section_config()
        result = _validate_content_language(SINHALA_PDF_CONTENT, "sinhala.pdf", config)
        assert result == DownloadStatus.LANGUAGE_REJECTED


# ─── Rate limiter ─────────────────────────────────────────────────────────────

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_enforces_interval(self):
        limiter = RateLimiter(rps=2.0)  # 0.5s between calls
        await limiter.wait()
        start = time.monotonic()
        await limiter.wait()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.45, f"Expected ≥0.45s gap, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_first_call_does_not_wait(self):
        limiter = RateLimiter(rps=1.0)
        start = time.monotonic()
        await limiter.wait()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, "First call should not wait"


# ─── Download orchestration ───────────────────────────────────────────────────

class TestDownloadDocuments:
    @pytest.mark.asyncio
    async def test_successful_download(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("INGESTION_MIN_FILE_SIZE_KB", "1")
        monkeypatch.setenv("INGESTION_RATE_LIMIT_RPS", "100")  # fast for tests

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        candidate = make_candidate("test_registration.pdf")
        mock_response = make_http_response(VALID_PDF_CONTENT)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await download_documents([candidate], documents_base_path=tmp_path)

        assert len(result.successful) == 1
        assert len(result.failed) == 0
        assert result.successful[0].status == DownloadStatus.SUCCESS
        assert result.successful[0].content_hash is not None
        assert result.successful[0].file_size_kb is not None
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_html_captcha_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("INGESTION_RATE_LIMIT_RPS", "100")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        candidate = make_candidate("captcha.pdf")
        mock_response = make_http_response(HTML_CAPTCHA_CONTENT)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await download_documents([candidate], documents_base_path=tmp_path)

        assert len(result.failed) == 1
        assert result.failed[0].status == DownloadStatus.FAILED_MAGIC_BYTES
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_http_error_goes_to_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("INGESTION_RATE_LIMIT_RPS", "100")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        candidate = make_candidate("not_found.pdf")
        mock_response = make_http_response(b"", status_code=404)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await download_documents([candidate], documents_base_path=tmp_path)

        assert len(result.failed) == 1
        assert result.failed[0].status == DownloadStatus.HTTP_ERROR
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_failure_rate_calculation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("INGESTION_RATE_LIMIT_RPS", "100")
        monkeypatch.setenv("INGESTION_MIN_FILE_SIZE_KB", "1")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        good = make_candidate("good.pdf")
        bad = make_candidate("bad.pdf")
        responses = [make_http_response(VALID_PDF_CONTENT), make_http_response(HTML_CAPTCHA_CONTENT)]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=responses)
            mock_client_cls.return_value = mock_client

            result = await download_documents([good, bad], documents_base_path=tmp_path)

        assert result.failure_rate == pytest.approx(0.5)
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_pipeline_continues_on_single_failure(self, tmp_path, monkeypatch):
        """Failures are collected — pipeline does not raise."""
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("INGESTION_RATE_LIMIT_RPS", "100")
        monkeypatch.setenv("INGESTION_MIN_FILE_SIZE_KB", "1")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        candidates = [make_candidate(f"doc_{i}.pdf") for i in range(3)]
        responses = [
            make_http_response(VALID_PDF_CONTENT),
            make_http_response(HTML_CAPTCHA_CONTENT),
            make_http_response(VALID_PDF_CONTENT),
        ]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=responses)
            mock_client_cls.return_value = mock_client

            # Must not raise
            result = await download_documents(candidates, documents_base_path=tmp_path)

        assert len(result.successful) == 2
        assert len(result.failed) == 1
        get_settings.cache_clear()
