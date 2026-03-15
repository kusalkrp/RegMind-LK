"""
Base extractor class and shared extraction utilities.
All extractor implementations inherit from BaseExtractor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pdfplumber


@dataclass
class ExtractionResult:
    text: str
    structured_data: dict | None
    page_count: int
    extraction_confidence: str  # 'high' | 'medium' | 'low'
    ocr_used: bool = False


class BaseExtractor(ABC):
    @abstractmethod
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        """
        Extract content from a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            document_id: UUID of the document record (for logging).

        Returns:
            ExtractionResult with text and optional structured data.

        Raises:
            sltda_mcp.exceptions.ValidationError: If extracted content fails minimum quality.
            sltda_mcp.exceptions.ExtractionError: If document cannot be processed.
        """
        ...


def extract_pages_text(pdf_path: Path) -> tuple[list[str], int]:
    """Open PDF and extract text per page. Returns (page_texts, page_count)."""
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        page_texts = [p.extract_text() or "" for p in pdf.pages]
    return page_texts, page_count


def extract_pages_tables(pdf_path: Path) -> tuple[list[list], int]:
    """Open PDF and extract all tables across all pages."""
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        all_tables: list[list] = []
        for page in pdf.pages:
            all_tables.extend(page.extract_tables() or [])
    return all_tables, page_count
