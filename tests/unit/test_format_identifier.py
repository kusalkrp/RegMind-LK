"""
Unit tests for ingestion/format_identifier.py.
All PDF operations and Qdrant calls are mocked.
"""

import pytest
from unittest.mock import AsyncMock

from sltda_mcp.ingestion.format_identifier import (
    FeatureFingerprint,
    classify_tier1,
    classify_from_score,
    identify_format,
    load_format_strategies,
    load_strategy,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_features(**kwargs) -> FeatureFingerprint:
    defaults = dict(
        page_count=5,
        table_count=0,
        avg_table_col_count=0.0,
        text_density_per_page=500.0,
        has_numbered_lists=False,
        first_page_title="",
        filename="document.pdf",
        file_size_kb=100,
    )
    defaults.update(kwargs)
    return FeatureFingerprint(**defaults)


# ─── Tier 1 classification ────────────────────────────────────────────────────

class TestTier1Classification:
    def test_tier1_annual_report_rule(self):
        result = classify_tier1(make_features(filename="annual_report_2023.pdf"))
        assert result is not None
        assert result.format_family == "annual_report"
        assert result.confidence == 1.0

    def test_tier1_gazette_rule(self):
        result = classify_tier1(
            make_features(first_page_title="Gazette Extraordinary No. 2345/12")
        )
        assert result is not None
        assert result.format_family == "gazette_legal"

    def test_tier1_checklist_rule(self):
        result = classify_tier1(make_features(page_count=1, has_numbered_lists=True))
        assert result is not None
        assert result.format_family == "checklist_form"

    def test_tier1_no_match_returns_none(self):
        result = classify_tier1(
            make_features(filename="unknown_doc.pdf", first_page_title="Some Report")
        )
        assert result is None

    def test_tier1_strategic_plan_rule(self):
        result = classify_tier1(make_features(filename="sltda_strategic_plan_2025.pdf"))
        assert result is not None
        assert result.format_family == "strategic_plan"

    def test_tier1_legislation_rule(self):
        result = classify_tier1(make_features(first_page_title="Tourism Act No. 38 of 2005"))
        assert result is not None
        assert result.format_family == "legislation"

    def test_tier1_toolkit_rule(self):
        result = classify_tier1(make_features(first_page_title="Wellness Tourism Toolkit"))
        assert result is not None
        assert result.format_family == "niche_toolkit"

    def test_tier1_financial_circular_rule(self):
        result = classify_tier1(make_features(first_page_title="Banking Circular No. 07/2019"))
        assert result is not None
        assert result.format_family == "financial_circular"

    def test_tier1_data_table_by_filename(self):
        result = classify_tier1(make_features(filename="monthly_arrivals_jan2024.pdf"))
        assert result is not None
        assert result.format_family == "data_table_report"


# ─── Tier 2 classification ────────────────────────────────────────────────────

class TestTier2Classification:
    @pytest.mark.asyncio
    async def test_tier2_embedding_high_confidence(self):
        features = make_features(filename="unknown_doc.pdf")
        mock_search = AsyncMock(return_value=("financial_circular", 0.91))

        classification, strategy = await identify_format(features, qdrant_search_fn=mock_search)

        assert classification.format_family == "financial_circular"
        assert classification.confidence == pytest.approx(0.91)
        assert classification.flag_for_review is False
        assert classification.tier == 2

    @pytest.mark.asyncio
    async def test_tier2_embedding_medium_confidence(self):
        features = make_features(filename="uncertain_doc.pdf")
        mock_search = AsyncMock(return_value=("financial_circular", 0.75))

        classification, strategy = await identify_format(features, qdrant_search_fn=mock_search)

        assert classification.format_family == "unknown"
        assert classification.flag_for_review is True
        assert strategy.extractor_class == "FallbackExtractor"

    @pytest.mark.asyncio
    async def test_unknown_document_never_dropped(self):
        """Score 0.40 → FallbackExtractor runs, document added to review queue."""
        features = make_features(filename="mystery_doc.pdf")
        mock_search = AsyncMock(return_value=("financial_circular", 0.40))

        classification, strategy = await identify_format(features, qdrant_search_fn=mock_search)

        assert classification.format_family == "unknown"
        assert classification.flag_for_review is True
        assert strategy.extractor_class == "FallbackExtractor"
        assert strategy.flag_for_review is True

    @pytest.mark.asyncio
    async def test_tier1_takes_priority_over_tier2(self):
        """Tier 1 match → Tier 2 search function is never called."""
        features = make_features(filename="annual_report_2024.pdf")
        mock_search = AsyncMock()

        classification, _ = await identify_format(features, qdrant_search_fn=mock_search)

        mock_search.assert_not_called()
        assert classification.format_family == "annual_report"

    @pytest.mark.asyncio
    async def test_no_qdrant_fn_defaults_to_unknown(self):
        """When qdrant_search_fn is None, unclassified docs → unknown."""
        features = make_features(filename="unclassified.pdf")

        classification, strategy = await identify_format(features, qdrant_search_fn=None)

        assert classification.format_family == "unknown"
        assert classification.flag_for_review is True


# ─── Strategy loading ─────────────────────────────────────────────────────────

class TestFormatStrategies:
    def test_format_strategies_yaml_loads_all_families(self):
        strategies = load_format_strategies()
        families = set(strategies["format_strategies"].keys())
        expected = {
            "checklist_form", "registration_steps", "gazette_legal",
            "legislation", "niche_toolkit", "data_table_report",
            "annual_report", "financial_circular", "strategic_plan",
            "registration_form_blank", "guidelines_narrative", "unknown",
        }
        assert expected.issubset(families)

    def test_each_family_has_required_fields(self):
        strategies = load_format_strategies()
        required = {"extractor", "output_table", "chunk_strategy"}
        for family, config in strategies["format_strategies"].items():
            missing = required - set(config.keys())
            assert not missing, f"Family '{family}' missing fields: {missing}"

    def test_load_strategy_unknown_family_returns_fallback(self):
        strategy = load_strategy("nonexistent_family_xyz")
        assert strategy.extractor_class == "FallbackExtractor"

    def test_load_strategy_annual_report(self):
        strategy = load_strategy("annual_report")
        assert strategy.extractor_class == "AnnualReportExtractor"
        assert strategy.table_extraction is True

    def test_load_strategy_unknown_has_flag_for_review(self):
        strategy = load_strategy("unknown")
        assert strategy.flag_for_review is True
        assert strategy.extractor_class == "FallbackExtractor"
