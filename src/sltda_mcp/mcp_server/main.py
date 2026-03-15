"""
FastMCP Server — sltda-mcp.
Registers all 14 tools across 6 clusters.
SSE transport on port 8001.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastmcp import FastMCP

from sltda_mcp.config import configure_logging, get_settings
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
from sltda_mcp.mcp_server.tools.statistics import (
    get_annual_report,
    get_latest_arrivals_report,
)
from sltda_mcp.qdrant_client import close_client, init_client, warmup_query

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    settings = get_settings()
    configure_logging(settings.log_level)

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
