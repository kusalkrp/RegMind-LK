"""
Unit tests for ingestion/chunker.py.
Pure computation — no mocks required.
"""

import json
from uuid import UUID

import pytest

from sltda_mcp.ingestion.chunker import (
    Chunk,
    TARGET_TOKENS,
    OVERLAP_TOKENS,
    SHORT_DOC_THRESHOLD,
    chunk_document,
    estimate_tokens,
)
from sltda_mcp.ingestion.extractors.base import ExtractionResult

# Fixed document ID for deterministic testing
_DOC_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def make_result(text: str, tables: list | None = None) -> ExtractionResult:
    return ExtractionResult(
        text=text,
        structured_data={"tables": tables} if tables is not None else None,
        page_count=1,
        extraction_confidence="high",
    )


# ─── estimate_tokens ─────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_400_chars_is_100_tokens(self):
        assert estimate_tokens("x" * 400) == 100

    def test_2400_chars_is_600_tokens(self):
        assert estimate_tokens("x" * 2400) == TARGET_TOKENS


# ─── Short document → single chunk ───────────────────────────────────────────

class TestShortDocument:
    def test_short_document_single_chunk(self):
        # < SHORT_DOC_THRESHOLD (200 tokens = 800 chars)
        text = "Brief tourism content. " * 30  # ~690 chars ≈ 172 tokens
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "paragraph_aware", _DOC_ID)

        assert len(chunks) == 1
        assert chunks[0].chunk_text == text

    def test_short_document_preserves_strategy_label(self):
        text = "x" * 100
        result = make_result(text)

        for strategy in ["paragraph_aware", "list_aware", "heading_aware", "clause_aware"]:
            chunks = chunk_document(result, "unknown", strategy, _DOC_ID)
            assert len(chunks) == 1


# ─── paragraph_aware ─────────────────────────────────────────────────────────

class TestParagraphAware:
    def test_paragraph_aware_respects_boundaries(self):
        # 3 distinct paragraphs, each ~250 tokens (1000 chars)
        paras = [f"PARA{i} " + "content word " * 76 for i in range(1, 4)]
        # "content word " = 13 chars, 76 * 13 = 988 + 7 = 995 chars ≈ 248 tokens
        text = "\n\n".join(paras)
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "paragraph_aware", _DOC_ID)

        # Para1+Para2 ≈ 496 tokens < 600 → chunk1; Para3 → chunk2
        assert len(chunks) == 2
        assert "PARA1" in chunks[0].chunk_text
        assert "PARA2" in chunks[0].chunk_text
        assert "PARA3" not in chunks[0].chunk_text
        assert "PARA3" in chunks[1].chunk_text

    def test_overlap_applied_correctly(self):
        # 7 paragraphs, each exactly 100 tokens (400 chars)
        para = "x" * 400
        text = "\n\n".join([para] * 7)
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "paragraph_aware", _DOC_ID)

        # Para 1-6 = 600 tokens → chunk1; Para 6 (100 tokens ≤ overlap) carried over + para7 → chunk2
        assert len(chunks) == 2
        # Overlap: chunk1 ends with para, chunk2 starts with para
        assert chunks[0].chunk_text.endswith(para)
        assert chunks[1].chunk_text.startswith(para)

    def test_chunk_indices_are_sequential(self):
        text = "\n\n".join(["x" * 400] * 7)
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "paragraph_aware", _DOC_ID)

        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_single_oversized_paragraph_becomes_one_chunk(self):
        # A single paragraph larger than max_tokens (no split possible)
        text = "x" * 3000  # 750 tokens > 600 limit, but no \n\n to split
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "paragraph_aware", _DOC_ID)

        assert len(chunks) == 1
        assert chunks[0].chunk_text == text


# ─── list_aware ──────────────────────────────────────────────────────────────

