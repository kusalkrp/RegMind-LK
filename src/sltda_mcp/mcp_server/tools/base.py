"""
Standard envelope builder and invocation logger for all MCP tools.
Every tool response must go through build_envelope().
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DISCLAIMER = (
    "Based on SLTDA documents as of {last_refresh}. "
    "Verify with official SLTDA sources before acting."
)
_LEGAL_DISCLAIMER = " This is not legal advice. Consult a qualified attorney."
_FINANCIAL_DISCLAIMER = (
    " Tax and levy information may change. Confirm with SLTDA or a tax professional."
)


def build_envelope(
    tool_name: str,
    status: str,
    data: dict | list,
    source_type: str,
    source_documents: list[dict],
    confidence: str | None = None,
    disclaimer: str | None = None,
    last_refresh: str = "latest available",
) -> dict[str, Any]:
    """Build the standard MCP tool response envelope."""
    base = _BASE_DISCLAIMER.format(last_refresh=last_refresh)
    full_disclaimer = (disclaimer or "") + base
    envelope: dict[str, Any] = {
        "status": status,
        "tool": tool_name,
        "data": data,
        "source": {
            "type": source_type,
            "documents": source_documents,
        },
        "disclaimer": full_disclaimer.strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if confidence is not None:
        envelope["confidence"] = confidence
    return envelope


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
    )


async def log_invocation(
    conn,
    tool_name: str,
    input_params: dict,
    status: str,
    latency_ms: float,
) -> None:
    """Write tool invocation to log table. Fire-and-forget — never blocks."""
    try:
        await conn.execute(
            """INSERT INTO tool_invocation_log
               (tool_name, input_params, status, latency_ms, invoked_at)
               VALUES ($1, $2, $3, $4, NOW())""",
            tool_name,
            str(input_params),
            status,
            latency_ms,
        )
    except Exception as exc:
        logger.warning("log_invocation failed (non-critical): %s", exc)
