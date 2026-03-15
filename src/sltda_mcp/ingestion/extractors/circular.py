"""
CircularExtractor — extracts financial concession records from banking circulars.
Parses concession name, type, applicable business types, rate/terms, effective date.
Raises ValidationError if no fee/rate information can be found.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.exceptions import ValidationError
from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"(?:name|concession.?name)\s*:\s*(.+)", re.IGNORECASE)
_TYPE_RE = re.compile(r"(?:type|concession.?type)\s*:\s*(.+)", re.IGNORECASE)
_APPLICABLE_RE = re.compile(r"(?:applicable.?to|applicable)\s*:\s*(.+)", re.IGNORECASE)
_RATE_RE = re.compile(
    r"(?:rate/terms|rate_or_terms|rate|terms|interest rate)\s*:\s*(.+)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"(?:effective.?date|date)\s*:\s*(.+)", re.IGNORECASE)
_INLINE_RATE_RE = re.compile(
    r"(?:not exceeding|at|@)\s*([\d.]+\s*%[^,\n]*|LKR\s*[\d,.]+[^,\n]*)",
    re.IGNORECASE,
)


def _parse_concessions(text: str) -> list[dict]:
    """Parse concession fields from circular text."""
    name_m = _NAME_RE.search(text)
    type_m = _TYPE_RE.search(text)
    applicable_m = _APPLICABLE_RE.search(text)
    rate_m = _RATE_RE.search(text)
    date_m = _DATE_RE.search(text)

    # Fallback: look for inline rate pattern if no explicit Rate: field
    rate_str = rate_m.group(1).strip() if rate_m else ""
    if not rate_str:
        inline = _INLINE_RATE_RE.search(text)
        rate_str = inline.group(1).strip() if inline else ""

    concession = {
        "concession_name": name_m.group(1).strip() if name_m else "Unknown Concession",
        "concession_type": type_m.group(1).strip() if type_m else "financial_concession",
        "applicable_business_types": applicable_m.group(1).strip() if applicable_m else "",
        "rate_or_terms": rate_str,
        "effective_date": date_m.group(1).strip() if date_m else "",
    }
    return [concession]


class CircularExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        concessions = _parse_concessions(full_text)

        if not any(c["rate_or_terms"] for c in concessions):
            raise ValidationError(
                f"CircularExtractor: no fee or rate information found in {pdf_path.name}"
            )

        logger.info(
            "CircularExtractor: %d concession record(s) from %s", len(concessions), pdf_path.name
        )
        return ExtractionResult(
            text=full_text,
            structured_data={"concessions": concessions},
            page_count=page_count,
            extraction_confidence="high",
        )
