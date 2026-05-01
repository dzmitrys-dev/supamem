"""Tests for ``supamem.indexer.run_index`` and the hash manifest (Plan 80.6-04 Task 1).

Locks the D-25 hybrid schema: every tuned upsert must carry both a ``dense``
and ``sparse`` named vector. Locks the fail-soft contract: Qdrant unreachable
must return 0, never raise — calling hooks should never break because Qdrant
is down.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.indexer import run_index
from supamem.indexer.manifest import Manifest


# ───── Manifest tests ──────────────────────────────────────────────────────


def test_manifest_load_missing_returns_empty(tmp_path: Path) -> None:
    m = Manifest.load(tmp_path / "nope.json")
    assert m.entries == {}


def test_manifest_needs_index_true_when_sha_changed(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"/doc.md": {"prod": "old", "tuned": "old"}}), encoding="utf-8")
    m = Manifest.load(p)
    assert m.needs_index("/doc.md", "new", "tuned") is True


def test_manifest_needs_index_false_when_sha_same(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"/doc.md": {"prod": "abc", "tuned": "abc"}}), encoding="utf-8"
    )
    m = Manifest.load(p)
    assert m.needs_index("/doc.md", "abc", "tuned") is False


def test_manifest_save_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    m = Manifest(entries={"/doc.md": {"prod": "x", "tuned": "y"}})
    m.save(p)
    loaded = Manifest.load(p)
    assert loaded.entries == {"/doc.md": {"prod": "x", "tuned": "y"}}


def test_manifest_legacy_flat_format_upgrades(tmp_path: Path) -> None:
    """Legacy ``{path: sha}`` should read as ``{path: {prod: sha, tuned: ''}}``."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"/doc.md": "legacy_sha"}), encoding="utf-8")
    m = Manifest.load(p)
    assert m.entries["/doc.md"] == {"prod": "legacy_sha", "tuned": ""}


# ───── run_index tests ─────────────────────────────────────────────────────


def _make_md(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


def test_run_index_failsoft_on_qdrant_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan acceptance: Qdrant unreachable → run_index returns 0, never raises."""
    src = _make_md(tmp_path, "doc.md", "# Title\nbody\n")

    class _BoomClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("connection refused")

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "QdrantClient", _BoomClient, raising=False)

    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
    )
    rc = run_index(target="tuned", force=False, sources=[str(src)], config=cfg)
    assert rc == 0, f"fail-soft: expected exit 0, got {rc}"


def test_run_index_skips_unchanged_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-populate manifest with current sha → no upsert calls."""
    body = "# Title\nstable body\n"
    src = _make_md(tmp_path, "doc.md", body)

    import hashlib

    sha = hashlib.sha256(body.encode()).hexdigest()
    abs_path = str(src.resolve())

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manifest_path = cache_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({abs_path: {"prod": sha, "tuned": sha}}), encoding="utf-8"
    )

    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    fake_dense = MagicMock()
    fake_sparse = MagicMock()

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "QdrantClient", lambda *a, **k: fake_client, raising=False)
    monkeypatch.setattr(
        indexer_mod, "build_dense_embedder", lambda *a, **k: fake_dense, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_sparse_embedder", lambda *a, **k: fake_sparse, raising=False
    )

    cfg = ResolvedConfig(sources=[str(src)], cache_dir=str(cache_dir))
    rc = run_index(target="tuned", force=False, sources=[str(src)], config=cfg)
    assert rc == 0
    fake_client.upsert.assert_not_called()


