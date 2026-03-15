"""
Integration tests — multi-tool chains with all external services mocked.

These tests verify that realistic user flows work end-to-end through
multiple tool calls, ensuring the response envelopes compose correctly
and error propagation is handled at the chain level.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult
from sltda_mcp.mcp_server.tools.niche import get_niche_categories, get_niche_toolkit
from sltda_mcp.mcp_server.tools.registration import (
    get_registration_checklist,
    get_registration_requirements,
)
from sltda_mcp.mcp_server.tools.strategy import (
    get_strategic_plan,
    get_tourism_act_provisions,
)


# ─── Shared helpers ────────────────────────────────────────────────────────────

def _make_conn(fetchrow_val=None, fetch_val=None, second_fetch_val=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_val)
    if second_fetch_val is not None:
        conn.fetch = AsyncMock(side_effect=[fetch_val or [], second_fetch_val])
    else:
        conn.fetch = AsyncMock(return_value=fetch_val or [])
    return conn


def _mock_acquire(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_rag_result(answer="Answer.", chunks=None) -> RagResult:
    if chunks is None:
        chunks = [
            RagChunk(
                chunk_text="Sample content.",
                document_id="doc-1",
                document_name="SLTDA Document",
                source_url="https://sltda.gov.lk/doc.pdf",
                chunk_index=0,
                score=0.88,
            )
        ]
    return RagResult(
        answer=answer,
        confidence="high",
        chunks=chunks,
        synthesis_used=True,
        query_expanded=False,
    )


# ─── Chain 1: Discover niche categories → get full toolkit ────────────────────

class TestNicheDiscoveryChain:
    """
    Simulates: user asks "what niche tourism categories exist?" →
    then asks for full details on eco tourism.
    """

    @pytest.mark.asyncio
    async def test_categories_then_toolkit_full_chain(self):
        # Step 1: list categories
        cat_rows = [
            {
                "toolkit_code": "ECO_TOURISM",
                "toolkit_name": "Eco Tourism Toolkit",
                "target_market": "Nature enthusiasts",
                "extraction_confidence": "high",
            }
        ]
        with patch(
            "sltda_mcp.mcp_server.tools.niche.acquire",
            return_value=_mock_acquire(_make_conn(fetch_val=cat_rows)),
        ):
            categories_result = await get_niche_categories()

        assert categories_result["status"] == "success"
        codes = [c["toolkit_code"] for c in categories_result["data"]["categories"]]
        assert "ECO_TOURISM" in codes

        # Step 2: get full toolkit for a code from step 1
        toolkit_row = {
            "toolkit_code": "ECO_TOURISM",
            "toolkit_name": "Eco Tourism Toolkit",
            "target_market": "Nature enthusiasts",
            "key_activities": ["bird watching"],
            "regulatory_notes": "Forest permit required.",
            "summary": "Eco tourism summary.",
            "source_text_tokens": 900,
            "source_pages": 8,
            "extraction_confidence": "high",
            "source_url": "https://sltda.gov.lk/eco.pdf",
        }
        with (
            patch(
                "sltda_mcp.mcp_server.tools.niche.acquire",
                return_value=_mock_acquire(_make_conn(fetchrow_val=toolkit_row)),
            ),
            patch(
                "sltda_mcp.mcp_server.tools.niche.run_rag",
                new_callable=AsyncMock,
                return_value=_make_rag_result("Eco tourism detail."),
            ),
        ):
            toolkit_result = await get_niche_toolkit("ECO_TOURISM", detail_level="full")

        assert toolkit_result["status"] == "success"
        assert toolkit_result["data"]["toolkit_code"] == "ECO_TOURISM"
        assert "rag_answer" in toolkit_result["data"]

    @pytest.mark.asyncio
    async def test_invalid_category_propagates_not_found(self):
        """If niche_categories returns a code that toolkit can't find, chain breaks gracefully."""
        with patch(
            "sltda_mcp.mcp_server.tools.niche.acquire",
            return_value=_mock_acquire(_make_conn(fetchrow_val=None)),
        ):
            result = await get_niche_toolkit("UNKNOWN_CODE")

        assert result["status"] == "not_found"


# ─── Chain 2: Registration requirements → checklist ───────────────────────────

