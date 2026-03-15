"""
Cluster 3 — Statistics & Reports Tools.

Tools:
  - get_latest_arrivals_report  (monthly/annual tourist arrivals data)
  - get_annual_report           (SLTDA annual report for a given year)
"""

import logging
from typing import Literal

from sltda_mcp.database import acquire
from sltda_mcp.mcp_server.tools.base import (
    assert_literal,
    build_envelope,
    not_found_envelope,
    validate_tool_inputs,
)

logger = logging.getLogger(__name__)

_TOOL_ARRIVALS = "get_latest_arrivals_report"
_TOOL_ANNUAL = "get_annual_report"

_SECTION_ARRIVALS = 9
_SECTION_ANNUAL_REPORTS = 10


async def get_latest_arrivals_report(
    report_type: Literal["monthly", "annual"] = "monthly",
    year: int | None = None,
) -> dict:
    """
    Get the latest SLTDA tourist arrivals report (monthly or annual statistics).

    Call this when a user asks about tourist arrival numbers, visitor statistics,
    top source markets, or accommodation occupancy data.
    Do NOT use this for the full SLTDA annual report with audited financials or
    organisational strategy — use annual_report.
    """
    p = validate_tool_inputs({"report_type": report_type})
    assert_literal(p["report_type"], ("monthly", "annual"), "report_type")

    async with acquire() as conn:
        params: list = [_SECTION_ARRIVALS]
        filters = ["section_id = $1", "is_active = TRUE"]
        idx = 2

        if year:
            filters.append(f"EXTRACT(YEAR FROM content_as_of) = ${idx}")
            params.append(year)
            idx += 1

        if p["report_type"] == "monthly":
            filters.append(f"LOWER(document_name) LIKE ${idx}")
            params.append("%monthly%")
            idx += 1
        elif p["report_type"] == "annual":
            filters.append(
                f"(LOWER(document_name) LIKE ${idx} OR LOWER(document_name) LIKE ${idx + 1})"
            )
            params.extend(["%annual%", "%yearly%"])
            idx += 2

        where = " AND ".join(filters)
        rows = await conn.fetch(
            f"""SELECT document_name, source_url, content_as_of, format_family
                FROM documents
                WHERE {where}
                ORDER BY content_as_of DESC NULLS LAST
                LIMIT 12""",
            *params,
        )

        if not rows:
            return not_found_envelope(
                _TOOL_ARRIVALS,
                f"No {p['report_type']} arrivals report found"
                + (f" for year {year}." if year else "."),
            )

        return build_envelope(
            tool_name=_TOOL_ARRIVALS,
            status="success",
            data={
                "report_type": p["report_type"],
                "year_filter": year,
                "latest": dict(rows[0]),
                "all_available": [dict(r) for r in rows],
            },
            source_type="database",
            source_documents=[
                {"type": "report", "name": r["document_name"], "url": r["source_url"]}
                for r in rows if r["source_url"]
            ],
            confidence="high",
        )


async def get_annual_report(
    year: int,
    language: str = "english",
) -> dict:
    """
    Get the SLTDA Annual Report for a specific year.

    Call this when a user asks for the SLTDA annual report, yearly performance
    summary, or audited financial statements for a given year.
    Do NOT use this for monthly tourist arrivals statistics or source market
    breakdowns — use latest_arrivals_report.
    """
    p = validate_tool_inputs({"language": language})

    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT document_name, source_url, content_as_of,
                      format_family, file_size_kb
               FROM documents
               WHERE section_id = $1
                 AND language = $2
                 AND is_active = TRUE
                 AND (
                     document_name ILIKE $3
                     OR EXTRACT(YEAR FROM content_as_of) = $4
                 )
               ORDER BY content_as_of DESC NULLS LAST
               LIMIT 1""",
            _SECTION_ANNUAL_REPORTS,
            p["language"],
            f"%{year}%",
            year,
        )

        if not row:
            return not_found_envelope(
                _TOOL_ANNUAL,
                f"SLTDA Annual Report for {year} not found "
                f"(language: {p['language']}). "
                "Available years may differ — try an adjacent year.",
            )

        return build_envelope(
            tool_name=_TOOL_ANNUAL,
            status="success",
            data={
                "year": year,
                "language": p["language"],
                "document_name": row["document_name"],
                "download_url": row["source_url"],
                "published": str(row["content_as_of"]) if row["content_as_of"] else None,
                "file_size_kb": row["file_size_kb"],
            },
            source_type="database",
            source_documents=[
                {"type": "annual_report", "name": row["document_name"], "url": row["source_url"]}
            ],
            confidence="high",
        )
