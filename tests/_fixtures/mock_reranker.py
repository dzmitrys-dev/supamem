"""Test-only reranker plugin (no torch / no HF download).

Deterministic reverse-order reranker -- score = N, N-1, ..., 1 from
end-to-start of the input candidates. Used by tests/test_rerankers.py
+ tests/test_tuned_hybrid_rerank.py via monkeypatched entry_points.
"""
from __future__ import annotations

from supamem.config import ResolvedConfig
from supamem.retrieval.types import RetrievedChunk


class MockReranker:
    name = "mock"
    model_id = "test/mock-reranker"

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        out: list[RetrievedChunk] = []
        for i, c in enumerate(reversed(candidates)):
            score = float(len(candidates) - i)
            out.append(c.model_copy(update={"score": score, "rerank_score": score}))
        return out