class TestRegistrationComplianceChain:
    """
    Simulates: user asks how to register a guesthouse →
    then asks what documents are needed for that business type.
    """

    _CATEGORY = {
        "category_code": "GUEST_HOUSE",
        "category_name": "Guest House",
        "category_group": "Accommodation",
        "gazette_url": "https://sltda.gov.lk/gazette.pdf",
        "checklist_url": "https://sltda.gov.lk/checklist.pdf",
    }

    _STEPS = [
        {
            "step_number": 1,
            "step_title": "Online Application",
            "step_description": "Submit via SLTDA portal.",
            "required_documents": ["NIC"],
            "fees": "LKR 2500",
            "online_link": "https://sltda.gov.lk/apply",
        }
    ]

    _CHECKLIST_ITEMS = [
        {
            "step_number": 1,
            "step_title": "Submit Application",
            "step_description": "Submit form.",
            "required_documents": ["NIC", "Business plan"],
        }
    ]

    @pytest.mark.asyncio
    async def test_registration_then_checklist(self):
        # Step 1: get registration requirements
        conn1 = _make_conn(fetchrow_val=self._CATEGORY, fetch_val=self._STEPS)
        with patch(
            "sltda_mcp.mcp_server.tools.registration.acquire",
            return_value=_mock_acquire(conn1),
        ):
            reg_result = await get_registration_requirements("GUEST_HOUSE", action="register")

        assert reg_result["status"] == "success"
        # business_type in data is category_name, not the code
        assert reg_result["data"]["category_code"] == "GUEST_HOUSE"

        # Step 2: get checklist for same business type
        # get_registration_checklist: fetchval (existence) → fetch (steps)
        conn2 = AsyncMock()
        conn2.fetchval = AsyncMock(return_value=1)
        conn2.fetch = AsyncMock(return_value=self._CHECKLIST_ITEMS)
        with patch(
            "sltda_mcp.mcp_server.tools.registration.acquire",
            return_value=_mock_acquire(conn2),
        ):
            checklist_result = await get_registration_checklist("GUEST_HOUSE", "registration")

        assert checklist_result["status"] == "success"
        assert "items" in checklist_result["data"]

    @pytest.mark.asyncio
    async def test_both_tools_share_same_envelope_shape(self):
        """Both registration tools must return standard envelope fields."""
        conn1 = _make_conn(fetchrow_val=self._CATEGORY, fetch_val=self._STEPS)
        conn2 = AsyncMock()
        conn2.fetchval = AsyncMock(return_value=1)
        conn2.fetch = AsyncMock(return_value=self._CHECKLIST_ITEMS)

        with patch(
            "sltda_mcp.mcp_server.tools.registration.acquire",
            return_value=_mock_acquire(conn1),
        ):
            r1 = await get_registration_requirements("GUEST_HOUSE")

        with patch(
            "sltda_mcp.mcp_server.tools.registration.acquire",
            return_value=_mock_acquire(conn2),
        ):
            r2 = await get_registration_checklist("GUEST_HOUSE", "registration")

        required = {"status", "tool", "data", "source", "disclaimer", "generated_at"}
        assert required.issubset(r1.keys())
        assert required.issubset(r2.keys())


# ─── Chain 3: Strategic plan → Tourism Act provisions ─────────────────────────

class TestStrategyLegalChain:
    """
    Simulates: AI assistant first queries strategic plan for tourism targets,
    then drills into Tourism Act for relevant legal provisions.
    """

    @pytest.mark.asyncio
    async def test_strategy_then_legal_provisions(self):
        strategy_rag = _make_rag_result(
            answer="Tourism target: 4 million arrivals by 2025.",
            chunks=[
                RagChunk(
                    chunk_text="4 million arrivals targeted.",
                    document_id="doc-sp",
                    document_name="Strategic Plan 2022-2025",
                    source_url="https://sltda.gov.lk/sp.pdf",
                    chunk_index=0,
                    score=0.91,
                    section_name="Strategic Plans",
                    page_numbers=[5],
                )
            ],
        )
        act_rag = _make_rag_result(
            answer="Section 12 covers licensing requirements.",
            chunks=[
                RagChunk(
                    chunk_text="Section 12 — Licensing.",
                    document_id="doc-act",
                    document_name="Tourism Act No. 38",
                    source_url="https://sltda.gov.lk/act.pdf",
                    chunk_index=0,
                    score=0.89,
                    section_name="Legislation",
                    page_numbers=[12],
                )
            ],
        )

        with patch(
            "sltda_mcp.mcp_server.tools.strategy.run_rag",
            new_callable=AsyncMock,
            return_value=strategy_rag,
        ):
            strat_result = await get_strategic_plan("tourist arrivals targets 2025")

        assert strat_result["status"] == "success"
        assert "4 million" in strat_result["data"]["answer"]

        with patch(
            "sltda_mcp.mcp_server.tools.strategy.run_rag",
            new_callable=AsyncMock,
            return_value=act_rag,
        ):
            act_result = await get_tourism_act_provisions("licensing requirements")

        assert act_result["status"] == "success"
        assert "legal advice" in act_result["disclaimer"].lower() or \
               "attorney" in act_result["disclaimer"].lower()

    @pytest.mark.asyncio
    async def test_strategy_not_found_does_not_block_act_query(self):
        """not_found on strategic plan must not affect a subsequent Tourism Act query."""
        empty_rag = _make_rag_result(chunks=[])
        act_rag = _make_rag_result(answer="Section 24 penalties.")

        with patch(
            "sltda_mcp.mcp_server.tools.strategy.run_rag",
            new_callable=AsyncMock,
            return_value=empty_rag,
        ):
            strat_result = await get_strategic_plan("nonexistent topic xyz")

        assert strat_result["status"] == "not_found"

        with patch(
            "sltda_mcp.mcp_server.tools.strategy.run_rag",
            new_callable=AsyncMock,
            return_value=act_rag,
        ):
            act_result = await get_tourism_act_provisions("penalties")

        assert act_result["status"] == "success"
