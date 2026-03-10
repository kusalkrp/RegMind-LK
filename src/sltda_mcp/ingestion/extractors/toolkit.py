"""
ToolkitExtractor — extracts niche tourism toolkit content.
Issue #8 mitigation: if text yield < 800 tokens for a doc > 5 pages,
skip Gemini summary and serve URL only (summary=None).
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^(.{3,80})\n", re.MULTILINE)
_TOKEN_THRESHOLD = 800  # ~600 words


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


def _extract_toolkit_name(text: str, filename: str) -> str:
    """Extract toolkit name from first non-empty line or filename."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) > 5:
            return stripped[:200]
    return filename.replace(".pdf", "").replace("_", " ").title()


def _extract_toolkit_code(filename: str) -> str:
    """Derive toolkit code from filename."""
    stem = filename.replace(".pdf", "").lower()
    return re.sub(r"[^a-z0-9_]", "_", stem)[:32]


class ToolkitExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        token_count = _estimate_tokens(full_text)

        # Issue #8 mitigation: skip Gemini summary if text yield is too low
        summary: str | None = None
        if token_count >= _TOKEN_THRESHOLD:
            # In production this would call Gemini Flash for summarisation.
            # Summary generation is handled by the pipeline orchestrator (Phase 6),
            # not here, to avoid embedding Gemini calls in a sync extractor.
            logger.debug(
                "ToolkitExtractor: %d tokens for %s — summary eligible",
                token_count,
                pdf_path.name,
            )
        else:
            logger.info(
                "ToolkitExtractor: %d tokens < %d threshold for %s — skipping summary",
                token_count,
                _TOKEN_THRESHOLD,
                pdf_path.name,
            )

        toolkit_name = _extract_toolkit_name(full_text, pdf_path.name)
        toolkit_code = _extract_toolkit_code(pdf_path.name)

        return ExtractionResult(
            text=full_text,
            structured_data={
                "toolkit_code": toolkit_code,
                "toolkit_name": toolkit_name,
                "target_market": "",
                "key_activities": [],
                "regulatory_notes": "",
                "summary": summary,
            },
            page_count=page_count,
            extraction_confidence="high" if token_count >= _TOKEN_THRESHOLD else "medium",
        )
