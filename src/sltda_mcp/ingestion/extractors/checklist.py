"""
ChecklistExtractor — parses numbered checklist items.
Detects mandatory vs optional items via keyword matching.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.exceptions import ValidationError
from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_ITEM_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s+([^\n]+)", re.MULTILINE)
_MANDATORY_RE = re.compile(r"\b(mandatory|required|must|shall)\b", re.IGNORECASE)
_OPTIONAL_RE = re.compile(r"\b(optional|if applicable|where applicable)\b", re.IGNORECASE)
_MIN_ITEMS = 1


def _parse_checklist_items(text: str) -> list[dict]:
    """Parse numbered checklist items from text."""
    matches = list(_ITEM_RE.finditer(text))
    items = []
    for i, match in enumerate(matches):
        item_number = int(match.group(1))
        item_text = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        notes = text[start:end].strip()
        full_item = item_text + " " + notes

        is_mandatory = (
            bool(_MANDATORY_RE.search(full_item)) and not bool(_OPTIONAL_RE.search(full_item))
        )
        items.append({
            "item_number": item_number,
            "document_name": item_text,
            "description": notes,
            "is_mandatory": is_mandatory,
            "notes": notes,
        })
    return items


class ChecklistExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        items = _parse_checklist_items(full_text)

        if len(items) < _MIN_ITEMS:
            raise ValidationError(
                f"ChecklistExtractor: minimum {_MIN_ITEMS} items required, "
                f"found {len(items)} in {pdf_path.name}"
            )

        logger.info("ChecklistExtractor: %d items extracted from %s", len(items), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"items": items},
            page_count=page_count,
            extraction_confidence="high",
        )
