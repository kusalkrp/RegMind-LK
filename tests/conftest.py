"""
Shared pytest fixtures for sltda-mcp test suite.
All external dependencies (DB, Qdrant, Gemini) are mocked by default.
Integration tests override these fixtures to use live connections.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ─── Sample data fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def sample_document_id() -> str:
    return str(uuid4())


@pytest.fixture
def sample_registration_steps() -> list[dict]:
    return [
        {
            "id": 1,
            "category_code": "guest_house",
            "action_type": "register",
            "step_number": 1,
            "step_title": "Submit Application Form",
            "step_description": "Complete and submit the SLTDA registration application form.",
            "required_documents": ["NIC copy", "Proof of ownership", "Floor plan"],
            "estimated_duration": "2–3 working days",
            "fees": {"amount": "LKR 5,000", "currency": "LKR", "notes": "Non-refundable"},
        },
        {
            "id": 2,
            "category_code": "guest_house",
            "action_type": "register",
            "step_number": 2,
            "step_title": "Property Inspection",
            "step_description": "SLTDA inspector visits property to verify standards compliance.",
            "required_documents": [],
            "estimated_duration": "5–7 working days",
            "fees": {"amount": "LKR 2,500", "currency": "LKR", "notes": "Inspection fee"},
        },
    ]


@pytest.fixture
def sample_business_category() -> dict:
    return {
        "id": 1,
        "category_code": "eco_lodge",
        "category_name": "Eco Lodge",
        "category_group": "accommodation",
        "gazette_document_id": str(uuid4()),
        "guidelines_document_id": str(uuid4()),
        "checklist_document_id": str(uuid4()),
        "registration_document_id": str(uuid4()),
        "notes": "Minimum 5 rooms. Must comply with environmental standards.",
    }


@pytest.fixture
def sample_financial_concession() -> dict:
    return {
        "id": 1,
        "concession_name": "Tourism Sector Tax Concession",
        "concession_type": "tax",
        "applicable_to": ["classified_hotel", "boutique_villa", "eco_lodge"],
        "rate_or_terms": "7% flat income tax rate for tourism businesses",
        "conditions": "Must be registered with SLTDA",
        "effective_from": "2021-01-01",
        "circular_reference": "Banking Circular No. 07 of 2019",
        "document_id": str(uuid4()),
    }


@pytest.fixture
def sample_niche_toolkit() -> dict:
    return {
        "id": 1,
        "toolkit_code": "wellness",
        "toolkit_name": "Wellness Tourism Toolkit",
        "target_market": "Health-conscious international visitors",
        "key_activities": ["Ayurveda", "Yoga retreats", "Spa treatments", "Meditation"],
        "regulatory_notes": "Ayurvedic practitioners must hold MOH-registered qualifications.",
        "document_id": str(uuid4()),
        "summary": "Sri Lanka offers authentic Ayurvedic treatments and wellness experiences.",
        "source_text_tokens": 1500,
        "source_pages": 12,
        "extraction_confidence": "high",
    }


@pytest.fixture
def sample_qdrant_points() -> list[dict]:
    """Sample Qdrant search results."""
    return [
        {
            "id": str(uuid4()),
            "score": 0.91,
            "payload": {
                "document_id": str(uuid4()),
                "document_name": "SLTDA Registration Guidelines",
                "section_name": "Registration & Renewal",
                "document_type": "guideline",
                "language": "english",
                "chunk_index": 0,
                "chunk_text": "To register a guest house with SLTDA, the applicant must submit Form R-01 along with proof of property ownership.",
                "page_numbers": [3],
                "source_url": "https://sltda.gov.lk/downloads/registration_guidelines.pdf",
                "last_updated": "2024-01-15",
                "superseded": False,
                "ocr_extracted": False,
            },
        },
        {
            "id": str(uuid4()),
            "score": 0.87,
            "payload": {
                "document_id": str(uuid4()),
                "document_name": "SLTDA Registration Guidelines",
                "section_name": "Registration & Renewal",
                "document_type": "guideline",
                "language": "english",
                "chunk_index": 1,
                "chunk_text": "The registration fee for guest houses is LKR 5,000 for initial registration and LKR 2,500 for annual renewal.",
                "page_numbers": [4],
                "source_url": "https://sltda.gov.lk/downloads/registration_guidelines.pdf",
                "last_updated": "2024-01-15",
                "superseded": False,
                "ocr_extracted": False,
            },
        },
    ]


# ─── Mock DB pool ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db_pool():
    """Mock asyncpg connection pool."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.get_size.return_value = 15
    pool.get_idle_size.return_value = 12
    return pool, conn


@pytest.fixture
def mock_db_conn(mock_db_pool):
    _, conn = mock_db_pool
    return conn


# ─── Mock Qdrant client ───────────────────────────────────────────────────────

@pytest.fixture
def mock_qdrant_client():
    client = AsyncMock()
    client.get_collections.return_value = MagicMock(collections=[])
    client.search.return_value = []
    return client


# ─── Mock Gemini ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_gemini_embed():
    """Returns a 768-dim zero vector for any input."""
    with patch("google.generativeai.embed_content") as mock:
        mock.return_value = {"embedding": [0.0] * 768}
        yield mock


@pytest.fixture
def mock_gemini_generate():
    """Returns a canned synthesis response."""
    response = MagicMock()
    response.text = "Based on SLTDA documents, the registration process requires Form R-01 and a fee of LKR 5,000."
    with patch("google.generativeai.GenerativeModel") as mock_model_cls:
        instance = AsyncMock()
        instance.generate_content_async.return_value = response
        mock_model_cls.return_value = instance
        yield instance


# ─── Sample PDF fixture path ──────────────────────────────────────────────────

@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_pdf_path(fixtures_dir, tmp_path) -> Path:
    """Create a minimal valid PDF for testing."""
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n9\n%%EOF\n"
    )
    pdf_path = tmp_path / "test_document.pdf"
    pdf_path.write_bytes(pdf_content)
    return pdf_path


@pytest.fixture
def sample_html_as_pdf(tmp_path) -> Path:
    """A file with .pdf extension but HTML content (CAPTCHA simulation)."""
    html_path = tmp_path / "captcha.pdf"
    html_path.write_bytes(b"<html><body>Please verify you are human</body></html>")
    return html_path
