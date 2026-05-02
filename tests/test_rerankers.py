"""Tests for reranker plugin loader + RerankerProtocol (Plan 08-01).

Behaviors locked to Phase 8 CONTEXT.md D-CONTRACT-01..05 / D-POOL-01..04 /
D-CONFIG-03. Implementation lands in Plan 08-01.
"""
from __future__ import annotations

import pytest


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


def test_top_n_clamp_warns(capsys, mock_reranker_entry_point):
    """top_n > len(candidates) reranks all + emits err_console warning (D-POOL-02)."""
    from supamem.config import ResolvedConfig
    from supamem.rerankers.mxbai_v2 import MxbaiV2Reranker
    from supamem.retrieval.types import RetrievedChunk
    cfg = ResolvedConfig()  # reranker_top_n = 50 default
    # Bypass load_reranker (which would try to instantiate via entry-points);
    # instantiate the default impl directly. Patch _ensure to a fake model so
    # we don't pull mxbai_rerank / torch.
    r = MxbaiV2Reranker(config=cfg)

    class _FakeModel:
        def rank(self, q, docs, **kw):
            class _R:
                def __init__(self, idx, score):
                    self.index = idx
                    self.score = score
            # Reverse-order ranking, score = N..1
            return [_R(len(docs) - 1 - i, float(len(docs) - i)) for i in range(len(docs))]

    r._model = _FakeModel()
    chunks = [RetrievedChunk(id=str(i), text=f"t{i}", score=0.001 * i) for i in range(3)]
    out = r.rerank("q", chunks)
    captured = capsys.readouterr()
    assert "reranker_top_n" in captured.err
    assert "reranking all" in captured.err
    assert len(out) == 3


def test_off_short_circuits_without_loading_model(monkeypatch):
    """load_reranker('off', cfg) returns None and never iterates entry-points."""
    import supamem.rerankers as rr
    from supamem.config import ResolvedConfig

    called = {"count": 0}

    def _spy(*a, **kw):
        called["count"] += 1
        return []

    monkeypatch.setattr(rr, "entry_points", _spy)
    cfg = ResolvedConfig()
    assert rr.load_reranker("off", cfg) is None
    assert called["count"] == 0