def test_run_index_chunks_and_upserts_hybrid_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locked D-25 schema: every PointStruct must carry both `dense` and `sparse` vectors."""
    # Each chunk must pass CHUNK_MIN_TOKENS=20 — pad with enough words.
    para = " ".join(["lorem"] * 30)
    body = f"# Header\n{para}\n## Sub\n{para}\n"
    src = _make_md(tmp_path, "doc.md", body)
    cache_dir = tmp_path / "cache"

    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])
    fake_sparse = MagicMock()

    class _SparseVec:
        def __init__(self) -> None:
            self.indices = [1, 2, 3]
            self.values = [0.5, 0.4, 0.3]

    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "QdrantClient", lambda *a, **k: fake_client, raising=False)
    monkeypatch.setattr(
        indexer_mod, "build_dense_embedder", lambda *a, **k: fake_dense, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_sparse_embedder", lambda *a, **k: fake_sparse, raising=False
    )

    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(cache_dir),
        collection="my_test_collection",
    )
    rc = run_index(target="tuned", force=True, sources=[str(src)], config=cfg)
    assert rc == 0

    assert fake_client.upsert.called, "expected upsert to be invoked"
    call = fake_client.upsert.call_args
    points = call.kwargs.get("points") or call.args[1]
    assert points, "expected at least one PointStruct"
    for point in points:
        vec = point.vector
        assert "dense" in vec, f"missing dense vector in {vec.keys()}"
        assert "sparse" in vec, f"missing sparse vector in {vec.keys()}"


def test_run_index_returns_zero_on_no_sources(tmp_path: Path) -> None:
    """Empty source list is a no-op success."""
    cfg = ResolvedConfig(sources=[], cache_dir=str(tmp_path / "cache"))
    rc = run_index(target="tuned", force=False, sources=[], config=cfg)
    assert rc == 0


# ───── Plan 07-02 — payload.room classification at index time ─────────────


def _wire_indexer_mocks(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stand up fake Qdrant + fake embedders for indexer integration tests."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    # Default scroll: return no points → sweep is a no-op when invoked.
    fake_client.scroll.return_value = ([], None)
    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        def __init__(self) -> None:
            self.indices = [1, 2, 3]
            self.values = [0.5, 0.4, 0.3]

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


def _collect_upserted_points(fake_client: MagicMock) -> list[Any]:
    out: list[Any] = []
    for call in fake_client.upsert.call_args_list:
        pts = call.kwargs.get("points") or (call.args[1] if len(call.args) > 1 else [])
        out.extend(pts)
    return out


def test_indexer_payload_room_present_for_backend_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File under ``src/`` → payload['room'] == 'backend' (D-06 + D-11)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    para = " ".join(["lorem"] * 30)
    body = f"# H\n{para}\n## Sub\n{para}\n"
    src = src_dir / "foo.md"
    src.write_text(body, encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
        collection="test_room",
    )
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)

    points = _collect_upserted_points(fake_client)
    assert points, "expected at least one upserted point"
    for point in points:
        assert "room" in point.payload, "payload.room MUST be present (D-06)"
        assert point.payload["room"] == "backend"


def test_indexer_payload_room_null_for_unmatched_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File under ``data/`` (no default keyword) → payload['room'] is None."""
    data_dir = tmp_path / "data" / "chest_xray"
    data_dir.mkdir(parents=True)
    para = " ".join(["lorem"] * 30)
    body = f"# H\n{para}\n"
    src = data_dir / "notes.md"
    src.write_text(body, encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
        collection="test_room",
    )
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)

    points = _collect_upserted_points(fake_client)
    assert points
    for point in points:
        assert "room" in point.payload, "payload.room MUST always be present (D-06)"
        assert point.payload["room"] is None


