"""
Standard envelope builder, input validation, and invocation logger for all MCP tools.
Every tool response must go through build_envelope().
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sltda_mcp.logging_config import log_cost_estimate

logger = logging.getLogger(__name__)

_BASE_DISCLAIMER = (
    "Based on SLTDA documents as of {last_refresh}. "
    "Verify with official SLTDA sources before acting."
)
_LEGAL_DISCLAIMER = " This is not legal advice. Consult a qualified attorney."
_FINANCIAL_DISCLAIMER = (
    " Tax and levy information may change. Confirm with SLTDA or a tax professional."
)

# Parameters treated as free-text queries — max 500 chars
_QUERY_PARAMS = frozenset({"query", "topic", "section_focus", "project_type"})
# Parameters that are codes/identifiers/enums — max 100 chars
_CODE_PARAMS = frozenset({
    "category", "business_type", "category_code", "checklist_type",
    "action", "detail_level", "report_type", "query_type",
    "concession_type", "language", "section_filter", "document_type_filter",
    "filter",
})


def validate_tool_inputs(
    params: dict[str, Any],
    required: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """
    Sanitize and validate tool input parameters.

    - Strips whitespace from string values.
    - Enforces length limits: query-type params ≤ 500 chars, code-type ≤ 100 chars.
    - Raises ValueError for missing required params or oversized strings.

    Returns the sanitized copy of params.
    """
    sanitized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            if key in required:
                raise ValueError(f"Required parameter '{key}' is missing.")
            sanitized[key] = value
            continue

        if isinstance(value, str):
            value = value.strip()
            if key in _QUERY_PARAMS and len(value) > 500:
                raise ValueError(
                    f"Parameter '{key}' exceeds 500-character limit "
                    f"(got {len(value)} chars)."
                )
            if key in _CODE_PARAMS and len(value) > 100:
                raise ValueError(
                    f"Parameter '{key}' exceeds 100-character limit "
                    f"(got {len(value)} chars)."
                )
        sanitized[key] = value
    return sanitized


def assert_literal(value: str, allowed: tuple[str, ...], param_name: str) -> None:
    """Raise ValueError if value is not one of the allowed Literal values."""
    if value not in allowed:
        raise ValueError(
            f"Invalid value '{value}' for parameter '{param_name}'. "
            f"Must be one of: {allowed}"
        )


def build_envelope(
    tool_name: str,
    status: str,
    data: dict | list,
    source_type: str,
    source_documents: list[dict],
    confidence: str = "high",
    disclaimer: str | None = None,
    last_refresh: str = "latest available",
) -> dict[str, Any]:
    """Build the standard MCP tool response envelope."""
    base = _BASE_DISCLAIMER.format(last_refresh=last_refresh)
    full_disclaimer = (disclaimer or "") + base
    return {
        "status": status,
        "tool": tool_name,
        "data": data,
        "source": {
            "type": source_type,
            "documents": source_documents,
        },
        "disclaimer": full_disclaimer.strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "confidence": confidence,
    }


def legal_disclaimer() -> str:
    return _LEGAL_DISCLAIMER


def financial_disclaimer() -> str:
    return _FINANCIAL_DISCLAIMER


def not_found_envelope(tool_name: str, message: str) -> dict[str, Any]:
    return build_envelope(
        tool_name=tool_name,
        status="not_found",
        data={"message": message},
        source_type="database",
        source_documents=[],
        confidence="low",
    )


async def log_invocation(
    conn,
    tool_name: str,
    input_params: dict,
    result_status: str,
    latency_ms: float,
    source_type: str = "database",
) -> None:
    """Write tool invocation to log table. Fire-and-forget — never blocks."""
    try:
        await conn.execute(
            """INSERT INTO tool_invocation_log
               (tool_name, input_params, result_status, response_time_ms, called_at)
               VALUES ($1, $2::jsonb, $3, $4, NOW())""",
            tool_name,
            str(input_params),
            result_status,
            int(latency_ms),
        )
        # Log cost estimate as structured record (non-blocking)
        embedding_calls = 1 if source_type == "rag" else 0
        synthesis_calls = 1 if source_type == "rag" else 0
        log_cost_estimate(logger, tool_name, embedding_calls, synthesis_calls)
    except Exception as exc:
        logger.warning("log_invocation failed (non-critical): %s", exc)
