"""
Smoke tests — all 14 MCP tools, DB and RAG fully mocked.

Run with: pytest tests/smoke/smoke_tests.py -v
These tests act as a post-ingestion gate: every tool must return
a valid envelope with status != 'error'.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult


# ─── Shared fixtures ──────────────────────────────────────────────────────────

def _make_conn(**kwargs):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=kwargs.get("row"))
    conn.fetch = AsyncMock(return_value=kwargs.get("rows", []))
    conn.fetchval = AsyncMock(return_value=kwargs.get("val", 1))
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _rag_result(answer="Smoke answer.") -> RagResult:
    return RagResult(
        answer=answer,
        confidence="high",
        chunks=[
            RagChunk(
                chunk_text="Smoke content.",
                document_id="doc-s",
                document_name="Smoke Doc",
                source_url="https://sltda.gov.lk/smoke.pdf",
                chunk_index=0,
                score=0.85,
            )
        ],
        synthesis_used=True,
        query_expanded=False,
    )


_CATEGORY_ROW = {
    "category_code": "HOTEL",
    "category_name": "Hotel",
    "category_group": "Accommodation",
    "gazette_url": "https://sltda.gov.lk/gazette.pdf",
    "checklist_url": "https://sltda.gov.lk/checklist.pdf",
}

_STEP_ROWS = [
    {
        "step_number": 1,
        "step_title": "Apply",
        "step_description": "Submit form.",
        "required_documents": ["NIC"],
        "fees": "LKR 2500",
        "online_link": None,
    }
]

_CHECKLIST_ROWS = [
    {
        "step_number": 1,
        "step_title": "Submit Application",
        "step_description": "Submit form.",
        "required_documents": ["NIC", "Business Registration"],
    }
]

_CONCESSION_ROWS = [
    {
        "concession_name": "Low Interest Loan",
        "concession_type": "interest_rate",
        "applicable_to": ["hotel"],
        "rate_or_terms": "4%",
        "conditions": "Licensed only",
        "circular_reference": "CB/2020/01",
        "circular_url": "https://sltda.gov.lk/fin.pdf",
    }
]

_TAX_ROWS = [
    {
        "concession_name": "IT Holiday",
        "rate_or_terms": "0%",
        "conditions": "New hotels only",
        "applicable_to": ["hotel"],
        "doc_url": "https://sltda.gov.lk/tax.pdf",
    }
]

_ARRIVALS_ROW = {
    "document_name": "Monthly Arrivals Jan 2025",
    "source_url": "https://sltda.gov.lk/jan25.pdf",
    "content_as_of": None,
    "extracted_data": {"arrivals": 150000},
    "document_id": "doc-arr",
    "section_id": 9,
}

_ANNUAL_ROW = {
    "document_name": "Annual Report 2023",
    "source_url": "https://sltda.gov.lk/ar2023.pdf",
    "content_as_of": None,
    "extracted_data": {"highlights": "Record arrivals."},
    "document_id": "doc-ar",
    "section_id": 10,
    "file_size_kb": 4200,
}

_NICHE_ROWS = [
    {
        "toolkit_code": "ECO",
        "toolkit_name": "Eco Tourism",
        "target_market": "Nature",
        "extraction_confidence": "high",
    }
]

_TOOLKIT_ROW = {
    "toolkit_code": "ECO",
    "toolkit_name": "Eco Tourism Toolkit",
    "target_market": "Nature enthusiasts",
    "key_activities": ["trekking"],
    "regulatory_notes": "Permit required.",
    "summary": "Eco summary.",
    "source_text_tokens": 900,
    "source_pages": 8,
    "extraction_confidence": "high",
    "source_url": "https://sltda.gov.lk/eco.pdf",
}

_TDL_ROWS = [
    {
        "concession_name": "TDL Rate",
        "rate_or_terms": "1%",
        "conditions": "All licensed businesses",
        "circular_reference": "SLTDA/TDL/2020",
        "doc_url": "https://sltda.gov.lk/tdl.pdf",
        "document_name": "TDL Guide",
    }
]

_FORM_ROWS: list = []

_INVEST_STEP_ROWS: list = []
_INVEST_FORM_ROWS: list = []

_ACCOMMODATION_ROW = {
    "category_code": "HOTEL",
    "category_name": "Hotel",
    "category_group": "Accommodation",
    "notes": "Must comply with star classification.",
    "gazette_url": "https://sltda.gov.lk/gazette.pdf",
    "gazette_name": "Hotel Standards Gazette",
    "guidelines_url": "https://sltda.gov.lk/guidelines.pdf",
    "guidelines_name": "Hotel Guidelines",
    "checklist_url": "https://sltda.gov.lk/checklist.pdf",
    "checklist_name": "Hotel Checklist",
    "registration_url": "https://sltda.gov.lk/reg.pdf",
}


# ─── Envelope validator ───────────────────────────────────────────────────────

_REQUIRED_FIELDS = {"status", "tool", "data", "source", "disclaimer", "generated_at"}


def _assert_valid_envelope(result: dict, tool_name: str) -> None:
    assert isinstance(result, dict), f"{tool_name}: result must be a dict"
    missing = _REQUIRED_FIELDS - result.keys()
    assert not missing, f"{tool_name}: missing envelope fields {missing}"
    assert result["status"] in ("success", "not_found"), \
        f"{tool_name}: unexpected status '{result['status']}'"
    assert result["tool"] == tool_name, \
        f"{tool_name}: wrong tool name in envelope"


# ─── Tool 1: get_registration_requirements ────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_registration_requirements():
    from sltda_mcp.mcp_server.tools.registration import get_registration_requirements
    conn = _make_conn(row=_CATEGORY_ROW, rows=_STEP_ROWS)
    with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
        result = await get_registration_requirements("HOTEL")
    _assert_valid_envelope(result, "get_registration_requirements")


# ─── Tool 2: get_accommodation_standards ──────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_accommodation_standards():
    from sltda_mcp.mcp_server.tools.registration import get_accommodation_standards
    conn = _make_conn(row=_ACCOMMODATION_ROW)
    with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
        result = await get_accommodation_standards("hotel")
    _assert_valid_envelope(result, "get_accommodation_standards")


# ─── Tool 3: get_registration_checklist ───────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_registration_checklist():
    from sltda_mcp.mcp_server.tools.registration import get_registration_checklist
    # get_registration_checklist uses fetchval (existence check) then fetch (steps)
    conn = _make_conn(val=1, rows=_CHECKLIST_ROWS)
    with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
        result = await get_registration_checklist("HOTEL")
    _assert_valid_envelope(result, "get_registration_checklist")


# ─── Tool 4: get_financial_concessions ────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_financial_concessions():
    from sltda_mcp.mcp_server.tools.financial import get_financial_concessions
    conn = _make_conn(rows=_CONCESSION_ROWS)
    with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
        result = await get_financial_concessions()
    _assert_valid_envelope(result, "get_financial_concessions")


# ─── Tool 5: get_tdl_information ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_tdl_information():
    from sltda_mcp.mcp_server.tools.financial import get_tdl_information
    conn = _make_conn()
    conn.fetch = AsyncMock(side_effect=[_TDL_ROWS, _FORM_ROWS])
    with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
        result = await get_tdl_information("overview")
    _assert_valid_envelope(result, "get_tdl_information")


# ─── Tool 6: get_tax_rate ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_tax_rate():
    from sltda_mcp.mcp_server.tools.financial import get_tax_rate
    conn = _make_conn(rows=_TAX_ROWS)
    with patch("sltda_mcp.mcp_server.tools.financial.acquire", return_value=_mock_acquire(conn)):
        result = await get_tax_rate()
    _assert_valid_envelope(result, "get_tax_rate")


# ─── Tool 7: get_latest_arrivals_report ───────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_latest_arrivals_report():
    from sltda_mcp.mcp_server.tools.statistics import get_latest_arrivals_report
    conn = _make_conn(row=_ARRIVALS_ROW)
    with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
        result = await get_latest_arrivals_report("monthly")
    _assert_valid_envelope(result, "get_latest_arrivals_report")


# ─── Tool 8: get_annual_report ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_annual_report():
    from sltda_mcp.mcp_server.tools.statistics import get_annual_report
    conn = _make_conn(row=_ANNUAL_ROW)
    with patch("sltda_mcp.mcp_server.tools.statistics.acquire", return_value=_mock_acquire(conn)):
        result = await get_annual_report(2023)
    _assert_valid_envelope(result, "get_annual_report")


# ─── Tool 9: get_strategic_plan ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_strategic_plan():
    from sltda_mcp.mcp_server.tools.strategy import get_strategic_plan
    with patch(
        "sltda_mcp.mcp_server.tools.strategy.run_rag",
        new_callable=AsyncMock,
        return_value=_rag_result(),
    ):
        result = await get_strategic_plan("What are the tourism goals for 2025?")
    _assert_valid_envelope(result, "get_strategic_plan")


# ─── Tool 10: get_tourism_act_provisions ──────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_tourism_act_provisions():
    from sltda_mcp.mcp_server.tools.strategy import get_tourism_act_provisions
    with patch(
        "sltda_mcp.mcp_server.tools.strategy.run_rag",
        new_callable=AsyncMock,
        return_value=_rag_result(),
    ):
        result = await get_tourism_act_provisions("registration offences")
    _assert_valid_envelope(result, "get_tourism_act_provisions")


# ─── Tool 11: get_niche_categories ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_niche_categories():
    from sltda_mcp.mcp_server.tools.niche import get_niche_categories
    conn = _make_conn(rows=_NICHE_ROWS)
    with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(conn)):
        result = await get_niche_categories()
    _assert_valid_envelope(result, "get_niche_categories")


# ─── Tool 12: get_niche_toolkit ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_niche_toolkit():
    from sltda_mcp.mcp_server.tools.niche import get_niche_toolkit
    conn = _make_conn(row=_TOOLKIT_ROW)
    with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=_mock_acquire(conn)):
        result = await get_niche_toolkit("ECO", detail_level="summary")
    _assert_valid_envelope(result, "get_niche_toolkit")


# ─── Tool 13: get_investment_process ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_get_investment_process():
    from sltda_mcp.mcp_server.tools.investor import get_investment_process
    conn = _make_conn()
    conn.fetch = AsyncMock(side_effect=[_INVEST_STEP_ROWS, _INVEST_FORM_ROWS])
    with (
        patch("sltda_mcp.mcp_server.tools.investor.acquire", return_value=_mock_acquire(conn)),
        patch(
            "sltda_mcp.mcp_server.tools.investor.run_rag",
            new_callable=AsyncMock,
            return_value=_rag_result(),
        ),
    ):
        result = await get_investment_process()
    _assert_valid_envelope(result, "get_investment_process")


# ─── Tool 14: search_sltda_resources ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_search_sltda_resources():
    from sltda_mcp.mcp_server.tools.investor import search_sltda_resources
    with patch(
        "sltda_mcp.mcp_server.tools.investor.run_rag",
        new_callable=AsyncMock,
        return_value=_rag_result(),
    ):
        result = await search_sltda_resources("SLTDA registration hotel")
    _assert_valid_envelope(result, "search_sltda_resources")
