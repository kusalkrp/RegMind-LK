"""
FastMCP Server — sltda-mcp.
Registers all 14 tools across 6 clusters.
SSE transport on port 8001.

Observability:
- Per-tool rolling 60-second rate limit warning (≥60 calls → log warning)
- Fire-and-forget invocation logging with latency to tool_invocation_log table
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastmcp import FastMCP

from sltda_mcp.config import get_settings
from sltda_mcp.database import acquire, close_pool, init_pool
from sltda_mcp.mcp_server.auth import ApiKeyMiddleware
from sltda_mcp.mcp_server.rate_limiter import check_rate_limit
from sltda_mcp.logging_config import configure_json_logging
from sltda_mcp.mcp_server.health import health_check
from sltda_mcp.mcp_server.tools.base import log_invocation
from sltda_mcp.mcp_server.tools.financial import (
    get_financial_concessions,
    get_tax_rate,
    get_tdl_information,
)
from sltda_mcp.mcp_server.tools.registration import (
    get_accommodation_standards,
    get_registration_checklist,
    get_registration_requirements,
)
from sltda_mcp.mcp_server.tools.investor import (
    get_investment_process,
    search_sltda_resources,
)
from sltda_mcp.mcp_server.tools.niche import (
    get_niche_categories,
    get_niche_toolkit,
)
from sltda_mcp.mcp_server.tools.statistics import (
    get_annual_report,
    get_latest_arrivals_report,
)
from sltda_mcp.mcp_server.tools.strategy import (
    get_strategic_plan,
    get_tourism_act_provisions,
)
from sltda_mcp.qdrant_client import close_client, init_client, warmup_query

logger = logging.getLogger(__name__)

async def _enforce_rate_limit(caller_id: str, tool_name: str) -> None:
    """Block with 429 if caller exceeds per-minute limit. Warn-only if no caller_id."""
    settings = get_settings()
    allowed, count = await check_rate_limit(
        caller_id, tool_name, settings.rate_limit_per_caller_per_minute
    )
    if not allowed:
        logger.warning(
            "rate_limit_exceeded",
            extra={"caller_id": caller_id, "tool_name": tool_name, "count": count},
        )
        from sltda_mcp.exceptions import AppBaseError
        raise AppBaseError(
            f"Rate limit exceeded: {count} calls to '{tool_name}' in the last 60 seconds. "
            f"Limit is {settings.rate_limit_per_caller_per_minute}/min."
        )


async def _timed_tool(coro, tool_name: str):
    """Run a tool coroutine with the configured timeout."""
    settings = get_settings()
    try:
        return await asyncio.wait_for(coro, timeout=settings.mcp_tool_timeout_seconds)
    except asyncio.TimeoutError:
        logger.error("tool_timeout", extra={"tool_name": tool_name, "timeout": settings.mcp_tool_timeout_seconds})
        raise TimeoutError(
            f"Tool '{tool_name}' exceeded {settings.mcp_tool_timeout_seconds}s timeout."
        )


async def _fire_log(
    tool_name: str,
    input_params: dict,
    result_status: str,
    latency_ms: float,
    source_type: str,
) -> None:
    """Acquire a DB connection and write the invocation log. Never raises."""
    try:
        async with acquire() as conn:
            await log_invocation(
                conn,
                tool_name=tool_name,
                input_params=input_params,
                result_status=result_status,
                latency_ms=latency_ms,
                source_type=source_type,
            )
    except Exception as exc:
        logger.warning("_fire_log failed (non-critical): %s", exc)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    settings = get_settings()
    configure_json_logging(settings.log_level)

    # Attach API key authentication to the FastMCP Starlette app
    try:
        if hasattr(app, 'app'):
            app.app.add_middleware(ApiKeyMiddleware)
        elif hasattr(app, '_app'):
            app._app.add_middleware(ApiKeyMiddleware)
        logger.info("ApiKeyMiddleware attached")
    except Exception as exc:
        logger.warning("Could not attach ApiKeyMiddleware (check FastMCP version): %s", exc)

    logger.info("sltda-mcp starting up")
    await init_pool()
    await init_client()
    await warmup_query(settings.qdrant_collection)
    logger.info("sltda-mcp ready")

    yield

    logger.info("sltda-mcp shutting down")
    await close_pool()
    await close_client()


mcp = FastMCP(
    "sltda-mcp",
    instructions=(
        "MCP server for Sri Lanka Tourism Development Authority (SLTDA) regulatory information. "
        "Covers business registration, financial concessions, tourist statistics, "
        "strategic plans, niche tourism toolkits, and investor guidance."
    ),
    lifespan=_lifespan,
)


# ── Cluster 1 — Registration & Compliance ─────────────────────────────────────

@mcp.tool()
async def registration_requirements(
    business_type: str,
    action: Literal["register", "renew"] = "register",
    language: str = "english",
) -> dict[str, Any]:
    """
    Get the step-by-step process to register or renew an SLTDA tourism business licence.

    Call this when a user asks HOW to register, renew, or what STEPS are required
    for a tourism business (e.g. hotel, guest house, boutique villa, travel agent).
    Do NOT use this for standards or legal classifications — use
    accommodation_standards instead.
    """
    await _enforce_rate_limit("anonymous", "registration_requirements")
    start = time.monotonic()
    result = await _timed_tool(get_registration_requirements(business_type, action, language), "registration_requirements")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "registration_requirements",
        {"business_type": business_type, "action": action, "language": language},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def accommodation_standards(
    category: str,
    detail_level: Literal["summary", "full"] = "summary",
) -> dict[str, Any]:
    """
    Get legal standards, gazette references, and official classifications
    for an SLTDA accommodation category (hotel, boutique villa, homestay, etc.).

    Use this for standards and legal classifications.
    For the step-by-step registration process, use registration_requirements instead.
    """
    await _enforce_rate_limit("anonymous", "accommodation_standards")
    start = time.monotonic()
    result = await _timed_tool(get_accommodation_standards(category, detail_level), "accommodation_standards")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "accommodation_standards",
        {"category": category, "detail_level": detail_level},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def registration_checklist(
    business_type: str,
    checklist_type: Literal["registration", "renewal", "inspection"] = "registration",
) -> dict[str, Any]:
    """
    Get the itemised document checklist (mandatory and optional items) for
    registering, renewing, or preparing for SLTDA inspection of a tourism business.

    Call this when a user asks WHAT DOCUMENTS are needed.
    For the full registration process with fees and steps, use registration_requirements.
    """
    await _enforce_rate_limit("anonymous", "registration_checklist")
    start = time.monotonic()
    result = await _timed_tool(get_registration_checklist(business_type, checklist_type), "registration_checklist")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "registration_checklist",
        {"business_type": business_type, "checklist_type": checklist_type},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


# ── Cluster 2 — Financial & Tax ───────────────────────────────────────────────

@mcp.tool()
async def financial_concessions(
    business_type: str | None = None,
    concession_type: str = "all",
) -> dict[str, Any]:
    """
    Get SLTDA-linked financial concessions: interest rate reductions, moratoriums,
    banking facilities, and levies for licensed tourism businesses.

    Call this for financial benefits, concessionary loans, or banking facilities.
    For tax rates specifically, use tax_rate instead.
    """
    await _enforce_rate_limit("anonymous", "financial_concessions")
    start = time.monotonic()
    result = await _timed_tool(get_financial_concessions(business_type, concession_type), "financial_concessions")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "financial_concessions",
        {"business_type": business_type, "concession_type": concession_type},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def tdl_information(
    query_type: Literal[
        "overview", "rate", "clearance_process", "required_documents", "form_download"
    ] = "overview",
) -> dict[str, Any]:
    """
    Get Tourism Development Levy (TDL) information: rates, clearance process,
    required documents, and downloadable forms.

    Call this for any question about TDL — what it is, the rate, how to get
    clearance, or what documents are needed for clearance.
    """
    await _enforce_rate_limit("anonymous", "tdl_information")
    start = time.monotonic()
    result = await _timed_tool(get_tdl_information(query_type), "tdl_information")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "tdl_information",
        {"query_type": query_type},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def tax_rate(
    business_type: str | None = None,
) -> dict[str, Any]:
    """
    Get applicable tax rates for SLTDA-licensed tourism businesses.

    Call this for tax rates, tax exemptions, or income tax concessions.
    For broader financial concessions (loans, moratoriums), use financial_concessions.
    """
    await _enforce_rate_limit("anonymous", "tax_rate")
    start = time.monotonic()
    result = await _timed_tool(get_tax_rate(business_type), "tax_rate")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "tax_rate",
        {"business_type": business_type},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


# ── Cluster 3 — Statistics & Reports ─────────────────────────────────────────

@mcp.tool()
async def latest_arrivals_report(
    report_type: Literal["monthly", "annual"] = "monthly",
    year: int | None = None,
) -> dict[str, Any]:
    """
    Get the latest SLTDA tourist arrivals report (monthly or annual statistics).

    Call this for tourist arrival numbers, visitor statistics, top source markets,
    or accommodation occupancy data.
    For full SLTDA annual reports (financials + strategy), use annual_report.
    """
    await _enforce_rate_limit("anonymous", "latest_arrivals_report")
    start = time.monotonic()
    result = await _timed_tool(get_latest_arrivals_report(report_type, year), "latest_arrivals_report")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "latest_arrivals_report",
        {"report_type": report_type, "year": year},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def annual_report(
    year: int,
    language: str = "english",
) -> dict[str, Any]:
    """
    Get the SLTDA Annual Report for a specific year.

    Call this for the full SLTDA annual report, yearly performance summary,
    or audited financial statements for a given year.
    For tourist arrival statistics only, use latest_arrivals_report.
    """
    await _enforce_rate_limit("anonymous", "annual_report")
    start = time.monotonic()
    result = await _timed_tool(get_annual_report(year, language), "annual_report")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "annual_report",
        {"year": year, "language": language},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


# ── Cluster 4 — Strategy & Policy ────────────────────────────────────────────

@mcp.tool()
async def strategic_plan(
    query: str,
    section_focus: str | None = None,
) -> dict[str, Any]:
    """
    Get information from SLTDA's strategic plan using semantic search.
    Call this for strategic goals, tourism targets, and policy direction.
    For legal provisions of the Tourism Act, use tourism_act_provisions instead.
    """
    await _enforce_rate_limit("anonymous", "strategic_plan")
    start = time.monotonic()
    result = await _timed_tool(get_strategic_plan(query, section_focus), "strategic_plan")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "strategic_plan",
        {"query": query, "section_focus": section_focus},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "rag"),
    ))
    return result


@mcp.tool()
async def tourism_act_provisions(
    topic: str,
) -> dict[str, Any]:
    """
    Get relevant provisions from the Tourism Act No. 38 of 2005.
    Call this for legal provisions, definitions, offences, and regulatory powers.
    For policy goals, use strategic_plan instead.
    """
    await _enforce_rate_limit("anonymous", "tourism_act_provisions")
    start = time.monotonic()
    result = await _timed_tool(get_tourism_act_provisions(topic), "tourism_act_provisions")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "tourism_act_provisions",
        {"topic": topic},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "rag"),
    ))
    return result


# ── Cluster 5 — Niche Tourism ────────────────────────────────────────────────

@mcp.tool()
async def niche_categories(
    filter: str | None = None,
) -> dict[str, Any]:
    """
    List all SLTDA-recognised niche tourism categories (eco, MICE, adventure, etc.).
    Use this to discover available categories before drilling in with niche_toolkit.
    """
    await _enforce_rate_limit("anonymous", "niche_categories")
    start = time.monotonic()
    result = await _timed_tool(get_niche_categories(filter), "niche_categories")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "niche_categories",
        {"filter": filter},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


@mcp.tool()
async def niche_toolkit(
    category: str,
    detail_level: Literal["summary", "full"] = "summary",
) -> dict[str, Any]:
    """
    Get information about a specific niche tourism toolkit.
    Summary mode: fast DB lookup. Full mode: DB + AI-synthesised document detail.
    Use niche_categories first to find the correct category code.
    """
    await _enforce_rate_limit("anonymous", "niche_toolkit")
    start = time.monotonic()
    result = await _timed_tool(get_niche_toolkit(category, detail_level), "niche_toolkit")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "niche_toolkit",
        {"category": category, "detail_level": detail_level},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "database"),
    ))
    return result


# ── Cluster 6 — Investor & Discovery ─────────────────────────────────────────

@mcp.tool()
async def investment_process(
    project_type: str | None = None,
) -> dict[str, Any]:
    """
    Get the investment process for starting a tourism business in Sri Lanka.
    Call this when an investor asks how to establish or get approval for a tourism project.
    """
    await _enforce_rate_limit("anonymous", "investment_process")
    start = time.monotonic()
    result = await _timed_tool(get_investment_process(project_type), "investment_process")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "investment_process",
        {"project_type": project_type},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "hybrid"),
    ))
    return result


@mcp.tool()
async def search_resources(
    query: str,
    section_filter: str | None = None,
    document_type_filter: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    General-purpose semantic search across all SLTDA documents.
    Use specific tools when intent is clear — they are faster and more accurate.
    Top_k is capped at 7.
    """
    await _enforce_rate_limit("anonymous", "search_resources")
    start = time.monotonic()
    result = await _timed_tool(search_sltda_resources(query, section_filter, document_type_filter, top_k), "search_resources")
    latency_ms = (time.monotonic() - start) * 1000
    asyncio.create_task(_fire_log(
        "search_resources",
        {"query": query, "section_filter": section_filter,
         "document_type_filter": document_type_filter, "top_k": top_k},
        result.get("status", "success"),
        latency_ms,
        result.get("source", {}).get("type", "rag"),
    ))
    return result


# ── Health (non-MCP HTTP endpoint) ────────────────────────────────────────────

@mcp.tool()
async def server_health() -> dict[str, Any]:
    """
    Return the health status of the sltda-mcp server components
    (PostgreSQL, Qdrant, last pipeline refresh time).
    """
    return await health_check()


if __name__ == "__main__":
    mcp.run(
        transport="sse",
        host="0.0.0.0",
        port=8001,
    )