def test_indexer_payload_room_for_tests_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tests/`` path component → payload['room'] == 'tests' (D-01a priority)."""
    tdir = tmp_path / "tests"
    tdir.mkdir()
    para = " ".join(["lorem"] * 30)
    body = f"# H\n{para}\n"
    src = tdir / "spec.md"
    src.write_text(body, encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
        collection="test_room",
    )
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)

    points = _collect_upserted_points(fake_client)
    assert points
    for point in points:
        assert point.payload["room"] == "tests"


def test_indexer_hash_drift_writes_classifier_hash_to_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First post-upgrade run: manifest.classifier_hash starts None, ends == current.

    Locks D-08 + D-10 + R-04: missing __classifier_hash__ → drift from None →
    sweep runs (no-op here because scroll returns []) → post-sweep manifest
    persists the new hash so subsequent runs are zero-overhead.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    para = " ".join(["lorem"] * 30)
    body = f"# H\n{para}\n"
    src = src_dir / "doc.md"
    src.write_text(body, encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
        collection="test_room",
    )
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)

    # First run triggered the drift gate (None != current); scroll was called.
    assert fake_client.scroll.called, "expected sweep on classifier_hash drift"
    manifest_path = tmp_path / "cache" / "manifest.json"
    assert manifest_path.exists()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "__classifier_hash__" in raw
    assert isinstance(raw["__classifier_hash__"], str)
    assert len(raw["__classifier_hash__"]) == 64  # sha256 hex


def test_indexer_no_sweep_when_classifier_hash_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second invocation with unchanged config → ZERO scroll calls (D-08)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    para = " ".join(["lorem"] * 30)
    body = f"# H\n{para}\n"
    src = src_dir / "doc.md"
    src.write_text(body, encoding="utf-8")

    fake_client = _wire_indexer_mocks(monkeypatch)
    cfg = ResolvedConfig(
        sources=[str(src)],
        cache_dir=str(tmp_path / "cache"),
        collection="test_room",
    )
    # First run: writes classifier_hash to manifest.
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)
    fake_client.reset_mock()

    # Second run: hash matches → sweep gate does NOT fire.
    run_index(target="tuned", force=True, sources=[str(src)], config=cfg)
    assert not fake_client.scroll.called, (
        "second run with stable classifier_hash MUST NOT scroll"
    )


# ───── Plan 06-04 B2 — _parse_since + _filter_jsonl_by_since ──────────────


def test_parse_since_supports_d_and_h() -> None:
    """B2 / D-21: ``--since`` accepts Nd / Nh; 0 disables; None → default_days."""
    from supamem.cli import _parse_since

    assert _parse_since("180d", default_days=180) == 180 * 86400
    assert _parse_since("24h", default_days=180) == 24 * 3600
    assert _parse_since(None, default_days=180) == 180 * 86400
    assert _parse_since("0", default_days=180) is None
    assert _parse_since("0d", default_days=180) is None
    assert _parse_since("0h", default_days=180) is None


def test_parse_since_rejects_malformed() -> None:
    """T-06-x10: malformed --since raises typer.BadParameter."""
    import typer

    from supamem.cli import _parse_since

    with pytest.raises(typer.BadParameter):
        _parse_since("nope", default_days=180)
    with pytest.raises(typer.BadParameter):
        _parse_since("30days", default_days=180)


def test_filter_jsonl_by_since_drops_old_files(tmp_path: Path) -> None:
    """B2: mtime-filter excludes JSONL files older than the recency window."""
    import os
    import time

    from supamem.cli import _filter_jsonl_by_since

    recent = tmp_path / "recent.jsonl"
    recent.write_text("{}")
    old = tmp_path / "old.jsonl"
    old.write_text("{}")
    old_mtime = time.time() - (30 * 86400)
    os.utime(old, (old_mtime, old_mtime))
    kept = _filter_jsonl_by_since([recent, old], 7 * 86400.0)
    assert recent in kept
    assert old not in kept


def test_filter_jsonl_by_since_disabled_keeps_all(tmp_path: Path) -> None:
    """B2: window_seconds=None disables the filter (--since=0 path)."""
    import os
    import time

    from supamem.cli import _filter_jsonl_by_since

    a = tmp_path / "a.jsonl"
    a.write_text("{}")
    b = tmp_path / "b.jsonl"
    b.write_text("{}")
    old_mtime = time.time() - (365 * 86400)
    os.utime(b, (old_mtime, old_mtime))
    kept = _filter_jsonl_by_since([a, b], None)
    assert set(kept) == {a, b}
