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
