"""
Document Chunker.
Splits ExtractionResult text into chunks using 5 strategies.
Target: 600 tokens / chunk, 100-token overlap where applicable.
Issue #22 mitigation: list_aware strategy never splits a numbered list mid-way.
"""

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from sltda_mcp.ingestion.extractors.base import ExtractionResult

logger = logging.getLogger(__name__)

TARGET_TOKENS = 600
OVERLAP_TOKENS = 100
SHORT_DOC_THRESHOLD = 200  # tokens — store as single chunk regardless of strategy

_LIST_ITEM_RE = re.compile(r"(?:^|\n)\s*\d+[\.\)]\s+", re.MULTILINE)
_HEADING_RE = re.compile(r"(?:^|\n)([A-Z][A-Z\s]{4,79})\n", re.MULTILINE)


@dataclass
class Chunk:
    document_id: str
    chunk_index: int
    chunk_text: str
    chunk_strategy: str
    format_family: str
    token_count: int
    page_numbers: list[int] = field(default_factory=list)
    chunk_type: str = "text"   # "text" | "table"

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "chunk_text": self.chunk_text,
            "chunk_strategy": self.chunk_strategy,
            "format_family": self.format_family,
            "token_count": self.token_count,
            "page_numbers": self.page_numbers,
            "chunk_type": self.chunk_type,
        }


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


def _make_chunk(
    doc_id: UUID,
    index: int,
    text: str,
    strategy: str,
    family: str,
    chunk_type: str = "text",
) -> Chunk:
    return Chunk(
        document_id=str(doc_id),
        chunk_index=index,
        chunk_text=text,
        chunk_strategy=strategy,
        format_family=family,
        token_count=estimate_tokens(text),
        chunk_type=chunk_type,
    )


def _chunk_paragraph_aware(
    text: str, doc_id: UUID, family: str, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    if estimate_tokens(text) <= SHORT_DOC_THRESHOLD:
        return [_make_chunk(doc_id, 0, text, "paragraph_aware", family)]

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    for para in paras:
        pt = estimate_tokens(para)
        if current_tokens + pt > max_tokens and current:
            chunk_text = "\n\n".join(current)
            chunks.append(_make_chunk(doc_id, len(chunks), chunk_text, "paragraph_aware", family))
            # Carry last paragraph as overlap if it fits in the overlap budget
            last = current[-1]
            current = [last, para] if estimate_tokens(last) <= overlap_tokens else [para]
            current_tokens = sum(estimate_tokens(p) for p in current)
        else:
            current.append(para)
            current_tokens += pt

    if current:
        chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current), "paragraph_aware", family))
    return chunks


def _chunk_list_aware(
    text: str, doc_id: UUID, family: str, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    """
    Issue #22 mitigation: detect sequential numbered list blocks and never
    split them. Non-list segments use paragraph_aware splitting.
    """
    if estimate_tokens(text) <= SHORT_DOC_THRESHOLD:
        return [_make_chunk(doc_id, 0, text, "list_aware", family)]

    # Identify list item start positions
    items = list(_LIST_ITEM_RE.finditer(text))
    if not items:
        return _chunk_paragraph_aware(text, doc_id, family, max_tokens, overlap_tokens)

    # Build (segment_text, is_list_block) pairs
    segments: list[tuple[str, bool]] = []
    prev_end = 0
    for i, match in enumerate(items):
        if match.start() > prev_end:
            seg = text[prev_end:match.start()].strip()
            if seg:
                segments.append((seg, False))
        item_end = items[i + 1].start() if i + 1 < len(items) else len(text)
        item_text = text[match.start():item_end].strip()
        # Merge with previous list block if exists
        if segments and segments[-1][1]:
            prev_seg, _ = segments[-1]
            segments[-1] = (prev_seg + "\n" + item_text, True)
        else:
            segments.append((item_text, True))
        prev_end = item_end
    if prev_end < len(text):
        remaining = text[prev_end:].strip()
        if remaining:
            segments.append((remaining, False))

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_tokens = 0

    for seg_text, is_list in segments:
        seg_tokens = estimate_tokens(seg_text)
        if is_list:
            # Keep entire list block together (oversized allowed)
            if current_parts and current_tokens + seg_tokens > max_tokens:
                chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current_parts), "list_aware", family))
                current_parts = []
                current_tokens = 0
            current_parts.append(seg_text)
            current_tokens += seg_tokens
        else:
            if current_parts and current_tokens + seg_tokens > max_tokens:
                chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current_parts), "list_aware", family))
                current_parts = []
                current_tokens = 0
            current_parts.append(seg_text)
            current_tokens += seg_tokens

    if current_parts:
        chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current_parts), "list_aware", family))
    return chunks


