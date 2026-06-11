"""Lifecycle integration tests for Qdrant collection helpers (Plan 18-C, Req-10).

Mock-client coverage for read-error, idempotent ensure, forbidden write guard,
and index create-on-missing — no live Qdrant required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from qdrant_client.http.exceptions import UnexpectedResponse

from supamem.config import ResolvedConfig
from supamem.indexer import run_index
from supamem.qdrant_collection import (
    CollectionMissingError,
    ensure_collection,
    validate_writable_collection,
)
from supamem.retrieval.tuned_hybrid import TunedHybridBackend


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "lifecycle_test_coll"),
        **overrides,
    )


def _wire_indexer_mocks(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mirror ``test_indexer_dispatch._wire_mocks`` for lifecycle tests."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    fake_client.query_points.return_value = MagicMock(points=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(
        indexer_mod, "QdrantClient", lambda *a, **k: fake_client, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_dense_embedder", lambda *a, **k: fake_dense, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_sparse_embedder", lambda *a, **k: fake_sparse, raising=False
    )
    return fake_client


def test_search_missing_collection_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TunedHybridBackend.query raises CollectionMissingError before query_points."""
    import supamem.retrieval.tuned_hybrid as mod

    coll = "missing_lifecycle_coll"
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


def test_ensure_collection_idempotent_no_delete() -> None:
    """Repeated ensure_collection must never delete an existing collection."""
    client = MagicMock()
    existing = MagicMock()
    existing.name = "bench_coll"
    client.get_collections.return_value = MagicMock(collections=[existing])

    assert ensure_collection(client, "bench_coll") is False
    assert ensure_collection(client, "bench_coll") is False

    client.delete_collection.assert_not_called()
    client.create_collection.assert_not_called()


def test_forbidden_collection_write_blocked_index_path() -> None:
    """validate_writable_collection blocks legacy names before index upsert."""
    cfg = _cfg(collection="dev_memory")
    with pytest.raises(RuntimeError, match="forbidden"):
        validate_writable_collection(cfg)


def test_forbidden_collection_write_blocked_run_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_index must not upsert when collection is forbidden."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(md)],
        cache_dir=str(tmp_path / "cache"),
        collection="dev_memory",
    )

    with pytest.raises(RuntimeError, match="forbidden"):
        run_index(target="tuned", force=True, sources=[str(md)], config=cfg)

    fake_client.upsert.assert_not_called()


def test_index_creates_collection_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_index creates hybrid collection once when missing, then upserts."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(md)],
        cache_dir=str(tmp_path / "cache"),
        collection="lifecycle_new_coll",
    )

    rc = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc == 0
    fake_client.create_collection.assert_called_once()
    assert fake_client.upsert.called
