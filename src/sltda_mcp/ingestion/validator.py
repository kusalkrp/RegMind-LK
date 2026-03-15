"""
Pandera validation schemas for extractor structured output.
On failure: log ERROR, add to format_review_queue, do NOT write to staging tables.
"""

import logging

import pandas as pd
import pandera as pa
from pandera import Column, DataFrameSchema

from sltda_mcp.ingestion.extractors.base import ExtractionResult

logger = logging.getLogger(__name__)

StepsSchema = DataFrameSchema(
    {
        "step_number": Column(int, pa.Check.greater_than_or_equal_to(1)),
        "step_title": Column(str, pa.Check(lambda s: s.str.len() > 0, element_wise=False)),
        "step_description": Column(
            str, pa.Check(lambda s: s.str.len() >= 10, element_wise=False)
        ),
    }
)

ChecklistSchema = DataFrameSchema(
    {
        "item_number": Column(int, pa.Check.greater_than_or_equal_to(1)),
        "document_name": Column(
            str, pa.Check(lambda s: s.str.len() > 0, element_wise=False)
        ),
        "is_mandatory": Column(bool),
    }
)

CircularSchema = DataFrameSchema(
    {
        "concession_name": Column(str),
        "concession_type": Column(str),
        "rate_or_terms": Column(
            str, pa.Check(lambda s: s.str.len() > 0, element_wise=False)
        ),
    }
)

ToolkitSchema = DataFrameSchema(
    {
        "toolkit_code": Column(str),
        "toolkit_name": Column(
            str, pa.Check(lambda s: s.str.len() > 0, element_wise=False)
        ),
    }
)

_SCHEMA_MAP: dict[str, DataFrameSchema | None] = {
    "registration_steps": StepsSchema,
    "checklist_form": ChecklistSchema,
    "financial_circular": CircularSchema,
    "niche_toolkit": ToolkitSchema,
}


def validate_extraction(
    result: ExtractionResult,
    format_family: str,
) -> bool:
    """
    Validate structured_data against the appropriate Pandera schema.

    Returns True if valid (or no schema defined for this format).
    Returns False and logs ERROR on validation failure.
    Does NOT raise — callers should check the return value and
    skip DB writes on False.
    """
    schema = _SCHEMA_MAP.get(format_family)
    if schema is None or result.structured_data is None:
        return True

    key = next(iter(result.structured_data), None)
    if key is None:
        return True

    records = result.structured_data.get(key, [])
    if not records:
        logger.error("Validation: empty structured data for %s (family=%s)", key, format_family)
        return False

    try:
        df = pd.DataFrame(records)
        schema.validate(df)
        logger.debug("Validation passed for %s (family=%s)", key, format_family)
        return True
    except pa.errors.SchemaError as exc:
        logger.error(
            "Pandera validation failed for %s (family=%s): %s",
            key,
            format_family,
            exc,
        )
        return False
