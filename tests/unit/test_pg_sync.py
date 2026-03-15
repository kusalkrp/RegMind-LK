"""
Unit tests for ingestion/pg_sync.py.
asyncpg connection is mocked — no real DB access.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from sltda_mcp.exceptions import ValidationError
from sltda_mcp.ingestion.pg_sync import (
    sync_document,
    sync_registration_steps,
    sync_financial_concessions,
    sync_business_categories,
    sync_niche_toolkit,
    SUMMARY_CONFIDENCE_THRESHOLD,
    SUMMARY_TOKEN_THRESHOLD,
    SUMMARY_PAGE_THRESHOLD,
)


def make_conn(fetchval_returns: bool = True) -> AsyncMock:
    """Create a mock asyncpg connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_returns)
    return conn


# ─── sync_document ────────────────────────────────────────────────────────────

class TestSyncDocument:
    @pytest.mark.asyncio
    async def test_writes_to_staging_not_production(self):
        """INSERT must target documents_staging, never documents."""
        conn = make_conn()
        doc_data = {
            "id": "doc-001",
            "source_url": "https://sltda.gov.lk/doc.pdf",
        }

        await sync_document(conn, doc_data)

        assert conn.execute.called
        sql = conn.execute.call_args[0][0]
        assert "documents_staging" in sql
        assert "INSERT INTO documents_staging" in sql
        # Must NOT target production table
        assert "INSERT INTO documents " not in sql

    @pytest.mark.asyncio
    async def test_returns_document_id(self):
        conn = make_conn()
        doc_data = {"id": "doc-xyz", "source_url": "https://example.com/x.pdf"}

        result = await sync_document(conn, doc_data)

        assert result == "doc-xyz"

    @pytest.mark.asyncio
    async def test_defaults_applied_for_missing_keys(self):
        """Missing optional fields fall back to sensible defaults."""
        conn = make_conn()
        doc_data = {"id": "doc-min", "source_url": "https://example.com/min.pdf"}

        # Should not raise — defaults fill in missing keys
        await sync_document(conn, doc_data)
        assert conn.execute.called


# ─── sync_registration_steps ─────────────────────────────────────────────────

class TestSyncRegistrationSteps:
    @pytest.mark.asyncio
    async def test_registration_steps_minimum_row_count(self):
        """1 step → ValidationError raised before any DB write."""
        conn = make_conn()
        steps = [
            {"step_number": 1, "step_title": "Apply", "step_description": "Submit application"},
        ]

        with pytest.raises(ValidationError, match="minimum 2 steps"):
            await sync_registration_steps(conn, "doc-001", "HOTEL", "new_registration", steps)

        # No DB write should have happened
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_steps_raises(self):
        conn = make_conn()

        with pytest.raises(ValidationError):
            await sync_registration_steps(conn, "doc-001", "HOTEL", "new_registration", [])

    @pytest.mark.asyncio
    async def test_two_steps_succeeds(self):
        conn = make_conn()
        steps = [
            {"step_number": 1, "step_title": "Apply", "step_description": "Submit form"},
            {"step_number": 2, "step_title": "Pay fee", "step_description": "Pay at counter"},
        ]

        count = await sync_registration_steps(conn, "doc-001", "HOTEL", "new_registration", steps)

        assert count == 2
        # DELETE + 2 INSERTs = 3 execute calls
        assert conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_deletes_before_insert(self):
        """DELETE must precede INSERT to avoid stale rows."""
        conn = make_conn()
        steps = [
            {"step_number": 1, "step_title": "A", "step_description": "desc a"},
            {"step_number": 2, "step_title": "B", "step_description": "desc b"},
        ]

        await sync_registration_steps(conn, "doc-001", "HOTEL", "new_registration", steps)

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "DELETE" in first_call_sql
        assert "registration_steps_staging" in first_call_sql


# ─── sync_financial_concessions ──────────────────────────────────────────────

