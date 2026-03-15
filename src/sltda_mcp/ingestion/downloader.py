"""
PDF Downloader & Validator.
Downloads PDFs with rate limiting, validates magic bytes + file size,
applies secondary language content check, stores to documents/raw/.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sltda_mcp.config import get_settings
from sltda_mcp.exceptions import DownloadError
from sltda_mcp.ingestion.scraper import CandidateDocument, _has_excessive_non_latin, _load_section_config, compute_content_hash

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF"
USER_AGENT = "sltda-mcp-research/1.0 (portfolio research project)"


class DownloadStatus(str, Enum):
    SUCCESS = "success"
    FAILED_MAGIC_BYTES = "failed_magic_bytes"
    FAILED_SIZE = "failed_size"
    LANGUAGE_REJECTED = "language_rejected"
    SUSPICIOUS_CONTENT = "suspicious_content"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"


@dataclass
class DownloadResult:
    candidate: CandidateDocument
    status: DownloadStatus
    local_path: Path | None = None
    content_hash: str | None = None
    file_size_kb: int | None = None
    error: str | None = None
    http_content_type: str | None = None


@dataclass
class DownloadBatchResult:
    successful: list[DownloadResult] = field(default_factory=list)
    failed: list[DownloadResult] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        total = len(self.successful) + len(self.failed)
        return len(self.failed) / total if total > 0 else 0.0


class RateLimiter:
    """Token-bucket rate limiter for polite crawling."""

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / rps
        self._last_call = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        sleep_for = self._interval - elapsed
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        self._last_call = time.monotonic()


def _validate_pdf_bytes(content: bytes, filename: str, min_size_kb: int) -> DownloadStatus | None:
    """
    Run all byte-level validations.
    Returns a failure DownloadStatus, or None if all checks pass.
    """
    # Magic bytes check — Issue #1 mitigation
    if not content[:4].startswith(PDF_MAGIC_BYTES):
        logger.warning("Magic bytes check failed for %s (got %r)", filename, content[:8])
        return DownloadStatus.FAILED_MAGIC_BYTES

    # Minimum file size check — Issue #1 mitigation
    size_kb = len(content) / 1024
    if size_kb < min_size_kb:
        logger.warning("File too small: %s (%.1f KB < %d KB min)", filename, size_kb, min_size_kb)
        return DownloadStatus.FAILED_SIZE

    return None


def _validate_content_language(content: bytes, filename: str, config: dict) -> DownloadStatus | None:
    """
    Secondary language check: extract first 500 chars via naive decode,
    reject if > 30% non-Latin Unicode.
    Issue #1 / Section 5.2 of design doc.
    """
    try:
        # Best-effort UTF-8 decode of the raw binary for language sniffing
        # We look at bytes 200-2000 to skip the PDF header
        sample_bytes = content[200:5000]
        text = sample_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return None  # can't decode — not a language rejection

    if _has_excessive_non_latin(text, config):
        logger.warning("Content language check failed for %s (>30%% non-Latin)", filename)
        return DownloadStatus.LANGUAGE_REJECTED

    return None


async def _download_single(
    candidate: CandidateDocument,
    client: httpx.AsyncClient,
    output_dir: Path,
    rate_limiter: RateLimiter,
    settings,
    config: dict,
) -> DownloadResult:
    """Download and validate one PDF."""
    await rate_limiter.wait()
    filename = candidate.filename
    dest_path = output_dir / filename

    try:
        response = await client.get(candidate.source_url)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        return DownloadResult(
            candidate=candidate,
            status=DownloadStatus.TIMEOUT,
            error=str(exc),
        )
    except httpx.HTTPStatusError as exc:
        return DownloadResult(
            candidate=candidate,
            status=DownloadStatus.HTTP_ERROR,
            error=f"HTTP {exc.response.status_code}",
        )

    content = response.content
    content_type = response.headers.get("content-type", "")

    # Log CDN/WAF indicators for Issue #1 detection
    cf_ray = response.headers.get("cf-ray", "")
    x_cache = response.headers.get("x-cache", "")
    if cf_ray or x_cache:
        logger.debug("CDN headers for %s — CF-Ray: %s, X-Cache: %s", filename, cf_ray, x_cache)

    # Byte-level validation
    failure = _validate_pdf_bytes(content, filename, settings.ingestion_min_file_size_kb)
    if failure:
        # Issue #1: check for suspicious content (large file, near-zero text)
        if len(content) > 10 * 1024 and failure == DownloadStatus.FAILED_MAGIC_BYTES:
            return DownloadResult(
                candidate=candidate,
                status=DownloadStatus.SUSPICIOUS_CONTENT,
                error="Large non-PDF file — possible CAPTCHA or WAF intercept",
                http_content_type=content_type,
            )
        return DownloadResult(
            candidate=candidate,
            status=failure,
            error=f"Validation failed: {failure.value}",
            http_content_type=content_type,
        )

    # Language content check
    lang_failure = _validate_content_language(content, filename, config)
    if lang_failure:
        return DownloadResult(
            candidate=candidate,
            status=lang_failure,
            error="Content contains >30% non-Latin characters",
        )

    # Save to disk
    dest_path.write_bytes(content)
    content_hash = compute_content_hash(dest_path)
    size_kb = int(len(content) / 1024)

    logger.info("Downloaded: %s (%.0f KB)", filename, size_kb)
    return DownloadResult(
        candidate=candidate,
        status=DownloadStatus.SUCCESS,
        local_path=dest_path,
        content_hash=content_hash,
        file_size_kb=size_kb,
        http_content_type=content_type,
    )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=False,
)
async def _download_with_retry(
    candidate: CandidateDocument,
    client: httpx.AsyncClient,
    output_dir: Path,
    rate_limiter: RateLimiter,
    settings,
    config: dict,
) -> DownloadResult:
    return await _download_single(
        candidate, client, output_dir, rate_limiter, settings, config
    )


async def download_documents(
    candidates: list[CandidateDocument],
    documents_base_path: Path | None = None,
) -> DownloadBatchResult:
    """
    Download all candidate documents with rate limiting and validation.
    Returns a DownloadBatchResult with successful and failed lists.
    Does NOT raise on individual failures — pipeline continues.
    """
    settings = get_settings()
    config = _load_section_config()

    base = documents_base_path or Path(settings.documents_base_path)
    rate_limiter = RateLimiter(rps=settings.ingestion_rate_limit_rps)
    batch = DownloadBatchResult()

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        for candidate in candidates:
            # Resolve output directory from section slug in config
            sections_config = config.get("sections", {})
            sec_cfg = sections_config.get(candidate.section_id, {})
            section_slug = sec_cfg.get("slug", f"section_{candidate.section_id:02d}")
            output_dir = base / "raw" / section_slug
            output_dir.mkdir(parents=True, exist_ok=True)

            result = await _download_with_retry(
                candidate, client, output_dir, rate_limiter, settings, config
            )

            if result.status == DownloadStatus.SUCCESS:
                batch.successful.append(result)
            else:
                logger.warning(
                    "Download failed [%s]: %s — %s",
                    result.status.value,
                    candidate.filename,
                    result.error,
                )
                batch.failed.append(result)

    logger.info(
        "Download complete: %d succeeded, %d failed (failure rate: %.1f%%)",
        len(batch.successful),
        len(batch.failed),
        batch.failure_rate * 100,
    )
    return batch
