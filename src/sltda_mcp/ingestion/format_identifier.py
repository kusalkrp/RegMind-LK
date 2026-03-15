"""
Dynamic Format Identifier.
2-tier classification: rule-based (Tier 1) + embedding similarity (Tier 2).
Unknown documents are never dropped — they get FallbackExtractor + review queue.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import yaml

logger = logging.getLogger(__name__)

_STRATEGIES_PATH = (
    Path(__file__).parents[3] / "ingestion" / "config" / "format_strategies.yaml"
)

_ANNUAL_REPORT_RE = re.compile(r"annual.?report.?\d{4}", re.IGNORECASE)
_MONTHLY_ARRIVALS_RE = re.compile(r"monthly.?arrival", re.IGNORECASE)
_STRATEGIC_PLAN_RE = re.compile(r"strategic.?plan", re.IGNORECASE)

# Tier 0: document-name keyword → format_family (ordered by specificity)
_NAME_FORMAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"gazette", re.I), "gazette_legal"),
    (re.compile(r"banking.?circular|circular.?no", re.I), "financial_circular"),
    (re.compile(r"toolkit", re.I), "niche_toolkit"),
    (re.compile(r"(registration|renewal).{0,20}(process|step)", re.I), "registration_steps"),
    (re.compile(r"check.?list", re.I), "checklist_form"),
    (re.compile(r"tourism.?act|act.?no\.?\s*\d+", re.I), "legislation"),
    (re.compile(r"strategic.?plan", re.I), "strategic_plan"),
    (re.compile(r"annual.?report", re.I), "annual_report"),
    (re.compile(r"monthly.?arrival|statistical.?report", re.I), "data_table_report"),
    (re.compile(r"concession|moratorium|tax.?rate|financial.?relief", re.I), "financial_circular"),
    (re.compile(r"clearance.?form|application.?form", re.I), "registration_form_blank"),
]


@dataclass
class FeatureFingerprint:
    page_count: int
    table_count: int
    avg_table_col_count: float
    text_density_per_page: float  # avg chars per page (first 3 pages)
    has_numbered_lists: bool
    first_page_title: str
    filename: str
    file_size_kb: int


@dataclass
class StrategyConfig:
    format_family: str
    extractor_class: str
    output_table: str
    chunk_strategy: str
    structured_extraction: bool
    table_extraction: bool
    flag_for_review: bool = False
    alert: bool = False
    min_confidence: float = 0.0


@dataclass
class FormatClassification:
    format_family: str   # family used for strategy selection
    confidence: float
    flag_for_review: bool = False
    tier: int = 1        # 1 = rule-based, 2 = embedding
    nearest_family: str | None = None  # Tier 2 nearest-neighbor family


def load_format_strategies() -> dict:
    with open(_STRATEGIES_PATH) as f:
        return yaml.safe_load(f)


def load_strategy(format_family: str) -> StrategyConfig:
    strategies = load_format_strategies()
    families = strategies["format_strategies"]
    fam = families.get(format_family) or families["unknown"]
    if format_family not in families:
        format_family = "unknown"
    return StrategyConfig(
        format_family=format_family,
        extractor_class=fam["extractor"],
        output_table=fam["output_table"],
        chunk_strategy=fam["chunk_strategy"],
        structured_extraction=fam.get("structured_extraction", False),
        table_extraction=fam.get("table_extraction", False),
        flag_for_review=fam.get("flag_for_review", False),
        alert=fam.get("alert", False),
        min_confidence=fam.get("min_confidence", 0.0),
    )


def extract_features(pdf_path: Path) -> FeatureFingerprint:
    """Extract lightweight structural features without full text parsing (< 500ms)."""
    file_size_kb = int(pdf_path.stat().st_size / 1024)

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        tables: list = []
        for page in pdf.pages[:5]:
            tables.extend(page.extract_tables() or [])
        table_count = len(tables)
        avg_table_col_count = (
            sum(len(t[0]) for t in tables if t) / len(tables) if tables else 0.0
        )

        total_chars = sum(len(p.extract_text() or "") for p in pdf.pages[:3])
        text_density_per_page = total_chars / max(page_count, 1)

        first_page_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
        first_page_title = next(
            (ln.strip() for ln in first_page_text.split("\n") if ln.strip()), ""
        )[:200]

        sample_text = "".join(p.extract_text() or "" for p in pdf.pages[:2])
        has_numbered_lists = bool(
            re.search(r"^\s*\d+[\.\)]\s+\w", sample_text, re.MULTILINE)
        )

    return FeatureFingerprint(
        page_count=page_count,
        table_count=table_count,
        avg_table_col_count=avg_table_col_count,
        text_density_per_page=text_density_per_page,
        has_numbered_lists=has_numbered_lists,
        first_page_title=first_page_title,
        filename=pdf_path.name,
        file_size_kb=file_size_kb,
    )


def classify_tier0(
    document_name: str,
    section_id: int | None,
    sections_config: dict,
) -> FormatClassification | None:
    """
    Tier 0: classify using document link-text keywords and page section.
    Most reliable — derived directly from the SLTDA website structure.
    Returns None only if neither name patterns nor section defaults match.
    """
    # Name-pattern matching (ordered by specificity)
    for pattern, family in _NAME_FORMAT_PATTERNS:
        if pattern.search(document_name):
            return FormatClassification(family, confidence=0.9, tier=1)

    # Section-default fallback
    if section_id is not None:
        sec_cfg = sections_config.get(section_id) or sections_config.get(str(section_id), {})
        default_family = sec_cfg.get("default_format_family")
        if default_family:
            return FormatClassification(default_family, confidence=0.75, tier=1)

    return None


def classify_tier1(features: FeatureFingerprint) -> FormatClassification | None:
    """Rule-based Tier 1 classification. Returns None if no rule matches (~75% of docs)."""
    fn = features.filename.lower()
    title = features.first_page_title.lower()

    if _ANNUAL_REPORT_RE.search(fn):
        return FormatClassification("annual_report", 1.0)
    if _MONTHLY_ARRIVALS_RE.search(fn):
        return FormatClassification("data_table_report", 1.0)
    if _STRATEGIC_PLAN_RE.search(fn):
        return FormatClassification("strategic_plan", 1.0)
    if "gazette" in title:
        return FormatClassification("gazette_legal", 1.0)
    if "act no" in title:
        return FormatClassification("legislation", 1.0)
    if "toolkit" in title:
        return FormatClassification("niche_toolkit", 1.0)
    if "circular" in title:
        return FormatClassification("financial_circular", 1.0)
    if features.page_count == 1 and features.has_numbered_lists:
        return FormatClassification("checklist_form", 1.0)
    if features.table_count > 8 and features.avg_table_col_count > 4:
        return FormatClassification("data_table_report", 1.0)
    if features.page_count <= 3 and features.text_density_per_page < 250:
        return FormatClassification("registration_form_blank", 1.0)
    return None


def classify_from_score(detected_family: str, score: float) -> FormatClassification:
    """Map Tier 2 embedding similarity score to a FormatClassification."""
    if score >= 0.85:
        return FormatClassification(
            format_family=detected_family,
            confidence=score,
            flag_for_review=False,
            tier=2,
            nearest_family=detected_family,
        )
    return FormatClassification(
        format_family="unknown",
        confidence=score,
        flag_for_review=True,
        tier=2,
        nearest_family=detected_family,
    )


async def identify_format(
    features: FeatureFingerprint,
    qdrant_search_fn: (
        Callable[[FeatureFingerprint], Awaitable[tuple[str, float]]] | None
    ) = None,
    section_id: int | None = None,
    document_name: str = "",
) -> tuple[FormatClassification, StrategyConfig]:
    """
    Classify a document's format and return its processing strategy.

    Tier 0 (document name + section): runs first — deterministic, from website structure.
    Tier 1 (rule-based features): filename/PDF structural rules.
    Tier 2 (embedding similarity): Qdrant exemplar search for ambiguous docs.
    Unknown documents always get FallbackExtractor.

    Args:
        features: Pre-extracted FeatureFingerprint.
        qdrant_search_fn: Async callable returning (format_family, cosine_score).
        section_id: Website section ID from the scraper (1–13).
        document_name: Link text from the website (e.g. "Registration Process / Steps").

    Returns:
        (FormatClassification, StrategyConfig)
    """
    # Load section config for Tier 0
    try:
        import yaml as _yaml
        with open(
            Path(__file__).parents[3] / "ingestion" / "config" / "section_map.yaml"
        ) as _f:
            _section_cfg = _yaml.safe_load(_f).get("sections", {})
    except Exception:
        _section_cfg = {}

    # Tier 0: document name + section-based classification
    classification = classify_tier0(document_name, section_id, _section_cfg)

    # Tier 1: rule-based filename/PDF features (overrides Tier 0 for high-confidence rules)
    tier1 = classify_tier1(features)
    if tier1 is not None:
        classification = tier1

    if classification is None:
        if qdrant_search_fn is not None:
            detected_family, score = await qdrant_search_fn(features)
            classification = classify_from_score(detected_family, score)
            logger.info(
                "Tier 2 classification for %s: family=%s score=%.3f → %s (flag=%s)",
                features.filename,
                detected_family,
                score,
                classification.format_family,
                classification.flag_for_review,
            )
        else:
            classification = FormatClassification(
                format_family="unknown", confidence=0.0, flag_for_review=True, tier=2
            )
            logger.warning("No Qdrant/section match — defaulting to unknown: %s", features.filename)

    strategy = load_strategy(classification.format_family)
    logger.info(
        "Format identified: %s → %s (confidence=%.2f, flag=%s)",
        features.filename,
        classification.format_family,
        classification.confidence,
        classification.flag_for_review,
    )
    return classification, strategy
