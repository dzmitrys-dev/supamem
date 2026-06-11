"""Lifecycle integration tests for Qdrant collection helpers (Plan 18-C/D, Req-10).

Mock-client coverage for read-error, idempotent ensure, forbidden write guard,
index create-on-missing, and idempotent re-index — no live Qdrant required.
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

from tests.test_indexer_dispatch import _wire_mocks


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "lifecycle_test_coll"),
        **overrides,
    )


def _wire_stateful_collections(
    monkeypatch: pytest.MonkeyPatch, collection_name: str
) -> MagicMock:
    """Mock client whose get_collections reflects create_collection calls."""
    fake_client, _, _ = _wire_mocks(monkeypatch)
    created: list[str] = []

    def _get_collections() -> MagicMock:
        colls = []
        for name in created:
            entry = MagicMock()
            entry.name = name
            colls.append(entry)
        return MagicMock(collections=colls)

    def _create_collection(*, collection_name: str, **kwargs: Any) -> None:
        if collection_name not in created:
            created.append(collection_name)

    fake_client.get_collections.side_effect = _get_collections
    fake_client.create_collection.side_effect = _create_collection
    fake_client.scroll.return_value = ([], None)
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

    fake_client, _, _ = _wire_mocks(monkeypatch)
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

    fake_client, _, _ = _wire_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(md)],
        cache_dir=str(tmp_path / "cache"),
        collection="lifecycle_new_coll",
    )

    rc = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc == 0
    fake_client.create_collection.assert_called_once()
    assert fake_client.upsert.called


def test_index_idempotent_when_collection_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive run_index calls must not recreate or delete the collection."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    coll = "lifecycle_idempotent_coll"
    fake_client = _wire_stateful_collections(monkeypatch, coll)
    cfg = ResolvedConfig(
        sources=[str(md)],
        cache_dir=str(tmp_path / "cache"),
        collection=coll,
    )

    rc1 = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc1 == 0
    first_create_count = fake_client.create_collection.call_count
    assert first_create_count == 1

    rc2 = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc2 == 0
    assert fake_client.create_collection.call_count <= 1
    fake_client.delete_collection.assert_not_called()
