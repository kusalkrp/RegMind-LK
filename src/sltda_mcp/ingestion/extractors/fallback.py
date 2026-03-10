"""
FallbackExtractor — generic extractor for unclassified or low-confidence documents.
Triggers Tesseract OCR when text density is below threshold.
Documents with < 100 chars even after OCR are classified as unextractable.
"""

import logging
from pathlib import Path
from uuid import UUID

import pdfplumber
import pytesseract

from sltda_mcp.exceptions import ExtractionError
from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_OCR_TRIGGER_CHARS_PER_PAGE = 200
_UNEXTRACTABLE_TOTAL_CHARS = 100


def _ocr_page_text(page) -> str:
    """Rasterise a PDF page and run Tesseract OCR (English only)."""
    try:
        img = page.to_image(resolution=300).original
        return pytesseract.image_to_string(img, lang="eng")
    except Exception as exc:
        logger.warning("OCR failed for page: %s", exc)
        return ""


class FallbackExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

            avg_chars = sum(len(t) for t in page_texts) / max(page_count, 1)
            ocr_used = False

            if avg_chars < _OCR_TRIGGER_CHARS_PER_PAGE:
                logger.info(
                    "FallbackExtractor: low text density (%.0f chars/page) — triggering OCR for %s",
                    avg_chars,
                    pdf_path.name,
                )
                ocr_used = True
                page_texts = [_ocr_page_text(p) for p in pdf.pages]

        full_text = "\n".join(page_texts).strip()

        if len(full_text) < _UNEXTRACTABLE_TOTAL_CHARS:
            logger.error(
                "FallbackExtractor: document is unextractable (%d chars) — %s",
                len(full_text),
                pdf_path.name,
            )
            raise ExtractionError(
                f"Document is unextractable: {pdf_path.name} "
                f"(extracted {len(full_text)} chars even after OCR)"
            )

        logger.info(
            "FallbackExtractor: %d chars extracted from %s (ocr=%s)",
            len(full_text),
            pdf_path.name,
            ocr_used,
        )
        return ExtractionResult(
            text=full_text,
            structured_data=None,
            page_count=page_count,
            extraction_confidence="low" if ocr_used else "medium",
            ocr_used=ocr_used,
        )
