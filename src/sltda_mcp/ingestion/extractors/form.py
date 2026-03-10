"""
FormExtractor — extracts fields and instructions from blank registration forms.
Low text density; used for URL serving and basic text indexing.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_FIELD_RE = re.compile(r"([A-Za-z][A-Za-z\s/()]{3,60})\s*[:_]{1,3}\s*$", re.MULTILINE)


def _extract_form_fields(text: str) -> list[str]:
    """Detect lines that look like form field labels."""
    return [m.group(1).strip() for m in _FIELD_RE.finditer(text)]


class FormExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        fields = _extract_form_fields(full_text)

        logger.info("FormExtractor: %d fields detected in %s", len(fields), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"fields": fields},
            page_count=page_count,
            extraction_confidence="medium",
        )
