"""Tests for ``supamem.retrieval.tuned_hybrid`` (Plan 80.6-04 Task 2).

Locks D-25 hybrid retrieval — Qdrant native sparse-dense FusionQuery with
prefetch over (dense, sparse). Verifies forbidden-collection guard, sparse
availability error, and ``load_retrieval`` plugin discovery.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.retrieval import load_retrieval
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "test_hybrid_collection"),
        **overrides,
    )


def test_forbidden_collection_guard() -> None:
    """Refuse a protected production collection name unless explicit opt-in."""
    cfg = _cfg(collection="dev_memory")
    with pytest.raises(RuntimeError, match="forbidden"):
        TunedHybridBackend(config=cfg)


def test_forbidden_collection_allowed_with_opt_in() -> None:
    """``allow_legacy_collection=True`` overrides the guard."""
    cfg = _cfg(collection="dev_memory")
    cfg.allow_legacy_collection = True  # type: ignore[attr-defined]
    backend = TunedHybridBackend(config=cfg)
    assert backend.config.collection == "dev_memory"


def test_query_missing_collection_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing collection on read path raises CollectionMissingError before query_points."""
    import supamem.retrieval.tuned_hybrid as mod
    from qdrant_client.http.exceptions import UnexpectedResponse

    from supamem.qdrant_collection import CollectionMissingError

    coll = "missing_bench_coll"
    fake_client = MagicMock()
    fake_client.get_collection.side_effect = UnexpectedResponse(
        status_code=404,
        reason_phrase="Not Found",
        content=b"",
        headers={},
    )

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    backend = TunedHybridBackend(config=_cfg(collection=coll))
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    with pytest.raises(CollectionMissingError, match=r"(?i)supamem (index|init)") as exc_info:
        backend.query("hello", k=5)

    assert coll in str(exc_info.value)
    fake_client.query_points.assert_not_called()


