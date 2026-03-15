"""
Cluster 6 — Investor & Discovery Tools.

Tools:
  - get_investment_process  (registration steps + RAG for investor unit details)
  - search_sltda_resources  (full-collection semantic search with optional filters)
"""

import logging
from typing import Any

from sltda_mcp.database import acquire
from sltda_mcp.mcp_server.rag import run_rag
from sltda_mcp.mcp_server.tools.base import build_envelope, not_found_envelope

logger = logging.getLogger(__name__)

_TOOL_INVEST = "get_investment_process"
_TOOL_SEARCH = "search_sltda_resources"

_TOP_K_MAX = 7  # Issue #14 hard cap


async def get_investment_process(
    project_type: str | None = None,
) -> dict[str, Any]:
    """
    Get the investment process for starting a tourism business in Sri Lanka:
    registration steps, required forms, SLTDA investor unit contact details.

    Call this when an investor or developer asks how to invest in, establish,
    or get approval for a tourism project in Sri Lanka.
    """
    query = (
        f"investment process for {project_type} tourism project in Sri Lanka"
        if project_type
        else "investment process tourism business registration SLTDA investor unit"
    )

    async with acquire() as conn:
        # Fetch any structured investor steps if they exist
        steps = await conn.fetch(
            """SELECT step_number, step_title, step_description,
                      required_documents, fees
               FROM registration_steps
               WHERE LOWER(action_type) LIKE '%invest%'
                  OR LOWER(category_code) LIKE '%invest%'
               ORDER BY step_number
               LIMIT 20""",
        )

        # Forms related to investment
        form_docs = await conn.fetch(
            """SELECT document_name, source_url
               FROM documents
               WHERE LOWER(document_name) LIKE '%invest%'
                  OR LOWER(document_name) LIKE '%investor%'
               ORDER BY content_as_of DESC NULLS LAST
               LIMIT 5""",
        )

    # RAG for investor unit / BOI details
    rag_result = await run_rag(query=query)

    source_docs = [
        {"type": "form", "name": f["document_name"], "url": f["source_url"]}
        for f in form_docs if f["source_url"]
    ] + [
        {"type": "document", "name": c.document_name, "url": c.source_url}
        for c in rag_result.chunks if c.source_url
    ]

    return build_envelope(
        tool_name=_TOOL_INVEST,
        status="success",
        data={
            "project_type": project_type,
            "structured_steps": [dict(s) for s in steps],
            "investor_guidance": rag_result.answer,
            "rag_confidence": rag_result.confidence,
            "forms": [dict(f) for f in form_docs],
            "source_excerpts": [
                {"text": c.chunk_text, "document": c.document_name}
                for c in rag_result.chunks
            ],
        },
        source_type="hybrid",
        source_documents=source_docs,
        confidence=rag_result.confidence,
    )


async def search_sltda_resources(
    query: str,
    section_filter: str | None = None,
    document_type_filter: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    General-purpose semantic search across all SLTDA documents.

    Call this when a user's question doesn't clearly match a specific tool,
    or when they need to discover what SLTDA documents cover a given topic.
    Use specific tools (registration_requirements, tdl_information, etc.)
    when the intent is clear — they are faster and more accurate.

    top_k is capped at 7 (Issue #14).
    """
    effective_k = min(top_k, _TOP_K_MAX)
    if top_k > _TOP_K_MAX:
        logger.info("search_sltda_resources: top_k capped from %d to %d", top_k, _TOP_K_MAX)

    rag_result = await run_rag(
        query=query,
        section_filter=section_filter,
        document_type_filter=document_type_filter,
        top_k=effective_k,
    )

    # Gemini query intent interpretation (brief, reuses synthesis semaphore)
    query_interpreted_as = query  # default: use original
    if rag_result.synthesis_used:
        query_interpreted_as = (
            f"Query interpreted as: {query}"
            + (f" (expanded)" if rag_result.query_expanded else "")
        )

    return build_envelope(
        tool_name=_TOOL_SEARCH,
        status="success" if rag_result.chunks else "not_found",
        data={
            "query": query,
            "query_interpreted_as": query_interpreted_as,
            "top_k_requested": top_k,
            "top_k_used": effective_k,
            "total_results": len(rag_result.chunks),
            "answer": rag_result.answer,
            "results": [
                {
                    "text": c.chunk_text,     # already truncated to 500 chars
                    "document_name": c.document_name,
                    "source_url": c.source_url,
                    "score": round(c.score, 3),
                    "section": c.section_name,
                }
                for c in rag_result.chunks
            ],
        },
        source_type="rag",
        source_documents=[
            {"name": c.document_name, "url": c.source_url}
            for c in rag_result.chunks if c.source_url
        ],
        confidence=rag_result.confidence,
    )