class TestSyncFinancialConcessions:
    @pytest.mark.asyncio
    async def test_concession_type_normalised(self):
        """Raw type 'interest_rate_concession' maps to 'banking'."""
        conn = make_conn()
        concessions = [
            {
                "concession_name": "Low interest scheme",
                "concession_type": "interest_rate_concession",
                "applicable_business_types": "hotel,guesthouse",
                "rate_or_terms": "6% per annum",
            }
        ]

        await sync_financial_concessions(conn, "doc-001", concessions)

        insert_call = conn.execute.call_args_list[1]  # call 0 is DELETE
        args = insert_call[0]
        # db_type is the 2nd positional arg after SQL
        assert args[2] == "banking"

    @pytest.mark.asyncio
    async def test_unknown_type_defaults_to_banking(self):
        conn = make_conn()
        concessions = [
            {
                "concession_name": "Mystery concession",
                "concession_type": "completely_unknown_type",
                "applicable_business_types": "",
                "rate_or_terms": "some terms",
            }
        ]

        await sync_financial_concessions(conn, "doc-001", concessions)

        insert_call = conn.execute.call_args_list[1]
        assert insert_call[0][2] == "banking"

    @pytest.mark.asyncio
    async def test_empty_concessions_still_deletes(self):
        conn = make_conn()

        count = await sync_financial_concessions(conn, "doc-001", [])

        assert count == 0
        conn.execute.assert_called_once()  # only the DELETE
        assert "DELETE" in conn.execute.call_args[0][0]


# ─── sync_business_categories ────────────────────────────────────────────────

class TestSyncBusinessCategories:
    @pytest.mark.asyncio
    async def test_orphan_fk_logged_not_silently_inserted(self, caplog):
        """FK not found in documents_staging → WARNING logged + field set NULL."""
        conn = make_conn(fetchval_returns=False)  # simulate missing FK
        categories = [
            {
                "category_code": "HOTEL_1",
                "category_name": "Budget Hotel",
                "category_group": "accommodation",
                "gazette_document_id": "missing-doc-id",
            }
        ]

        with caplog.at_level(logging.WARNING, logger="sltda_mcp.ingestion.pg_sync"):
            await sync_business_categories(conn, "doc-001", categories)

        # Warning must be logged
        orphan_warnings = [r for r in caplog.records if "Orphan FK" in r.message]
        assert len(orphan_warnings) >= 1

        # gazette_document_id must be passed as NULL in the INSERT
        insert_call = conn.execute.call_args_list[-1]
        args = insert_call[0]
        # gazette_document_id is the 4th param ($4): category_code, name, group, gazette_id
        assert args[4] is None  # gazette_document_id nulled

    @pytest.mark.asyncio
    async def test_valid_fk_not_nulled(self):
        """FK found in documents_staging → preserved as-is."""
        conn = make_conn(fetchval_returns=True)  # FK exists
        categories = [
            {
                "category_code": "HOTEL_2",
                "category_name": "Luxury Hotel",
                "category_group": "accommodation",
                "gazette_document_id": "real-doc-id",
            }
        ]

        await sync_business_categories(conn, "doc-001", categories)

        insert_call = conn.execute.call_args_list[-1]
        args = insert_call[0]
        assert args[4] == "real-doc-id"  # gazette_document_id preserved

    @pytest.mark.asyncio
    async def test_returns_category_count(self):
        conn = make_conn(fetchval_returns=True)
        categories = [
            {"category_code": "A", "category_name": "A name", "category_group": "g"},
            {"category_code": "B", "category_name": "B name", "category_group": "g"},
            {"category_code": "C", "category_name": "C name", "category_group": "g"},
        ]

        count = await sync_business_categories(conn, "doc-001", categories)

        assert count == 3


# ─── sync_niche_toolkit ───────────────────────────────────────────────────────