def _chunk_heading_aware(
    text: str, doc_id: UUID, family: str, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    """Split at ALL-CAPS heading boundaries; further split oversized sections."""
    if estimate_tokens(text) <= SHORT_DOC_THRESHOLD:
        return [_make_chunk(doc_id, 0, text, "heading_aware", family)]

    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return _chunk_paragraph_aware(text, doc_id, family, max_tokens, overlap_tokens)

    sections: list[str] = []
    # Text before first heading
    if matches[0].start() > 0:
        pre = text[:matches[0].start()].strip()
        if pre:
            sections.append(pre)
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sec = text[match.start():end].strip()
        if sec:
            sections.append(sec)

    chunks: list[Chunk] = []
    for sec in sections:
        if estimate_tokens(sec) > max_tokens:
            sub = _chunk_paragraph_aware(sec, doc_id, family, max_tokens, overlap_tokens)
            # Re-index
            for s in sub:
                s.chunk_index = len(chunks)
                s.chunk_strategy = "heading_aware"
                chunks.append(s)
        else:
            chunks.append(_make_chunk(doc_id, len(chunks), sec, "heading_aware", family))
    return chunks


def _chunk_clause_aware(
    text: str, doc_id: UUID, family: str, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    """Split on numbered clause/section boundaries (e.g. '1.', '2.1.')."""
    if estimate_tokens(text) <= SHORT_DOC_THRESHOLD:
        return [_make_chunk(doc_id, 0, text, "clause_aware", family)]

    clause_re = re.compile(r"(?:^|\n)\s*\d+(?:\.\d+)*\.\s+", re.MULTILINE)
    matches = list(clause_re.finditer(text))
    if not matches:
        return _chunk_paragraph_aware(text, doc_id, family, max_tokens, overlap_tokens)

    sections: list[str] = []
    if matches[0].start() > 0:
        pre = text[:matches[0].start()].strip()
        if pre:
            sections.append(pre)
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[match.start():end].strip())

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0
    for sec in sections:
        st = estimate_tokens(sec)
        if current_tokens + st > max_tokens and current:
            chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current), "clause_aware", family))
            current = [sec]
            current_tokens = st
        else:
            current.append(sec)
            current_tokens += st
    if current:
        chunks.append(_make_chunk(doc_id, len(chunks), "\n\n".join(current), "clause_aware", family))
    return chunks


def _chunk_table_per_chunk(
    result: ExtractionResult, doc_id: UUID, family: str
) -> list[Chunk]:
    """Each table becomes its own chunk; text content also chunked."""
    chunks: list[Chunk] = []
    tables = (result.structured_data or {}).get("tables", [])

    for table in tables:
        import json
        table_text = json.dumps(table, default=str)
        chunks.append(_make_chunk(doc_id, len(chunks), table_text, "table_per_chunk", family, chunk_type="table"))

    if result.text.strip():
        text_chunks = _chunk_paragraph_aware(result.text, doc_id, family, TARGET_TOKENS, OVERLAP_TOKENS)
        for tc in text_chunks:
            tc.chunk_index = len(chunks)
            tc.chunk_strategy = "table_per_chunk"
            chunks.append(tc)
    return chunks


_STRATEGY_MAP = {
    "paragraph_aware": lambda r, doc_id, fam, mx, ov: _chunk_paragraph_aware(r.text, doc_id, fam, mx, ov),
    "list_aware": lambda r, doc_id, fam, mx, ov: _chunk_list_aware(r.text, doc_id, fam, mx, ov),
    "clause_aware": lambda r, doc_id, fam, mx, ov: _chunk_clause_aware(r.text, doc_id, fam, mx, ov),
    "heading_aware": lambda r, doc_id, fam, mx, ov: _chunk_heading_aware(r.text, doc_id, fam, mx, ov),
    "section_aware": lambda r, doc_id, fam, mx, ov: _chunk_clause_aware(r.text, doc_id, fam, mx, ov),
}


def chunk_document(
    result: ExtractionResult,
    format_family: str,
    chunk_strategy: str,
    document_id: UUID,
    max_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """
    Split ExtractionResult into chunks using the specified strategy.
    Short documents (< 200 tokens) are always stored as a single chunk.
    """
    if chunk_strategy == "table_per_chunk":
        chunks = _chunk_table_per_chunk(result, document_id, format_family)
    else:
        fn = _STRATEGY_MAP.get(chunk_strategy, _STRATEGY_MAP["paragraph_aware"])
        chunks = fn(result, document_id, format_family, max_tokens, overlap_tokens)

    logger.info(
        "Chunker: %d chunks from strategy=%s family=%s",
        len(chunks), chunk_strategy, format_family,
    )
    return chunks
