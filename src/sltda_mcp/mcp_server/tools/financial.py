"""
Cluster 2 — Financial & Tax Tools.

Tools:
  - get_financial_concessions  (interest rate concessions, moratoriums, levies)
  - get_tdl_information        (Tourism Development Levy — rates, clearance, forms)
  - get_tax_rate               (applicable tax rates by business type)
"""

import logging
from typing import Literal

from sltda_mcp.database import acquire
from sltda_mcp.mcp_server.tools.base import (
    build_envelope,
    financial_disclaimer,
    not_found_envelope,
)

logger = logging.getLogger(__name__)

_TOOL_FC = "get_financial_concessions"
_TOOL_TDL = "get_tdl_information"
_TOOL_TAX = "get_tax_rate"


async def get_financial_concessions(
    business_type: str | None = None,
    concession_type: str = "all",
) -> dict:
    """
    Get SLTDA-linked financial concessions: interest rate reductions, moratoriums,
    banking facilities, and levies available to licensed tourism businesses.

    Call this when a user asks about financial benefits, concessionary loans,
    or banking facilities for tourism businesses.
    For tax rates specifically, use get_tax_rate instead.
    """
    async with acquire() as conn:
        conditions = []
        params: list = []
        idx = 1

        if business_type:
            conditions.append(f"$${idx} = ANY(applicable_to)")
            params.append(business_type.lower())
            idx += 1

        if concession_type != "all":
            conditions.append(f"concession_type = $${idx}")
            params.append(concession_type.lower())
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        # Fix positional params — asyncpg uses $1, $2 not $$1, $$2
        where = where.replace("$$", "$")

        rows = await conn.fetch(
            f"""SELECT fc.concession_name, fc.concession_type, fc.applicable_to,
                       fc.rate_or_terms, fc.conditions, fc.circular_reference,
                       d.source_url AS circular_url
                FROM financial_concessions fc
                LEFT JOIN documents d ON d.id = fc.document_id
                {where}
                ORDER BY fc.concession_type, fc.concession_name""",
            *params,
        )

        if not rows:
            return not_found_envelope(
                _TOOL_FC,
                "No financial concessions found matching the specified criteria.",
            )

        source_docs = [
            {"type": "circular", "url": r["circular_url"]}
            for r in rows if r["circular_url"]
        ]
        # Deduplicate source docs
        seen = set()
        unique_docs = []
        for d in source_docs:
            if d["url"] not in seen:
                seen.add(d["url"])
                unique_docs.append(d)

        return build_envelope(
            tool_name=_TOOL_FC,
            status="success",
            data={
                "total": len(rows),
                "concessions": [dict(r) for r in rows],
            },
            source_type="database",
            source_documents=unique_docs,
            disclaimer=financial_disclaimer(),
        )


async def get_tdl_information(
    query_type: Literal[
        "overview", "rate", "clearance_process", "required_documents", "form_download"
    ] = "overview",
) -> dict:
    """
    Get Tourism Development Levy (TDL) information: rates, clearance procedure,
    required documents, and downloadable forms.

    Call this for any question about TDL — what it is, how much it is,
    how to get clearance, or what documents are needed for clearance.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            """SELECT fc.concession_name, fc.rate_or_terms, fc.conditions,
                      fc.circular_reference, d.source_url AS doc_url,
                      d.document_name
               FROM financial_concessions fc
               LEFT JOIN documents d ON d.id = fc.document_id
               WHERE fc.concession_type = 'levy'
               ORDER BY fc.concession_name""",
        )

        # Also fetch any TDL form documents
        form_docs = await conn.fetch(
            """SELECT document_name, source_url
               FROM documents
               WHERE LOWER(document_name) LIKE '%tdl%'
                  OR LOWER(document_name) LIKE '%tourism development levy%'
               ORDER BY content_as_of DESC NULLS LAST""",
        )

        source_docs = [
            {"type": "circular", "url": r["doc_url"], "name": r["document_name"]}
            for r in rows if r["doc_url"]
        ] + [
            {"type": "form", "url": f["source_url"], "name": f["document_name"]}
            for f in form_docs if f["source_url"]
        ]

        data: dict = {
            "query_type": query_type,
            "levy_records": [dict(r) for r in rows],
            "forms": [dict(f) for f in form_docs],
        }

        return build_envelope(
            tool_name=_TOOL_TDL,
            status="success" if rows or form_docs else "not_found",
            data=data,
            source_type="database",
            source_documents=source_docs,
            disclaimer=financial_disclaimer(),
        )


async def get_tax_rate(
    business_type: str | None = None,
) -> dict:
    """
    Get applicable tax rates for SLTDA-licensed tourism businesses.

    Call this when a user asks about tax rates, tax exemptions, or
    income tax concessions for tourism businesses.
    For broader financial concessions (loans, moratoriums), use get_financial_concessions.
    """
    async with acquire() as conn:
        if business_type:
            rows = await conn.fetch(
                """SELECT fc.concession_name, fc.rate_or_terms, fc.conditions,
                          fc.applicable_to, d.source_url AS doc_url
                   FROM financial_concessions fc
                   LEFT JOIN documents d ON d.id = fc.document_id
                   WHERE fc.concession_type = 'tax'
                     AND $1 = ANY(fc.applicable_to)
                   ORDER BY fc.concession_name""",
                business_type.lower(),
            )
        else:
            rows = await conn.fetch(
                """SELECT fc.concession_name, fc.rate_or_terms, fc.conditions,
                          fc.applicable_to, d.source_url AS doc_url
                   FROM financial_concessions fc
                   LEFT JOIN documents d ON d.id = fc.document_id
                   WHERE fc.concession_type = 'tax'
                   ORDER BY fc.concession_name""",
            )

        if not rows:
            return not_found_envelope(
                _TOOL_TAX,
                f"No tax rate records found"
                + (f" for business type '{business_type}'." if business_type else "."),
            )

        return build_envelope(
            tool_name=_TOOL_TAX,
            status="success",
            data={
                "business_type_filter": business_type,
                "total": len(rows),
                "tax_rates": [dict(r) for r in rows],
            },
            source_type="database",
            source_documents=[
                {"type": "circular", "url": r["doc_url"]}
                for r in rows if r["doc_url"]
            ],
            disclaimer=financial_disclaimer(),
        )
