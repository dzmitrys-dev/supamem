"""Phase 18 Plan G — opt-in heuristic dedup (Req-04).

Content-hash short-circuit + configurable cosine merge on tuned_hybrid read path
and optional write-time in-batch skip. Default OFF preserves pre-Phase-18 behavior.
"""
from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.memory_writer import _index_single_doc
from supamem.retrieval.tuned_hybrid import _dedup_hits


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "test_heuristic_dedup"),
        **overrides,
    )


def _hit(
    doc_id: str,
    *,
    content_hash: str,
    vec: list[float] | None,
    document: str = "body",
    score: float = 1.0,
) -> tuple[str, float, dict, list[float] | None]:
    return (
        doc_id,
        score,
        {"document": document, "content_hash": content_hash},
        vec,
    )


def _unit_vec(dim: int, axis: int) -> list[float]:
    v = [0.0] * dim
    v[axis] = 1.0
    return v


def _near_duplicate_vec() -> list[float]:
    base = 1.0 / math.sqrt(4)
    return [base, base, base, base]


def test_dedup_default_off_returns_all_chunks() -> None:
    """dedup_enabled=False: orthogonal vectors pass through (no cosine collapse)."""
    cfg = _cfg(dedup_enabled=False)
    hits = [
        _hit("a", content_hash="hash-a", vec=_unit_vec(8, 0)),
        _hit("b", content_hash="hash-b", vec=_unit_vec(8, 1)),
        _hit("c", content_hash="hash-c", vec=_unit_vec(8, 2)),
    ]
    out = _dedup_hits(hits, cfg)
    assert [h[0] for h in out] == ["a", "b", "c"]


def test_hash_dedup_collapses_identical_content_hash() -> None:
    """When enabled, identical content_hash collapses even if doc_id differs."""
    cfg = _cfg(dedup_enabled=True)
    shared_hash = "deadbeef" * 8
    hits = [
        _hit("doc-1", content_hash=shared_hash, vec=_unit_vec(8, 0)),
        _hit(
            "doc-2",
            content_hash=shared_hash,
            vec=_unit_vec(8, 1),
            document="other body",
        ),
    ]
    out = _dedup_hits(hits, cfg)
    assert len(out) == 1
    assert out[0][0] == "doc-1"


def test_cosine_dedup_merges_similar_vectors() -> None:
    """Cosine >= threshold merges without requiring matching content_hash."""
    cfg = _cfg(dedup_enabled=True, dedup_cosine_threshold=0.97)
    vec = _near_duplicate_vec()
    hits = [
        _hit("first", content_hash="hash-one", vec=list(vec)),
        _hit(
            "second",
            content_hash="hash-two",
            vec=list(vec),
            document="near dup",
        ),
    ]
    out = _dedup_hits(hits, cfg)
    assert len(out) == 1
    assert out[0][0] == "first"


def test_distinct_hashes_different_content_kept() -> None:
    """Different hashes and low cosine similarity → both kept when enabled."""
    cfg = _cfg(dedup_enabled=True, dedup_cosine_threshold=0.97)
    hits = [
        _hit("keep-a", content_hash="aaa", vec=_unit_vec(8, 0)),
        _hit("keep-b", content_hash="bbb", vec=_unit_vec(8, 1)),
    ]
    out = _dedup_hits(hits, cfg)
    assert [h[0] for h in out] == ["keep-a", "keep-b"]


def test_write_dedup_skips_duplicate_hash_in_batch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory_writer skips duplicate content_hash in same upsert batch when enabled."""
    from pathlib import Path

    root = Path(tmp_path)
    target = root / "note.md"
    target.write_text("x", encoding="utf-8")

    upserted: list[int] = []

    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    fake_client.create_collection.side_effect = lambda **_: None
    fake_client.upsert.side_effect = lambda **kw: upserted.append(len(kw["points"]))

    class _SparseVec:
        indices = [1]
        values = [0.5]

    monkeypatch.setattr("qdrant_client.QdrantClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(
        "supamem.embedders.build_dense_embedder",
        lambda: MagicMock(embed=lambda batch: iter([[0.1] * 384 for _ in batch])),
    )
    monkeypatch.setattr(
        "supamem.embedders.build_sparse_embedder",
        lambda: MagicMock(embed=lambda batch: iter([_SparseVec() for _ in batch])),
    )
    # Two chunks with identical body → same file-level content_hash
    monkeypatch.setattr(
        "supamem.indexer.chunker.chunk_markdown",
        lambda body: ["chunk-a", "chunk-b"],
    )

    cfg = _cfg(dedup_enabled=True, collection="agent-dedup-test")
    n = _index_single_doc(
        cfg,
        target_path=target,
        body="same parent hash",
        point_id="00000000-0000-0000-0000-000000000001",
    )
    assert n == 1
    assert upserted == [1]
