"""
LegislationExtractor — extracts sections and subsections from Acts.
Preserves section numbering for get_tourism_act_provisions tool.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(\d+)\.\s+([A-Z][^\n]{2,80})", re.MULTILINE
)


def _parse_sections(text: str) -> list[dict]:
    matches = list(_SECTION_RE.finditer(text))
    sections = []
    for i, match in enumerate(matches):
        section_number = int(match.group(1))
        section_title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        sections.append({
            "section_number": section_number,
            "section_title": section_title,
            "section_text": section_text,
        })
    return sections


class LegislationExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        sections = _parse_sections(full_text)

        logger.info("LegislationExtractor: %d sections from %s", len(sections), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"sections": sections},
            page_count=page_count,
            extraction_confidence="high",
        )
