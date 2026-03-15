"""
GazetteExtractor — extracts clause hierarchy from official gazette PDFs.
Parses section → subsection → clause numbering.
Table extraction enabled (embedded tables extracted as JSON sub-objects).
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_CLAUSE_RE = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+)*)\s*[.\)]\s+([^\n]+)", re.MULTILINE
)


def _parse_clauses(text: str) -> list[dict]:
    matches = list(_CLAUSE_RE.finditer(text))
    clauses = []
    for i, match in enumerate(matches):
        clause_number = match.group(1)
        clause_title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        clause_text = text[start:end].strip()
        clauses.append({
            "clause_number": clause_number,
            "clause_title": clause_title,
            "clause_text": clause_text,
            "page_numbers": [],
        })
    return clauses


class GazetteExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        all_tables: list = []
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]
            for page in pdf.pages:
                all_tables.extend(page.extract_tables() or [])

        full_text = "\n".join(page_texts)
        clauses = _parse_clauses(full_text)

        logger.info("GazetteExtractor: %d clauses from %s", len(clauses), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"clauses": clauses, "tables": all_tables},
            page_count=page_count,
            extraction_confidence="high",
        )
