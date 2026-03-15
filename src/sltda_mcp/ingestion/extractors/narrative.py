"""
NarrativeExtractor — paragraph-aware extraction for strategic plans and guidelines.
No structured output — sections only for chunking and RAG indexing.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"(?:^|\n)([A-Z][A-Z\s]{4,79})\n", re.MULTILINE)


def _split_sections(text: str) -> list[dict]:
    """Split text into sections by detecting all-caps headings."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [{"heading": "Main Content", "text": text}]

    sections = []
    for i, match in enumerate(matches):
        heading = match.group(1).strip().title()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({"heading": heading, "text": text[start:end].strip()})
    return sections


class NarrativeExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        sections = _split_sections(full_text)

        logger.info("NarrativeExtractor: %d sections from %s", len(sections), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"sections": sections},
            page_count=page_count,
            extraction_confidence="high",
        )
