"""
Unit tests for ingestion/extractors/*.py.
All pdfplumber calls are mocked — no real PDF files required.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from sltda_mcp.exceptions import ExtractionError, ValidationError
from sltda_mcp.ingestion.extractors.checklist import ChecklistExtractor
from sltda_mcp.ingestion.extractors.circular import CircularExtractor
from sltda_mcp.ingestion.extractors.fallback import FallbackExtractor
from sltda_mcp.ingestion.extractors.steps import StepsExtractor
from sltda_mcp.ingestion.extractors.toolkit import ToolkitExtractor


# ─── Test content fixtures ─────────────────────────────────────────────────────

FOUR_STEPS_TEXT = """REGISTRATION PROCEDURE FOR TOURIST ACCOMMODATION

1. Submit Application
Fill in the application form available at the SLTDA office.
Complete all sections of the form carefully.

2. Document Verification
Submit all required supporting documents as listed in the checklist.

3. Site Inspection
An SLTDA inspector will visit your premises to verify compliance.

4. Fee Payment
Pay the applicable registration fee at the SLTDA cashier."""

ONE_STEP_TEXT = """1. Submit Application
This is the only step described in this document."""

CHECKLIST_TEXT = """REQUIRED DOCUMENTS FOR REGISTRATION

1. Completed Application Form (Mandatory - must be signed by proprietor)
2. Valid Business Registration Certificate (Required document)
3. Site Plan or Floor Plan (Optional - if applicable to the premises)
4. Recent Utility Bill (Mandatory - not older than 3 months)"""

CIRCULAR_TEXT = """BANKING CIRCULAR No. 07/2019

FINANCIAL CONCESSIONS FOR TOURISM ESTABLISHMENTS

