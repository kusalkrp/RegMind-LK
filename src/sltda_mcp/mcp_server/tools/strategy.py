"""
Cluster 4 — Strategy & Policy Tools.

Tools:
  - get_strategic_plan          (RAG over SLTDA strategic plan documents)
  - get_tourism_act_provisions  (RAG over Tourism Act No. 38 of 2005)
"""

import logging
from typing import Any

from sltda_mcp.mcp_server.rag import run_rag
from sltda_mcp.mcp_server.tools.base import (
    build_envelope,
    legal_disclaimer,
    validate_tool_inputs,
)

logger = logging.getLogger(__name__)

_TOOL_PLAN = "get_strategic_plan"
_TOOL_ACT = "get_tourism_act_provisions"

_SECTION_STRATEGIC = "Strategic Plans"
_SECTION_LEGISLATION = "Acts and Regulations"


async def get_strategic_plan(
    query: str,
    section_focus: str | None = None,
) -> dict[str, Any]:
    """
    Get information from SLTDA's strategic plan documents using semantic search.

    Call this for questions about SLTDA's strategic goals, tourism targets,
    development priorities, or policy direction.
    Do NOT use this for legal text, Act provisions, or offences under tourism
    law — use tourism_act_provisions instead.
    """
    p = validate_tool_inputs(
        {"query": query, "section_focus": section_focus},
        required=frozenset({"query"}),
    )

    result = await run_rag(
        query=p["query"],
        section_filter=p["section_focus"] or _SECTION_STRATEGIC,
    )

    source_docs = [
        {
            "document_name": c.document_name,
            "url": c.source_url,
            "score": round(c.score, 3),
            "section": c.section_name,
        }
        for c in result.chunks
    ]

    return build_envelope(
        tool_name=_TOOL_PLAN,
        status="success" if result.chunks else "not_found",
        data={
            "answer": result.answer,
            "synthesis_used": result.synthesis_used,
            "query_expanded": result.query_expanded,
            "source_excerpts": [
                {"text": c.chunk_text, "document": c.document_name, "pages": c.page_numbers}
                for c in result.chunks
            ],
        },
        source_type="rag",
        source_documents=source_docs,
        confidence=result.confidence,
    )


async def get_tourism_act_provisions(
    topic: str,
) -> dict[str, Any]:
    """
    Get relevant provisions from the Tourism Act No. 38 of 2005 (Sri Lanka).

    Call this for legal provisions, sections of the Act, definitions,
    offences, penalties, or regulatory powers under Sri Lanka tourism law.
    Do NOT use this for policy goals and tourism targets — use
    strategic_plan instead.
    """
    p = validate_tool_inputs(
        {"topic": topic},
        required=frozenset({"topic"}),
    )

    result = await run_rag(
        query=p["topic"],
        section_filter=_SECTION_LEGISLATION,
        document_type_filter="legislation",
    )

    sections_cited: list[str] = []
    for chunk in result.chunks:
        if chunk.page_numbers:
            sections_cited.append(f"p.{chunk.page_numbers[0]}")

    source_docs = [
        {
            "document_name": c.document_name,
            "url": c.source_url,
            "score": round(c.score, 3),
        }
        for c in result.chunks
    ]

    return build_envelope(
        tool_name=_TOOL_ACT,
        status="success" if result.chunks else "not_found",
        data={
            "answer": result.answer,
            "sections_cited": sections_cited,
            "synthesis_used": result.synthesis_used,
            "source_excerpts": [
                {"text": c.chunk_text, "document": c.document_name, "pages": c.page_numbers}
                for c in result.chunks
            ],
        },
        source_type="rag",
        source_documents=source_docs,
        confidence=result.confidence,
        disclaimer=legal_disclaimer(),
    )