def test_setup_raises_actionable_when_sparse_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If fastembed sparse is unavailable, _ensure() must raise a clear RuntimeError."""
    import supamem.retrieval.tuned_hybrid as mod

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(mod, "_SPARSE_IMPORT_ERROR", ImportError("no sparse"), raising=False)
    backend = TunedHybridBackend(config=_cfg())
    with pytest.raises(RuntimeError, match="fastembed"):
        backend._ensure()


def test_query_invokes_qdrant_with_fusion_rrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query() must call Qdrant query_points with FusionQuery(RRF) + 2 Prefetches."""
    import supamem.retrieval.tuned_hybrid as mod
    from qdrant_client.http import models as qmodels

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(points=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        indices = [1, 2]
        values = [0.5, 0.4]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    backend = TunedHybridBackend(config=_cfg())
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    backend.query("hello world", k=5)

    fake_client.query_points.assert_called_once()
    kwargs = fake_client.query_points.call_args.kwargs
    assert isinstance(kwargs["query"], qmodels.FusionQuery)
    assert kwargs["query"].fusion == qmodels.Fusion.RRF
    prefetch = kwargs["prefetch"]
    assert len(prefetch) == 2, f"expected 2 prefetches (dense + sparse), got {len(prefetch)}"


def test_query_returns_retrieved_chunks_with_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock query_points returns a point list; assert RetrievedChunk emitted."""
    import supamem.retrieval.tuned_hybrid as mod

    fake_client = MagicMock()
    point = MagicMock()
    point.id = "p1"
    point.score = 0.9
    point.payload = {"document": "alpha", "source": "doc.md"}
    point.vector = {"dense": [0.1] * 4}
    fake_client.query_points.return_value = MagicMock(points=[point])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 4 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    backend = TunedHybridBackend(config=_cfg())
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    out = backend.query("alpha", k=5)
    assert len(out) == 1
    chunk = out[0]
    assert isinstance(chunk, RetrievedChunk)
    assert chunk.text == "alpha"
    assert chunk.source_path == "doc.md"
    assert chunk.score > 0


def test_load_retrieval_via_entry_points_returns_tuned_hybrid() -> None:
    """``load_retrieval('tuned_hybrid')`` resolves the registered plugin."""
    cls = load_retrieval("tuned_hybrid")
    assert cls.__name__ == "TunedHybridBackend"


def test_load_retrieval_unknown_raises() -> None:
    with pytest.raises(LookupError):
        load_retrieval("does_not_exist")


# ── Plan 07-03 — where parameter threading (D-02, D-03) ────────────────────


def _make_fake_backend(monkeypatch: pytest.MonkeyPatch) -> tuple[TunedHybridBackend, MagicMock]:
    """Build a TunedHybridBackend with mocked client/dense/sparse for query inspection."""
    import supamem.retrieval.tuned_hybrid as mod

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(points=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 4 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    backend = TunedHybridBackend(config=_cfg())
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse
    return backend, fake_client


def test_query_threads_filter_to_both_prefetch_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-03: Filter built ONCE and threaded to BOTH dense + sparse Prefetch arms
    AND top-level query_filter (defense-in-depth per RESEARCH §Pattern 3)."""
    backend, fake_client = _make_fake_backend(monkeypatch)

    backend.query("hello", k=5, where={"room": "backend"})

    kwargs = fake_client.query_points.call_args.kwargs
    # Top-level filter present
    assert kwargs.get("query_filter") is not None
    # Both prefetch arms got the SAME filter object (single construction)
    prefetch = kwargs["prefetch"]
    assert len(prefetch) == 2
    assert prefetch[0].filter is not None
    assert prefetch[1].filter is not None
    assert prefetch[0].filter is prefetch[1].filter  # same Python object (D-03)
    assert prefetch[0].filter is kwargs["query_filter"]


def test_query_no_filter_when_where_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When where=None, query_filter contains ONLY the always-on temporal clause.

    Phase 9 D-FILTER-01: ``build_qdrant_filter(None)`` now produces a Filter
    wrapping the IsEmpty(valid_to) ∨ DatetimeRange(gt=now) sub-filter so that
    every retrieval path inherits temporal validity for free. The tuned_hybrid
    backend MUST propagate that filter to ``query_filter`` AND to both Prefetch
    arms (D-03 single construction site).
    """
    from qdrant_client.http import models as qmodels

    backend, fake_client = _make_fake_backend(monkeypatch)

    backend.query("x", k=5)  # default where=None

    kwargs = fake_client.query_points.call_args.kwargs
    qf = kwargs.get("query_filter")
    assert qf is not None
    assert qf.must is not None and len(qf.must) == 1
    # Single must-entry is the nested temporal sub-filter.
    nested = qf.must[0]
    assert isinstance(nested, qmodels.Filter)
    assert nested.should is not None and len(nested.should) == 2
    assert isinstance(nested.should[0], qmodels.IsEmptyCondition)
    # Same Filter object on both Prefetch arms (D-03).
    for pf in kwargs["prefetch"]:
        assert pf.filter is qf


def test_query_filter_shape_matches_match_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The captured Filter wraps the temporal clause AND a MatchValue.

    Phase 9 D-FILTER-01 + D-COMPOSE: temporal sub-filter is prepended to the
    must= list, then the caller's where clauses follow in insertion order.
    """
    backend, fake_client = _make_fake_backend(monkeypatch)
    backend.query("x", k=5, where={"room": "backend"})

    qf = fake_client.query_points.call_args.kwargs["query_filter"]
    assert qf.must is not None and len(qf.must) == 2
    # Position 0: nested temporal sub-filter.
    from qdrant_client.http import models as qmodels

    assert isinstance(qf.must[0], qmodels.Filter)
    assert qf.must[0].should is not None and len(qf.must[0].should) == 2
    # Position 1: the room MatchValue.
    cond = qf.must[1]
    assert isinstance(cond, qmodels.FieldCondition)
    assert cond.key == "room"
    assert cond.match.value == "backend"


def test_dense_stub_accepts_where_kwarg() -> None:
    from supamem.retrieval.dense import DenseBackend

    b = DenseBackend(config=_cfg())
    with pytest.raises(NotImplementedError):
        b.query("x", k=5, where={"room": "backend"})


def test_bm25_stub_accepts_where_kwarg() -> None:
    from supamem.retrieval.bm25 import BM25Backend

    b = BM25Backend(config=_cfg())
    with pytest.raises(NotImplementedError):
        b.query("x", k=5, where={"room": "backend"})
