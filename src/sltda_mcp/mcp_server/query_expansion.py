"""
Query Expansion.
Loads ingestion/config/query_expansion.yaml at startup.
Expands acronyms unconditionally; synonym expansion is optional per call.
Issue #23 mitigation: union results from original + expanded query.
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_YAML_PATH = (
    Path(__file__).parents[3] / "ingestion" / "config" / "query_expansion.yaml"
)


@dataclass
class ExpandedQuery:
    original: str
    expanded_terms: list[str]
    acronyms_replaced: dict[str, str]
    _expanded_original: str = ""  # acronym-replaced version of original

    @property
    def full_query(self) -> str:
        """Acronym-replaced query + all synonym expanded terms for embedding."""
        base = self._expanded_original or self.original
        parts = [base] + self.expanded_terms
        return " ".join(parts)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    with open(_YAML_PATH) as f:
        return yaml.safe_load(f)


def expand_query(query: str) -> ExpandedQuery:
    """
    Expand a raw user query:
    1. Replace known acronyms unconditionally.
    2. Match lowercase query tokens against synonym expansion table.
    Returns ExpandedQuery with all terms for union search.
    """
    config = _load_config()
    acronyms: dict[str, str] = config.get("acronyms", {})
    expansions: dict[str, list[str]] = config.get("expansions", {})

    replaced: dict[str, str] = {}
    expanded_query = query

    # Acronym replacement (case-insensitive word boundary match)
    for acronym, full_form in acronyms.items():
        pattern = re.compile(rf"\b{re.escape(acronym)}\b", re.IGNORECASE)
        if pattern.search(expanded_query):
            expanded_query = pattern.sub(full_form, expanded_query)
            replaced[acronym] = full_form

    # Synonym expansion
    query_lower = query.lower()
    extra_terms: list[str] = []
    for phrase, synonyms in expansions.items():
        if phrase in query_lower:
            extra_terms.extend(synonyms)

    return ExpandedQuery(
        original=query,
        expanded_terms=extra_terms,
        acronyms_replaced=replaced,
        _expanded_original=expanded_query,
    )
