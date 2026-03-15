"""
Cluster 1 — Registration & Compliance Tools.

Tools:
  - get_registration_requirements  (step-by-step registration/renewal process)
  - get_accommodation_standards    (gazette refs, legal classifications, standards)
  - get_registration_checklist     (itemised mandatory/optional document checklist)
"""

import logging
from typing import Literal

from sltda_mcp.database import acquire
from sltda_mcp.mcp_server.tools.base import (
    build_envelope,
    legal_disclaimer,
    not_found_envelope,
)

logger = logging.getLogger(__name__)

_TOOL_REG_REQ = "get_registration_requirements"
_TOOL_ACC_STD = "get_accommodation_standards"
_TOOL_CHECKLIST = "get_registration_checklist"


async def get_registration_requirements(
    business_type: str,
    action: Literal["register", "renew"] = "register",
    language: str = "english",
) -> dict:
    """
    Get the step-by-step process to register or renew an SLTDA tourism business licence.

    Call this when a user asks HOW to register, renew, or what STEPS are required
    for a tourism business (e.g. hotel, guest house, boutique villa, travel agent).

    Do NOT use this for standards or legal classifications — use
    get_accommodation_standards instead.
    """
    async with acquire() as conn:
        category = await conn.fetchrow(
            """SELECT bc.category_code, bc.category_name, bc.category_group,
                      d.source_url AS gazette_url,
                      d2.source_url AS checklist_url
               FROM business_categories bc
               LEFT JOIN documents d ON d.id = bc.gazette_document_id
               LEFT JOIN documents d2 ON d2.id = bc.checklist_document_id
               WHERE bc.category_code = $1""",
            business_type.upper(),
        )
        if not category:
            return not_found_envelope(
                _TOOL_REG_REQ,
                f"Business type '{business_type}' not found. "
                "Check spelling or use a known SLTDA category code.",
            )

        steps = await conn.fetch(
            """SELECT step_number, step_title, step_description,
                      required_documents, fees
               FROM registration_steps
               WHERE category_code = $1 AND action_type = $2
               ORDER BY step_number""",
            business_type.upper(),
            action,
        )

        source_docs = []
        if category["gazette_url"]:
            source_docs.append({"type": "gazette", "url": category["gazette_url"]})
        if category["checklist_url"]:
            source_docs.append({"type": "checklist", "url": category["checklist_url"]})

        return build_envelope(
            tool_name=_TOOL_REG_REQ,
            status="success",
            data={
                "business_type": category["category_name"],
                "category_code": category["category_code"],
                "action": action,
                "language": language,
                "step_count": len(steps),
                "steps": [dict(s) for s in steps],
            },
            source_type="database",
            source_documents=source_docs,
            disclaimer=legal_disclaimer(),
        )


async def get_accommodation_standards(
    category: str,
    detail_level: Literal["summary", "full"] = "summary",
) -> dict:
    """
    Get legal standards, gazette references, and official classifications
    for an SLTDA accommodation category (e.g. star hotel, boutique villa, homestay).

    Use this for standards and legal classifications.
    For the step-by-step registration process, use get_registration_requirements instead.
    """
    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT bc.category_code, bc.category_name, bc.category_group, bc.notes,
                      d_gaz.source_url   AS gazette_url,   d_gaz.document_name   AS gazette_name,
                      d_gui.source_url   AS guidelines_url, d_gui.document_name  AS guidelines_name,
                      d_chk.source_url   AS checklist_url,  d_chk.document_name  AS checklist_name,
                      d_reg.source_url   AS registration_url
               FROM business_categories bc
               LEFT JOIN documents d_gaz ON d_gaz.id = bc.gazette_document_id
               LEFT JOIN documents d_gui ON d_gui.id = bc.guidelines_document_id
               LEFT JOIN documents d_chk ON d_chk.id = bc.checklist_document_id
               LEFT JOIN documents d_reg ON d_reg.id = bc.registration_document_id
               WHERE bc.category_code = $1""",
            category.upper(),
        )
        if not row:
            return not_found_envelope(
                _TOOL_ACC_STD,
                f"Category '{category}' not found in SLTDA classification records.",
            )

        data: dict = {
            "category_code": row["category_code"],
            "category_name": row["category_name"],
            "category_group": row["category_group"],
            "gazette_url": row["gazette_url"],
            "guidelines_url": row["guidelines_url"],
            "checklist_url": row["checklist_url"],
            "registration_form_url": row["registration_url"],
        }
        if detail_level == "full":
            data["notes"] = row["notes"]

        source_docs = [
            {"type": t, "name": n, "url": u}
            for t, n, u in [
                ("gazette", row["gazette_name"], row["gazette_url"]),
                ("guidelines", row["guidelines_name"], row["guidelines_url"]),
                ("checklist", row["checklist_name"], row["checklist_url"]),
            ]
            if u
        ]

        return build_envelope(
            tool_name=_TOOL_ACC_STD,
            status="success",
            data=data,
            source_type="database",
            source_documents=source_docs,
            disclaimer=legal_disclaimer(),
        )


async def get_registration_checklist(
    business_type: str,
    checklist_type: Literal["registration", "renewal", "inspection"] = "registration",
) -> dict:
    """
    Get the itemised document checklist (mandatory and optional items) for
    registering, renewing, or preparing for SLTDA inspection of a tourism business.

    Call this when a user asks WHAT DOCUMENTS are needed.
    For the full registration process with fees and steps, use get_registration_requirements.
    """
    async with acquire() as conn:
        # Verify business type exists
        exists = await conn.fetchval(
            "SELECT 1 FROM business_categories WHERE category_code = $1",
            business_type.upper(),
        )
        if not exists:
            return not_found_envelope(
                _TOOL_CHECKLIST,
                f"Business type '{business_type}' not found.",
            )

        steps = await conn.fetch(
            """SELECT step_number, step_title, step_description,
                      required_documents
               FROM registration_steps
               WHERE category_code = $1 AND action_type = $2
               ORDER BY step_number""",
            business_type.upper(),
            checklist_type,
        )

        # Flatten required_documents arrays into checklist items
        items = []
        for step in steps:
            docs = step["required_documents"] or []
            for doc in docs:
                items.append({
                    "item": doc,
                    "step": step["step_number"],
                    "step_title": step["step_title"],
                    "is_mandatory": True,  # all required_documents are mandatory
                })

        return build_envelope(
            tool_name=_TOOL_CHECKLIST,
            status="success",
            data={
                "business_type": business_type.upper(),
                "checklist_type": checklist_type,
                "total_items": len(items),
                "mandatory_items": sum(1 for i in items if i["is_mandatory"]),
                "items": items,
            },
            source_type="database",
            source_documents=[],
        )
