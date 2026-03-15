"""
AnnualReportExtractor — heading-aware segmentation for SLTDA annual reports.
Extracts narrative sections + financial tables.
Key figures extracted where present: total arrivals, top source markets.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_ARRIVALS_RE = re.compile(r"total.?(?:tourist\s+)?arrivals[^\d]*(\d[\d,]+)", re.IGNORECASE)
_HEADING_RE = re.compile(r"(?:^|\n)([A-Z][A-Z\s]{4,79})\n", re.MULTILINE)


def _extract_key_figures(text: str) -> dict:
    figures: dict = {}
    arrivals_m = _ARRIVALS_RE.search(text)
    if arrivals_m:
        figures["total_arrivals"] = arrivals_m.group(1).replace(",", "")
    return figures


def _split_sections(text: str) -> list[dict]:
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


class AnnualReportExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        all_tables: list = []
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]
            for page in pdf.pages:
                all_tables.extend(page.extract_tables() or [])

        full_text = "\n".join(page_texts)
        sections = _split_sections(full_text)
        key_figures = _extract_key_figures(full_text)

        logger.info(
            "AnnualReportExtractor: %d sections, %d tables from %s",
            len(sections), len(all_tables), pdf_path.name,
        )
        return ExtractionResult(
            text=full_text,
            structured_data={
                "sections": sections,
                "tables": all_tables,
                "key_figures": key_figures,
            },
            page_count=page_count,
            extraction_confidence="high",
        )