class TestSyncNicheToolkit:
    @pytest.mark.asyncio
    async def test_niche_toolkit_summary_gate_below_token_threshold(self):
        """tokens=500 < 800 threshold → _generate_toolkit_summary NOT called."""
        conn = make_conn()
        toolkit_data = {
            "toolkit_code": "ECO_TOUR",
            "toolkit_name": "Eco Tourism Toolkit",
            "target_market": "Adventure travelers",
            "key_activities": ["trekking", "bird watching"],
            "regulatory_notes": "Requires forest permit",
        }

        with patch("sltda_mcp.ingestion.pg_sync._generate_toolkit_summary") as mock_gen:
            await sync_niche_toolkit(
                conn,
                "doc-001",
                toolkit_data,
                full_text="Sample toolkit content text here.",
                confidence=0.90,   # passes confidence gate
                token_count=500,   # BELOW 800 threshold
                page_count=10,     # passes page gate
            )

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_niche_toolkit_summary_gate_below_page_threshold(self):
        """pages=3 ≤ 5 threshold → _generate_toolkit_summary NOT called."""
        conn = make_conn()
        toolkit_data = {"toolkit_code": "X", "toolkit_name": "X Toolkit"}

        with patch("sltda_mcp.ingestion.pg_sync._generate_toolkit_summary") as mock_gen:
            await sync_niche_toolkit(
                conn, "doc-001", toolkit_data,
                full_text="content",
                confidence=0.90,
                token_count=1200,
                page_count=3,      # BELOW page threshold (> 5 required)
            )

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_niche_toolkit_summary_gate_below_confidence_threshold(self):
        """confidence=0.70 < 0.85 threshold → _generate_toolkit_summary NOT called."""
        conn = make_conn()
        toolkit_data = {"toolkit_code": "X", "toolkit_name": "X Toolkit"}

        with patch("sltda_mcp.ingestion.pg_sync._generate_toolkit_summary") as mock_gen:
            await sync_niche_toolkit(
                conn, "doc-001", toolkit_data,
                full_text="content",
                confidence=0.70,   # BELOW 0.85
                token_count=1200,
                page_count=10,
            )

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_niche_toolkit_summary_generated_when_all_gates_pass(self):
        """tokens=1200, pages=10, confidence=0.90 → Gemini called exactly once."""
        conn = make_conn()
        toolkit_data = {
            "toolkit_code": "MICE",
            "toolkit_name": "MICE Tourism Toolkit",
            "target_market": "Business travelers",
            "key_activities": ["conferences", "exhibitions"],
            "regulatory_notes": "SLTDA approval required",
        }

        with patch(
            "sltda_mcp.ingestion.pg_sync._generate_toolkit_summary",
            new_callable=AsyncMock,
            return_value="Professional 150-word summary of the toolkit.",
        ) as mock_gen:
            await sync_niche_toolkit(
                conn,
                "doc-001",
                toolkit_data,
                full_text="Full toolkit content spanning many pages...",
                confidence=0.90,   # passes (≥ 0.85)
                token_count=1200,  # passes (≥ 800)
                page_count=10,     # passes (> 5)
            )

        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_summary_not_overwritten(self):
        """If summary already in toolkit_data, _generate_toolkit_summary NOT called."""
        conn = make_conn()
        toolkit_data = {
            "toolkit_code": "SURF",
            "toolkit_name": "Surf Tourism Toolkit",
            "summary": "Pre-existing human-written summary.",
        }

        with patch("sltda_mcp.ingestion.pg_sync._generate_toolkit_summary") as mock_gen:
            await sync_niche_toolkit(
                conn, "doc-001", toolkit_data,
                full_text="content",
                confidence=0.95,
                token_count=2000,
                page_count=20,
            )

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_to_staging_not_production(self):
        """INSERT must target niche_toolkits_staging."""
        conn = make_conn()
        toolkit_data = {"toolkit_code": "TEST", "toolkit_name": "Test Toolkit"}

        with patch("sltda_mcp.ingestion.pg_sync._generate_toolkit_summary", new_callable=AsyncMock):
            await sync_niche_toolkit(
                conn, "doc-001", toolkit_data,
                full_text="x", confidence=0.5, token_count=100, page_count=1,
            )

        insert_sql = conn.execute.call_args_list[-1][0][0]
        assert "niche_toolkits_staging" in insert_sql
        assert "INSERT INTO niche_toolkits" not in insert_sql.replace("niche_toolkits_staging", "")