Name: Tourism Business Development Loan
Type: interest_rate_concession
Applicable to: Hotels, Guest Houses
Rate/Terms: Interest rate not exceeding 12% per annum
Effective Date: 01/01/2019"""

TOOLKIT_SHORT_TEXT = "Wellness Tourism Toolkit\n\nIntroduction\nBrief text."

TOOLKIT_LONG_TEXT = (
    "Wellness Tourism Toolkit\n\n"
    + "This is detailed content about wellness tourism activities and regulations. " * 200
)


# ─── Mock helpers ──────────────────────────────────────────────────────────────

def _make_mock_pdf(page_texts: list[str]):
    """Build a mock pdfplumber PDF context manager with controlled page text."""
    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    pages = []
    for text in page_texts:
        page = MagicMock()
        page.extract_text.return_value = text
        page.extract_tables.return_value = []
        pages.append(page)
    mock_pdf.pages = pages
    return mock_pdf


# ─── StepsExtractor ───────────────────────────────────────────────────────────

class TestStepsExtractor:
    def test_steps_extractor_minimum_steps(self):
        extractor = StepsExtractor()
        mock_pdf = _make_mock_pdf([ONE_STEP_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            with pytest.raises(ValidationError, match="minimum"):
                extractor.extract(Path("test.pdf"), uuid4())

    def test_steps_extractor_happy_path(self):
        extractor = StepsExtractor()
        mock_pdf = _make_mock_pdf([FOUR_STEPS_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("test.pdf"), uuid4())

        steps = result.structured_data["steps"]
        assert len(steps) == 4
        assert steps[0]["step_number"] == 1
        assert steps[1]["step_number"] == 2
        assert steps[2]["step_number"] == 3
        assert steps[3]["step_number"] == 4
        assert all(s["step_title"] for s in steps)
        assert result.extraction_confidence == "high"

    def test_steps_extractor_result_has_text(self):
        extractor = StepsExtractor()
        mock_pdf = _make_mock_pdf([FOUR_STEPS_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("test.pdf"), uuid4())

        assert len(result.text) > 0
        assert result.page_count == 1


# ─── ChecklistExtractor ───────────────────────────────────────────────────────

class TestChecklistExtractor:
    def test_checklist_extractor_mandatory_flag(self):
        extractor = ChecklistExtractor()
        mock_pdf = _make_mock_pdf([CHECKLIST_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("checklist.pdf"), uuid4())

        items = result.structured_data["items"]
        assert len(items) == 4

        by_number = {i["item_number"]: i["is_mandatory"] for i in items}
        assert by_number[1] is True   # "Mandatory"
        assert by_number[2] is True   # "Required"
        assert by_number[3] is False  # "Optional"
        assert by_number[4] is True   # "Mandatory"

    def test_checklist_extractor_returns_document_names(self):
        extractor = ChecklistExtractor()
        mock_pdf = _make_mock_pdf([CHECKLIST_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("checklist.pdf"), uuid4())

        for item in result.structured_data["items"]:
            assert item["document_name"]  # non-empty


# ─── CircularExtractor ────────────────────────────────────────────────────────

class TestCircularExtractor:
    def test_circular_extractor_fee_validation(self):
        extractor = CircularExtractor()
        mock_pdf = _make_mock_pdf([CIRCULAR_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("circular.pdf"), uuid4())

        records = result.structured_data["concessions"]
        assert len(records) >= 1
        assert records[0]["rate_or_terms"] != ""

    def test_circular_extractor_no_rate_raises(self):
        extractor = CircularExtractor()
        mock_pdf = _make_mock_pdf(["BANKING CIRCULAR\n\nSome text without any rate information."])

        with patch("pdfplumber.open", return_value=mock_pdf):
            with pytest.raises(ValidationError):
                extractor.extract(Path("bad_circular.pdf"), uuid4())

    def test_circular_extractor_captures_name(self):
        extractor = CircularExtractor()
        mock_pdf = _make_mock_pdf([CIRCULAR_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("circular.pdf"), uuid4())

        assert result.structured_data["concessions"][0]["concession_name"] != ""


# ─── ToolkitExtractor ─────────────────────────────────────────────────────────

class TestToolkitExtractor:
    def test_toolkit_extractor_skips_summary_low_yield(self):
        extractor = ToolkitExtractor()
        mock_pdf = _make_mock_pdf([TOOLKIT_SHORT_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("toolkit.pdf"), uuid4())

        assert result.structured_data["summary"] is None

    def test_toolkit_extractor_has_text_with_high_yield(self):
        extractor = ToolkitExtractor()
        mock_pdf = _make_mock_pdf([TOOLKIT_LONG_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("toolkit_large.pdf"), uuid4())

        # Gemini not called in unit tests — but text captured and code set
        assert len(result.text) > 100
        assert result.structured_data["toolkit_code"]

    def test_toolkit_extractor_confidence_medium_on_short(self):
        extractor = ToolkitExtractor()
        mock_pdf = _make_mock_pdf([TOOLKIT_SHORT_TEXT])

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extractor.extract(Path("toolkit.pdf"), uuid4())

        assert result.extraction_confidence == "medium"


# ─── FallbackExtractor ────────────────────────────────────────────────────────

class TestFallbackExtractor:
    def test_fallback_extractor_triggers_ocr(self):
        extractor = FallbackExtractor()
        mock_pdf = _make_mock_pdf(["A"])  # only 1 char — below 200 threshold

        with patch("pdfplumber.open", return_value=mock_pdf):
            with patch(
                "sltda_mcp.ingestion.extractors.fallback._ocr_page_text",
                return_value="Extracted text from OCR scan of the document page. Contains English tourism registration content for SLTDA compliance.",
            ):
                result = extractor.extract(Path("scanned.pdf"), uuid4())

        assert result.ocr_used is True
        assert result.extraction_confidence == "low"

    def test_unextractable_document_excluded(self):
        """< 100 chars even after OCR → ExtractionError with 'unextractable'."""
        extractor = FallbackExtractor()
        mock_pdf = _make_mock_pdf(["A"])

        with patch("pdfplumber.open", return_value=mock_pdf):
            with patch(
                "sltda_mcp.ingestion.extractors.fallback._ocr_page_text",
                return_value="",  # OCR yields nothing
            ):
                with pytest.raises(ExtractionError, match="unextractable"):
                    extractor.extract(Path("blank.pdf"), uuid4())

    def test_fallback_normal_text_no_ocr(self):
        """Sufficient text (>= 200 chars/page) → OCR not triggered."""
        extractor = FallbackExtractor()
        rich_text = "A" * 300  # 300 chars on one page
        mock_pdf = _make_mock_pdf([rich_text])

        with patch("pdfplumber.open", return_value=mock_pdf):
            with patch(
                "sltda_mcp.ingestion.extractors.fallback._ocr_page_text"
            ) as mock_ocr:
                result = extractor.extract(Path("normal.pdf"), uuid4())
                mock_ocr.assert_not_called()

        assert result.ocr_used is False
        assert result.extraction_confidence == "medium"
