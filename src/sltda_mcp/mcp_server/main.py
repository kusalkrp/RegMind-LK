"""
FastMCP Server — sltda-mcp.
Registers all 14 tools across 6 clusters.
SSE transport on port 8001.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastmcp import FastMCP

from sltda_mcp.config import get_settings
from sltda_mcp.logging_config import configure_json_logging
from sltda_mcp.database import close_pool, init_pool
from sltda_mcp.mcp_server.health import health_check
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


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    settings = get_settings()
    configure_json_logging(settings.log_level)

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
    return await get_registration_requirements(business_type, action, language)


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
    return await get_accommodation_standards(category, detail_level)


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
    return await get_registration_checklist(business_type, checklist_type)


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
    return await get_financial_concessions(business_type, concession_type)


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
    return await get_tdl_information(query_type)


@mcp.tool()
async def tax_rate(
    business_type: str | None = None,
) -> dict[str, Any]:
    """
    Get applicable tax rates for SLTDA-licensed tourism businesses.

    Call this for tax rates, tax exemptions, or income tax concessions.
    For broader financial concessions (loans, moratoriums), use financial_concessions.
    """
    return await get_tax_rate(business_type)


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
    return await get_latest_arrivals_report(report_type, year)


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
    return await get_annual_report(year, language)


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
    return await get_strategic_plan(query, section_focus)


@mcp.tool()
async def tourism_act_provisions(
    topic: str,
) -> dict[str, Any]:
    """
    Get relevant provisions from the Tourism Act No. 38 of 2005.
    Call this for legal provisions, definitions, offences, and regulatory powers.
    For policy goals, use strategic_plan instead.
    """
    return await get_tourism_act_provisions(topic)


# ── Cluster 5 — Niche Tourism ────────────────────────────────────────────────

@mcp.tool()
async def niche_categories(
    filter: str | None = None,
) -> dict[str, Any]:
    """
    List all SLTDA-recognised niche tourism categories (eco, MICE, adventure, etc.).
    Use this to discover available categories before drilling in with niche_toolkit.
    """
    return await get_niche_categories(filter)


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
    return await get_niche_toolkit(category, detail_level)


# ── Cluster 6 — Investor & Discovery ─────────────────────────────────────────

@mcp.tool()
async def investment_process(
    project_type: str | None = None,
) -> dict[str, Any]:
    """
    Get the investment process for starting a tourism business in Sri Lanka.
    Call this when an investor asks how to establish or get approval for a tourism project.
    """
    return await get_investment_process(project_type)


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
    return await search_sltda_resources(query, section_filter, document_type_filter, top_k)


# ── Health (non-MCP HTTP endpoint) ────────────────────────────────────────────

@mcp.tool()
async def server_health() -> dict[str, Any]:
    """
    Return the health status of the sltda-mcp server components
    (PostgreSQL, Qdrant, last pipeline refresh time).
    """
    return await health_check()


if __name__ == "__main__":
    settings = get_settings()
    mcp.run(
        transport="sse",
        host="0.0.0.0",
        port=8001,
    )
