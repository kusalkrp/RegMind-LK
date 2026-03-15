"""
Cluster 5 — Niche Tourism Tools.

Tools:
  - get_niche_categories  (list all niche tourism categories, optional keyword filter)
  - get_niche_toolkit     (summary mode: PG only; full mode: PG + RAG)
"""

import logging
from typing import Any, Literal

from sltda_mcp.database import acquire
from sltda_mcp.mcp_server.rag import run_rag
from sltda_mcp.mcp_server.tools.base import build_envelope, not_found_envelope

logger = logging.getLogger(__name__)

_TOOL_CATS = "get_niche_categories"
_TOOL_TOOLKIT = "get_niche_toolkit"

_SECTION_NICHE = "Niche Tourism"


async def get_niche_categories(
    filter: str | None = None,
) -> dict[str, Any]:
    """
    List all SLTDA-recognised niche tourism categories (eco tourism, MICE,
    adventure tourism, wellness, etc.).

    Call this when a user wants to discover what niche tourism types SLTDA
    supports, or to browse available categories before drilling into one.
    For detailed toolkit information, use get_niche_toolkit.
    """
    async with acquire() as conn:
        if filter:
            rows = await conn.fetch(
                """SELECT toolkit_code, toolkit_name, target_market, extraction_confidence
                   FROM niche_toolkits
                   WHERE LOWER(toolkit_name) LIKE $1
                      OR LOWER(target_market) LIKE $1
                   ORDER BY toolkit_name""",
                f"%{filter.lower()}%",
            )
        else:
            rows = await conn.fetch(
                """SELECT toolkit_code, toolkit_name, target_market, extraction_confidence
                   FROM niche_toolkits
                   ORDER BY toolkit_name""",
            )

    if not rows:
        return not_found_envelope(
            _TOOL_CATS,
            f"No niche categories found"
            + (f" matching '{filter}'." if filter else "."),
        )

    return build_envelope(
        tool_name=_TOOL_CATS,
        status="success",
        data={
            "total": len(rows),
            "filter": filter,
            "categories": [dict(r) for r in rows],
        },
        source_type="database",
        source_documents=[],
    )


async def get_niche_toolkit(
    category: str,
    detail_level: Literal["summary", "full"] = "summary",
) -> dict[str, Any]:
    """
    Get information about a specific SLTDA niche tourism toolkit.

    Summary mode (default): fast database lookup — regulatory notes,
    target market, key activities, extraction confidence.
    Full mode: adds AI-synthesised detail from the full toolkit document.

    Call this when a user asks how to operate in a specific niche tourism
    segment (eco tourism, MICE, adventure, wellness, surfing, etc.).
    Use get_niche_categories first to discover available category codes.

    Issue #8: extraction_confidence field present in all responses.
    """
    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT nt.toolkit_code, nt.toolkit_name, nt.target_market,
                      nt.key_activities, nt.regulatory_notes, nt.summary,
                      nt.source_text_tokens, nt.source_pages,
                      nt.extraction_confidence, d.source_url
               FROM niche_toolkits nt
               LEFT JOIN documents d ON d.id = nt.document_id
               WHERE nt.toolkit_code = $1""",
            category.upper(),
        )

    if not row:
        return not_found_envelope(
            _TOOL_TOOLKIT,
            f"Niche toolkit '{category}' not found. "
            "Use get_niche_categories to list available codes.",
        )

    source_docs = [{"type": "toolkit", "url": row["source_url"]}] if row["source_url"] else []

    data: dict[str, Any] = {
        "toolkit_code": row["toolkit_code"],
        "toolkit_name": row["toolkit_name"],
        "target_market": row["target_market"],
        "key_activities": row["key_activities"] or [],
        "regulatory_notes": row["regulatory_notes"],
        "summary": row["summary"],
        "extraction_confidence": row["extraction_confidence"],
        "source_pages": row["source_pages"],
        "detail_level": detail_level,
    }

    if detail_level == "full":
        # RAG augmentation over the toolkit document
        rag_query = (
            f"What are the regulatory requirements and operational guidelines "
            f"for {row['toolkit_name']} in Sri Lanka?"
        )
        rag_result = await run_rag(
            query=rag_query,
            section_filter=_SECTION_NICHE,
        )
        data["rag_answer"] = rag_result.answer
        data["rag_confidence"] = rag_result.confidence
        data["rag_excerpts"] = [
            {"text": c.chunk_text, "pages": c.page_numbers}
            for c in rag_result.chunks
        ]
        for s in rag_result.chunks:
            if s.source_url and {"type": "toolkit", "url": s.source_url} not in source_docs:
                source_docs.append({"type": "toolkit", "url": s.source_url})

    return build_envelope(
        tool_name=_TOOL_TOOLKIT,
        status="success",
        data=data,
        source_type="hybrid" if detail_level == "full" else "database",
        source_documents=source_docs,
        confidence=row["extraction_confidence"],
    )
