"""
StepsExtractor — parses step-by-step registration processes.
Detects numbered steps, extracts title + description per step.
Raises ValidationError if fewer than 2 steps are found.
"""

import logging
import re
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.exceptions import ValidationError
from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

_STEP_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s+([^\n]+)", re.MULTILINE)
_MIN_STEPS = 2


def _parse_steps(text: str) -> list[dict]:
    """Parse numbered steps from text. Returns list of step dicts."""
    matches = list(_STEP_RE.finditer(text))
    steps = []
    for i, match in enumerate(matches):
        step_number = int(match.group(1))
        step_title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        description = text[start:end].strip()
        steps.append({
            "step_number": step_number,
            "step_title": step_title,
            "step_description": description,
            "required_documents": [],
            "fees": {},
        })
    return steps


class StepsExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)
        steps = _parse_steps(full_text)

        if len(steps) < _MIN_STEPS:
            raise ValidationError(
                f"StepsExtractor: minimum {_MIN_STEPS} steps required, found {len(steps)} "
                f"in {pdf_path.name}"
            )

        logger.info("StepsExtractor: %d steps extracted from %s", len(steps), pdf_path.name)
        return ExtractionResult(
            text=full_text,
            structured_data={"steps": steps},
            page_count=page_count,
            extraction_confidence="high",
        )
