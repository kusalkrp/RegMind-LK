"""
Unit tests for mcp_server/tools/registration.py.
DB is fully mocked — no real connection needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.tools.registration import (
    get_accommodation_standards,
    get_registration_checklist,
    get_registration_requirements,
)


def _mock_conn(fetchrow=None, fetch=None, fetchval=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.fetchval = AsyncMock(return_value=fetchval)
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── get_registration_requirements ───────────────────────────────────────────

class TestGetRegistrationRequirements:
    @pytest.mark.asyncio
    async def test_happy_path_returns_steps(self):
        category = {
            "category_code": "BOUTIQUE_HOTEL",
            "category_name": "Boutique Hotel",
            "category_group": "accommodation",
            "gazette_url": "https://sltda.gov.lk/gazette.pdf",
            "checklist_url": "https://sltda.gov.lk/checklist.pdf",
        }
        steps = [
            {"step_number": 1, "step_title": "Apply", "step_description": "Submit form",
             "required_documents": ["NIC copy"], "fees": {"application": 500}},
            {"step_number": 2, "step_title": "Inspection", "step_description": "SLTDA visit",
             "required_documents": ["site plan"], "fees": {}},
            {"step_number": 3, "step_title": "Pay", "step_description": "Pay fee",
             "required_documents": [], "fees": {"licence": 5000}},
            {"step_number": 4, "step_title": "Collect", "step_description": "Collect licence",
             "required_documents": [], "fees": {}},
        ]
        conn = _mock_conn(fetchrow=category, fetch=steps)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_requirements("boutique_hotel", "register")

        assert result["status"] == "success"
        assert result["tool"] == "get_registration_requirements"
        assert result["data"]["step_count"] == 4
        assert len(result["data"]["steps"]) == 4

    @pytest.mark.asyncio
    async def test_unknown_business_type_returns_not_found(self):
        conn = _mock_conn(fetchrow=None)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_requirements("flying_carpet")

        assert result["status"] == "not_found"
        assert "flying_carpet" in result["data"]["message"]

    @pytest.mark.asyncio
    async def test_defaults_language_to_english(self):
        category = {
            "category_code": "HOTEL",
            "category_name": "Hotel",
            "category_group": "accommodation",
            "gazette_url": None,
            "checklist_url": None,
        }
        steps = [
            {"step_number": 1, "step_title": "A", "step_description": "desc",
             "required_documents": [], "fees": {}},
            {"step_number": 2, "step_title": "B", "step_description": "desc",
             "required_documents": [], "fees": {}},
        ]
        conn = _mock_conn(fetchrow=category, fetch=steps)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_requirements("hotel")

        assert result["data"]["language"] == "english"

    @pytest.mark.asyncio
    async def test_envelope_has_required_fields(self):
        category = {
            "category_code": "HOTEL",
            "category_name": "Hotel",
            "category_group": "accommodation",
            "gazette_url": None,
            "checklist_url": None,
        }
        conn = _mock_conn(fetchrow=category, fetch=[
            {"step_number": 1, "step_title": "Apply", "step_description": "desc",
             "required_documents": [], "fees": {}},
            {"step_number": 2, "step_title": "Pay", "step_description": "pay",
             "required_documents": [], "fees": {}},
        ])

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_requirements("hotel")

        for field in ("status", "tool", "data", "source", "disclaimer", "generated_at"):
            assert field in result, f"Envelope missing field: {field}"


# ─── get_accommodation_standards ─────────────────────────────────────────────

class TestGetAccommodationStandards:
    @pytest.mark.asyncio
    async def test_happy_path_returns_document_urls(self):
        row = {
            "category_code": "BOUTIQUE_VILLA",
            "category_name": "Boutique Villa",
            "category_group": "accommodation",
            "notes": "Min 5 rooms",
            "gazette_url": "https://sltda.gov.lk/gaz.pdf",
            "gazette_name": "Gazette 2021",
            "guidelines_url": "https://sltda.gov.lk/guide.pdf",
            "guidelines_name": "Guidelines",
            "checklist_url": "https://sltda.gov.lk/chk.pdf",
            "checklist_name": "Checklist",
            "registration_url": "https://sltda.gov.lk/reg.pdf",
        }
        conn = _mock_conn(fetchrow=row)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_accommodation_standards("boutique_villa", "full")

        assert result["status"] == "success"
        assert result["data"]["gazette_url"] == "https://sltda.gov.lk/gaz.pdf"
        assert result["data"]["guidelines_url"] == "https://sltda.gov.lk/guide.pdf"
        assert result["data"]["checklist_url"] == "https://sltda.gov.lk/chk.pdf"

    @pytest.mark.asyncio
    async def test_unknown_category_returns_not_found(self):
        conn = _mock_conn(fetchrow=None)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_accommodation_standards("mars_resort")

        assert result["status"] == "not_found"


# ─── get_registration_checklist ──────────────────────────────────────────────

class TestGetRegistrationChecklist:
    @pytest.mark.asyncio
    async def test_mandatory_item_count(self):
        conn = _mock_conn(
            fetchval=1,  # business type exists
            fetch=[
                {"step_number": 1, "step_title": "Apply",
                 "step_description": "desc",
                 "required_documents": ["NIC", "Deed", "Plan"]},
                {"step_number": 2, "step_title": "Inspect",
                 "step_description": "desc",
                 "required_documents": ["Fire cert", "Health cert"]},
            ],
        )

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_checklist("hotel")

        assert result["status"] == "success"
        assert result["data"]["total_items"] == 5
        assert result["data"]["mandatory_items"] == 5

    @pytest.mark.asyncio
    async def test_unknown_type_returns_not_found(self):
        conn = _mock_conn(fetchval=None)

        with patch("sltda_mcp.mcp_server.tools.registration.acquire", return_value=_mock_acquire(conn)):
            result = await get_registration_checklist("unknown_biz")

        assert result["status"] == "not_found"
