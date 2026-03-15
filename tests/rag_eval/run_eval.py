"""
RAG Evaluation Runner.
Runs ground_truth.json queries through the live RAG pipeline
and scores correctness (keyword presence) and grounding.

Usage:
    python tests/rag_eval/run_eval.py [--verbose]

Target: >= 80% (16/20) correct.
Requires: running PostgreSQL + Qdrant with ingested data + GEMINI_API_KEY set.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from sltda_mcp.config import configure_logging, get_settings
from sltda_mcp.database import close_pool, init_pool
from sltda_mcp.mcp_server.rag import run_rag
from sltda_mcp.qdrant_client import close_client, init_client

logger = logging.getLogger(__name__)

GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.json"
PASS_THRESHOLD = 0.80


def _score_correctness(answer: str, expected_keywords: list[str]) -> bool:
    """All expected keywords must appear in the answer (case-insensitive)."""
    answer_lower = answer.lower()
    return all(kw.lower() in answer_lower for kw in expected_keywords)


async def run_evaluation(verbose: bool = False) -> dict:
    settings = get_settings()
    await init_pool()
    await init_client()

    with open(GROUND_TRUTH_PATH) as f:
        ground_truth = json.load(f)

    results = []
    passed = 0

    for item in ground_truth:
        qid = item["id"]
        query = item["query"]
        expected_keywords = item["expected_keywords"]

        try:
            rag_result = await run_rag(query)
            correct = _score_correctness(rag_result.answer, expected_keywords)
            status = "PASS" if correct else "FAIL"
            if correct:
                passed += 1
        except Exception as exc:
            status = "ERROR"
            rag_result = None
            logger.error("Query %d failed: %s", qid, exc)

        result = {
            "id": qid,
            "query": query,
            "status": status,
            "expected_keywords": expected_keywords,
            "answer": rag_result.answer if rag_result else "ERROR",
            "confidence": rag_result.confidence if rag_result else "n/a",
            "chunks_retrieved": len(rag_result.chunks) if rag_result else 0,
        }
        results.append(result)

        if verbose:
            kw_found = [kw for kw in expected_keywords if kw.lower() in result["answer"].lower()]
            kw_missing = [kw for kw in expected_keywords if kw.lower() not in result["answer"].lower()]
            print(
                f"[{status}] Q{qid}: {query[:60]}...\n"
                f"       keywords found={kw_found}, missing={kw_missing}\n"
                f"       confidence={result['confidence']}, chunks={result['chunks_retrieved']}\n"
            )
        else:
            print(f"[{status}] Q{qid}: {query[:70]}")

    score = passed / len(ground_truth)
    summary = {
        "total": len(ground_truth),
        "passed": passed,
        "failed": len(ground_truth) - passed,
        "score": f"{score:.1%}",
        "target": f"{PASS_THRESHOLD:.0%}",
        "result": "PASS" if score >= PASS_THRESHOLD else "FAIL",
        "details": results,
    }

    print(f"\n{'='*50}")
    print(f"RAG Eval: {passed}/{len(ground_truth)} correct ({score:.1%}) — {summary['result']}")
    print(f"Target: {PASS_THRESHOLD:.0%}")
    print(f"{'='*50}")

    await close_pool()
    await close_client()

    return summary


if __name__ == "__main__":
    configure_logging()
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    summary = asyncio.run(run_evaluation(verbose=verbose))
    sys.exit(0 if summary["result"] == "PASS" else 1)
