"""
Load / concurrency test — 20 concurrent tool calls, P95 latency assertions.

Run with: pytest tests/smoke/load_test.py -v -s

Verifies:
  - All 20 concurrent calls complete successfully (no hangs, no crashes)
  - P95 wall-clock latency ≤ 2 000 ms when all external I/O is mocked
  - The Gemini semaphore (Issue #13) does not cause deadlocks under concurrency
"""

import asyncio
import statistics
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.mcp_server.rag import RagChunk, RagResult

# ─── Concurrency target ───────────────────────────────────────────────────────

_CONCURRENCY = 20
_P95_LIMIT_MS = 2_000  # relaxed limit for mocked I/O


# ─── Shared stubs ─────────────────────────────────────────────────────────────

def _make_conn():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    return conn


def _mock_acquire():
    conn = _make_conn()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _rag_result() -> RagResult:
    return RagResult(
        answer="Load test answer.",
        confidence="high",
        chunks=[
            RagChunk(
                chunk_text="Load test content.",
                document_id="doc-load",
                document_name="Load Doc",
                source_url="https://sltda.gov.lk/load.pdf",
                chunk_index=0,
                score=0.80,
            )
        ],
        synthesis_used=True,
        query_expanded=False,
    )


async def _timed(coro) -> float:
    """Run a coroutine and return its wall-clock duration in milliseconds."""
    start = time.perf_counter()
    await coro
    return (time.perf_counter() - start) * 1_000


# ─── Concurrency tests ────────────────────────────────────────────────────────

class TestConcurrentToolCalls:
    """
    Launches _CONCURRENCY concurrent invocations of each tool group
    and asserts P95 latency stays within _P95_LIMIT_MS.
    """

    @pytest.mark.asyncio
    async def test_concurrent_strategic_plan_calls(self):
        from sltda_mcp.mcp_server.tools.strategy import get_strategic_plan

        async def _call(i: int) -> float:
            with patch(
                "sltda_mcp.mcp_server.tools.strategy.run_rag",
                new_callable=AsyncMock,
                return_value=_rag_result(),
            ):
                return await _timed(get_strategic_plan(f"query {i}"))

        durations = await asyncio.gather(*[_call(i) for i in range(_CONCURRENCY)])

        sorted_d = sorted(durations)
        p95 = sorted_d[int(0.95 * len(sorted_d))]
        assert p95 <= _P95_LIMIT_MS, f"P95={p95:.1f}ms exceeds {_P95_LIMIT_MS}ms"
        assert all(isinstance(d, float) for d in durations)

    @pytest.mark.asyncio
    async def test_concurrent_search_calls(self):
        from sltda_mcp.mcp_server.tools.investor import search_sltda_resources

        async def _call(i: int) -> float:
            with patch(
                "sltda_mcp.mcp_server.tools.investor.run_rag",
                new_callable=AsyncMock,
                return_value=_rag_result(),
            ):
                return await _timed(search_sltda_resources(f"search query {i}"))

        durations = await asyncio.gather(*[_call(i) for i in range(_CONCURRENCY)])

        sorted_d = sorted(durations)
        p95 = sorted_d[int(0.95 * len(sorted_d))]
        assert p95 <= _P95_LIMIT_MS, f"P95={p95:.1f}ms exceeds {_P95_LIMIT_MS}ms"

    @pytest.mark.asyncio
    async def test_concurrent_niche_toolkit_calls(self):
        from sltda_mcp.mcp_server.tools.niche import get_niche_toolkit

        toolkit_row = {
            "toolkit_code": "ECO",
            "toolkit_name": "Eco Tourism",
            "target_market": "Nature",
            "key_activities": [],
            "regulatory_notes": "",
            "summary": "Eco summary.",
            "source_text_tokens": 900,
            "source_pages": 8,
            "extraction_confidence": "high",
            "source_url": "https://sltda.gov.lk/eco.pdf",
        }

        async def _call(_i: int) -> float:
            conn = _make_conn()
            conn.fetchrow = AsyncMock(return_value=toolkit_row)
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=conn)
            cm.__aexit__ = AsyncMock(return_value=False)
            with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=cm):
                return await _timed(get_niche_toolkit("ECO", detail_level="summary"))

        durations = await asyncio.gather(*[_call(i) for i in range(_CONCURRENCY)])

        sorted_d = sorted(durations)
        p95 = sorted_d[int(0.95 * len(sorted_d))]
        assert p95 <= _P95_LIMIT_MS, f"P95={p95:.1f}ms exceeds {_P95_LIMIT_MS}ms"

    @pytest.mark.asyncio
    async def test_no_deadlock_under_mixed_load(self):
        """
        Mix of RAG-heavy and DB-only tools running simultaneously.
        Verifies Issue #13 semaphore doesn't block DB-only calls.
        """
        from sltda_mcp.mcp_server.tools.niche import get_niche_categories
        from sltda_mcp.mcp_server.tools.strategy import get_strategic_plan

        async def _rag_call(i: int) -> float:
            with patch(
                "sltda_mcp.mcp_server.tools.strategy.run_rag",
                new_callable=AsyncMock,
                return_value=_rag_result(),
            ):
                return await _timed(get_strategic_plan(f"rag query {i}"))

        async def _db_call(_i: int) -> float:
            cat_rows = [
                {
                    "toolkit_code": "ECO",
                    "toolkit_name": "Eco",
                    "target_market": "Nature",
                    "extraction_confidence": "high",
                }
            ]
            conn = _make_conn()
            conn.fetch = AsyncMock(return_value=cat_rows)
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=conn)
            cm.__aexit__ = AsyncMock(return_value=False)
            with patch("sltda_mcp.mcp_server.tools.niche.acquire", return_value=cm):
                return await _timed(get_niche_categories())

        half = _CONCURRENCY // 2
        rag_calls = [_rag_call(i) for i in range(half)]
        db_calls = [_db_call(i) for i in range(half)]

        durations = await asyncio.gather(*rag_calls, *db_calls)

        assert len(durations) == _CONCURRENCY
        # All must complete — no TimeoutError, no None
        assert all(isinstance(d, float) and d >= 0 for d in durations)

        sorted_d = sorted(durations)
        p95 = sorted_d[int(0.95 * len(sorted_d))]
        assert p95 <= _P95_LIMIT_MS, f"Mixed P95={p95:.1f}ms exceeds {_P95_LIMIT_MS}ms"

    @pytest.mark.asyncio
    async def test_latency_stats_logged(self, capsys):
        """Sanity: print P50/P95/P99 so CI logs show timing profile."""
        from sltda_mcp.mcp_server.tools.strategy import get_strategic_plan

        async def _call(i: int) -> float:
            with patch(
                "sltda_mcp.mcp_server.tools.strategy.run_rag",
                new_callable=AsyncMock,
                return_value=_rag_result(),
            ):
                return await _timed(get_strategic_plan(f"q{i}"))

        durations = await asyncio.gather(*[_call(i) for i in range(_CONCURRENCY)])
        sorted_d = sorted(durations)

        n = len(sorted_d)
        p50 = sorted_d[int(0.50 * n)]
        p95 = sorted_d[int(0.95 * n)]
        p99 = sorted_d[min(int(0.99 * n), n - 1)]
        mean = statistics.mean(sorted_d)

        print(
            f"\nLatency profile (n={n}, mocked): "
            f"mean={mean:.1f}ms P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms"
        )
        assert p95 <= _P95_LIMIT_MS