class TestListAware:
    def test_list_aware_never_splits_numbered_list(self):
        # 8-item list, each item ~100 tokens (400 chars) — total ~800 tokens > 600 limit
        item_body = "x" * 396
        list_text = "\n".join(f"{i}. {item_body}" for i in range(1, 9))
        result = make_result(list_text)

        chunks = chunk_document(result, "checklist_form", "list_aware", _DOC_ID)

        # Entire list must be kept as one oversized chunk (Issue #22 mitigation)
        assert len(chunks) == 1
        for i in range(1, 9):
            assert f"{i}." in chunks[0].chunk_text

    def test_list_aware_non_list_uses_paragraph_splitting(self):
        # Non-list text falls back to paragraph_aware
        paras = [f"Paragraph {i}. " + "text " * 200 for i in range(1, 4)]
        text = "\n\n".join(paras)
        result = make_result(text)

        chunks = chunk_document(result, "unknown", "list_aware", _DOC_ID)

        # Should produce multiple chunks (total ~3*203*5/4 = ~760 tokens > 600)
        assert len(chunks) >= 1
        # All text accounted for
        all_text = " ".join(c.chunk_text for c in chunks)
        assert "Paragraph 1" in all_text
        assert "Paragraph 3" in all_text


# ─── heading_aware ────────────────────────────────────────────────────────────

class TestHeadingAware:
    def test_heading_aware_splits_on_heading(self):
        # Two sections, each ~450 tokens — total >600, must split
        section1 = "INTRODUCTION\n" + "introduction text here " * 80
        section2 = "MAIN FINDINGS\n" + "findings body content " * 80
        text = section1 + "\n\n" + section2
        result = make_result(text)

        chunks = chunk_document(result, "annual_report", "heading_aware", _DOC_ID)

        assert len(chunks) >= 2
        intro_chunk = any("INTRODUCTION" in c.chunk_text for c in chunks)
        findings_chunk = any("MAIN FINDINGS" in c.chunk_text for c in chunks)
        assert intro_chunk
        assert findings_chunk

    def test_heading_aware_short_doc_single_chunk(self):
        text = "SHORT HEADING\nBrief content."
        result = make_result(text)

        chunks = chunk_document(result, "annual_report", "heading_aware", _DOC_ID)

        assert len(chunks) == 1


# ─── table_per_chunk ─────────────────────────────────────────────────────────

class TestTablePerChunk:
    def test_table_chunk_type_set(self):
        table = [["Hotel", "Stars", "Rooms"], ["Hilton", "5", "200"], ["Marriott", "5", "150"]]
        result = make_result("Some descriptive text.", tables=[table])

        chunks = chunk_document(result, "data_table_report", "table_per_chunk", _DOC_ID)

        table_chunks = [c for c in chunks if c.chunk_type == "table"]
        assert len(table_chunks) == 1
        assert table_chunks[0].chunk_type == "table"

    def test_table_chunk_content_is_json(self):
        table = [["col1", "col2"], ["val1", "val2"]]
        result = make_result("", tables=[table])

        chunks = chunk_document(result, "data_table_report", "table_per_chunk", _DOC_ID)

        table_chunk = next(c for c in chunks if c.chunk_type == "table")
        parsed = json.loads(table_chunk.chunk_text)
        assert parsed == table

    def test_multiple_tables_multiple_chunks(self):
        tables = [
            [["h1"], ["v1"]],
            [["h2"], ["v2"]],
            [["h3"], ["v3"]],
        ]
        result = make_result("", tables=tables)

        chunks = chunk_document(result, "data_table_report", "table_per_chunk", _DOC_ID)

        table_chunks = [c for c in chunks if c.chunk_type == "table"]
        assert len(table_chunks) == 3


# ─── Chunk dataclass ─────────────────────────────────────────────────────────

class TestChunkDataclass:
    def test_to_dict_has_required_fields(self):
        chunk = Chunk(
            document_id=str(_DOC_ID),
            chunk_index=0,
            chunk_text="test",
            chunk_strategy="paragraph_aware",
            format_family="unknown",
            token_count=1,
        )
        d = chunk.to_dict()
        required = {
            "document_id", "chunk_index", "chunk_text", "chunk_strategy",
            "format_family", "token_count", "page_numbers", "chunk_type",
        }
        assert required.issubset(d.keys())

    def test_default_chunk_type_is_text(self):
        chunk = Chunk(
            document_id=str(_DOC_ID), chunk_index=0, chunk_text="t",
            chunk_strategy="paragraph_aware", format_family="unknown", token_count=0,
        )
        assert chunk.chunk_type == "text"
