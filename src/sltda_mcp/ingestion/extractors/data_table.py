"""
DataTableExtractor — extracts structured tables from monthly arrivals reports
and accommodation services tables.
Uses tabula-py as primary; falls back to pdfplumber on ImportError or failure.
Post-extraction: validates non-null category_name per row (Issue #7 mitigation).
"""

import logging
from pathlib import Path
from uuid import UUID

import pdfplumber

from sltda_mcp.ingestion.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


def _extract_tables_tabula(pdf_path: Path) -> list[list]:
    """Attempt table extraction via tabula-py."""
    try:
        import tabula  # type: ignore[import-untyped]

        dfs = tabula.read_pdf(str(pdf_path), pages="all", silent=True)
        tables = []
        for df in dfs:
            if df is not None and not df.empty:
                # Convert to list-of-lists with header
                header = list(df.columns)
                rows = df.values.tolist()
                tables.append([header] + rows)
        return tables
    except Exception as exc:
        logger.warning("tabula extraction failed for %s: %s — falling back to pdfplumber", pdf_path.name, exc)
        return []


def _extract_tables_pdfplumber(pdf_path: Path) -> list[list]:
    """Fallback table extraction via pdfplumber."""
    tables: list[list] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables() or [])
    return tables


def _validate_tables(tables: list[list]) -> list[list]:
    """Filter out tables whose first column (category_name) is all-null."""
    valid = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        data_rows = table[1:]
        if any(row and row[0] for row in data_rows):
            valid.append(table)
    return valid


class DataTableExtractor(BaseExtractor):
    def extract(self, pdf_path: Path, document_id: UUID) -> ExtractionResult:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            page_texts = [p.extract_text() or "" for p in pdf.pages]

        full_text = "\n".join(page_texts)

        tables = _extract_tables_tabula(pdf_path)
        if not tables:
            tables = _extract_tables_pdfplumber(pdf_path)

        valid_tables = _validate_tables(tables)
        logger.info(
            "DataTableExtractor: %d valid tables (of %d) from %s",
            len(valid_tables), len(tables), pdf_path.name,
        )
        return ExtractionResult(
            text=full_text,
            structured_data={"tables": valid_tables},
            page_count=page_count,
            extraction_confidence="high" if valid_tables else "medium",
        )
