"""Tests for reranker plugin loader + RerankerProtocol (Plan 08-01).

Behaviors locked to Phase 8 CONTEXT.md D-CONTRACT-01..05 / D-POOL-01..04 /
D-CONFIG-03. RED skeleton in Wave 0; impl in Wave 1.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.xfail(
    reason="RED skeleton -- implementation lands in Plan 08-01",
    strict=False,
)


def test_load_reranker_off_returns_none():
    from supamem.config import ResolvedConfig
    from supamem.rerankers import load_reranker
    cfg = ResolvedConfig()
    assert load_reranker("off", cfg) is None


def test_load_reranker_unknown_raises_lookup():
    from supamem.config import ResolvedConfig
    from supamem.rerankers import load_reranker
    cfg = ResolvedConfig()
    with pytest.raises(LookupError, match="bogus"):
        load_reranker("bogus", cfg)


def test_load_reranker_returns_instance(mock_reranker_entry_point):
    from supamem.config import ResolvedConfig
    from supamem.rerankers import load_reranker
    cfg = ResolvedConfig()
    r = load_reranker("mock", cfg)
    assert r is not None
    assert hasattr(r, "rerank")


def test_score_replaced_not_blended(mock_reranker_entry_point):
    from supamem.config import ResolvedConfig
    from supamem.rerankers import load_reranker
    from supamem.retrieval.types import RetrievedChunk
    cfg = ResolvedConfig()
    r = load_reranker("mock", cfg)
    chunks = [RetrievedChunk(id=str(i), text=f"t{i}", score=0.001 * i) for i in range(3)]
    out = r.rerank("q", chunks)
    assert out[0].score == 3.0
    assert out[0].rerank_score == 3.0
    assert out[-1].score == 1.0


def test_empty_candidates_short_circuit(mock_reranker_entry_point):
    from supamem.config import ResolvedConfig
    from supamem.rerankers import load_reranker
    cfg = ResolvedConfig()
    r = load_reranker("mock", cfg)
    assert r.rerank("q", []) == []
