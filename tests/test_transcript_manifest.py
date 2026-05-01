"""Tests for the Manifest ``__transcripts__`` extension (Plan 06-03 Task 1).

Locks the R-04 additive shape: per-message dedupe is keyed by
``(session_uuid, message_uuid, content_hash)`` under a top-level
``__transcripts__`` key in the same JSON manifest. Existing file-keyed
entries roundtrip byte-stable.

Task 3 adds end-to-end append-only and modified-message integration tests.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.indexer import run_index
from supamem.indexer.manifest import Manifest


# ───── Backward compatibility ──────────────────────────────────────────────


def test_legacy_manifest_load_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps({"/doc.md": {"prod": "x", "tuned": "y"}}), encoding="utf-8"
    )
    m = Manifest.load(p)
    assert m.entries == {"/doc.md": {"prod": "x", "tuned": "y"}}
    assert m.transcripts == {}


def test_save_without_transcripts_omits_key(tmp_path: Path) -> None:
    """Lock: empty transcripts dict MUST NOT pollute legacy JSON output."""
    p = tmp_path / "manifest.json"
    m = Manifest(entries={"/doc.md": {"prod": "x", "tuned": "y"}})
    m.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "__transcripts__" not in raw
    assert raw == {"/doc.md": {"prod": "x", "tuned": "y"}}


# ───── New transcript API ──────────────────────────────────────────────────


def test_save_with_transcripts_adds_top_level_key(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    m = Manifest(
        entries={"/doc.md": {"prod": "x", "tuned": "y"}},
    )
    m.transcript_update("session-A", "msg-1", "hash-1")
    m.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "__transcripts__" in raw
    assert "session-A" in raw["__transcripts__"]
    assert raw["__transcripts__"]["session-A"]["msg-1"]["content_hash"] == "hash-1"
    # Roundtrip
    loaded = Manifest.load(p)
    assert loaded.entries == {"/doc.md": {"prod": "x", "tuned": "y"}}
    assert "session-A" in loaded.transcripts
    assert loaded.transcripts["session-A"]["msg-1"]["content_hash"] == "hash-1"


def test_transcript_needs_index_unknown_session() -> None:
    m = Manifest()
    assert m.transcript_needs_index("new-session", "m1", "h1") is True


def test_transcript_needs_index_known_message_same_hash() -> None:
    m = Manifest()
    m.transcript_update("s1", "m1", "h1")
    assert m.transcript_needs_index("s1", "m1", "h1") is False


def test_transcript_needs_index_changed_hash() -> None:
    m = Manifest()
    m.transcript_update("s1", "m1", "h1")
    assert m.transcript_needs_index("s1", "m1", "h2") is True


def test_transcript_update_records_iso_timestamp() -> None:
    m = Manifest()
    m.transcript_update("s1", "m1", "h1")
    rec = m.transcripts["s1"]["m1"]
    assert "content_hash" in rec
    assert rec["content_hash"] == "h1"
    # Must parse as ISO 8601
    parsed = datetime.fromisoformat(rec["indexed_at"])
    assert parsed is not None


def test_legacy_roundtrip_with_transcripts(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    payload = {
        "/doc.md": {"prod": "p", "tuned": "t"},
        "__transcripts__": {
            "s1": {"m1": {"content_hash": "h1", "indexed_at": "2026-01-01T00:00:00+00:00"}}
        },
    }
    p.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    m = Manifest.load(p)
    assert m.entries == {"/doc.md": {"prod": "p", "tuned": "t"}}
    assert m.transcripts == {
        "s1": {"m1": {"content_hash": "h1", "indexed_at": "2026-01-01T00:00:00+00:00"}}
    }
    # Save back; reload preserves both
    out = tmp_path / "out.json"
    m.save(out)
    m2 = Manifest.load(out)
    assert m2.entries == m.entries
    assert m2.transcripts == m.transcripts


# ───── Phase 7 (Plan 07-02 Task 1) — classifier_hash reserved key ─────────


def test_manifest_classifier_hash_default_none(tmp_path: Path) -> None:
    """Empty manifest.json → classifier_hash defaults to None (R-04 backward compat)."""
    p = tmp_path / "manifest.json"
    p.write_text("{}", encoding="utf-8")
    m = Manifest.load(p)
    assert m.classifier_hash is None


def test_manifest_classifier_hash_roundtrip(tmp_path: Path) -> None:
    """Save with classifier_hash set → load preserves it (D-10)."""
    p = tmp_path / "manifest.json"
    m = Manifest(entries={}, transcripts={}, classifier_hash="abc123")
    m.save(p)
    m2 = Manifest.load(p)
    assert m2.classifier_hash == "abc123"


def test_manifest_byte_stable_when_classifier_hash_none(tmp_path: Path) -> None:
    """Conditional emit: no __classifier_hash__ key when value is None.

    Mirrors the __transcripts__ byte-stability lock so a Phase-6-era manifest
    (no classifier hash) round-trips byte-identical bytes.
    """
    p = tmp_path / "manifest.json"
    m = Manifest(entries={"foo.py": {"prod": "x", "tuned": "y"}}, transcripts={})
    m.save(p)
    body = p.read_text(encoding="utf-8")
    assert "__classifier_hash__" not in body


def test_manifest_entries_filter_skips_classifier_hash(tmp_path: Path) -> None:
    """load() must NOT leak __classifier_hash__ into entries dict (D-10)."""
    p = tmp_path / "manifest.json"
    p.write_text(
        '{"foo.py": {"prod": "x", "tuned": "y"}, "__classifier_hash__": "abc"}',
        encoding="utf-8",
    )
    m = Manifest.load(p)
    assert "__classifier_hash__" not in m.entries
    assert "foo.py" in m.entries
    assert m.classifier_hash == "abc"


def test_double_underscore_namespace_does_not_collide(tmp_path: Path) -> None:
    """Only the literal key ``__transcripts__`` is special; other ``__x__``
    keys are filtered out (they should never appear; this guards against
    accidental future schema collisions)."""
    p = tmp_path / "manifest.json"
    payload = {
        "/doc.md": {"prod": "p", "tuned": "t"},
        "__future_thing__": {"foo": "bar"},
        "__transcripts__": {"s1": {"m1": {"content_hash": "h", "indexed_at": "x"}}},
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    m = Manifest.load(p)
    assert "/doc.md" in m.entries
    assert "__future_thing__" not in m.entries
    assert m.transcripts == {"s1": {"m1": {"content_hash": "h", "indexed_at": "x"}}}


# ───── End-to-end append-only integration (Task 3, D-25, INGEST-04) ────────


_FIXTURE = Path(__file__).parent / "fixtures" / "transcripts" / "simple_session.jsonl"


def _wire_index_mocks(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
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


def _append_pair(jsonl: Path, user_uuid: str, parent: str, asst_uuid: str) -> None:
    """Append a synthetic Q+A pair that mirrors the simple_session shape."""
    new_user = (
        '{"type":"user","uuid":"' + user_uuid + '","parentUuid":"' + parent
        + '","sessionId":"s1","timestamp":"2026-01-02T00:00:00Z","cwd":"/tmp",'
        '"version":"1.5.0","gitBranch":"main","isSidechain":false,'
        '"userType":"external","message":{"role":"user","content":"new question"}}'
    )
    new_asst = (
        '{"type":"assistant","uuid":"' + asst_uuid + '","parentUuid":"' + user_uuid
        + '","sessionId":"s1","timestamp":"2026-01-02T00:00:01Z","cwd":"/tmp",'
        '"version":"1.5.0","gitBranch":"main","isSidechain":false,'
        '"userType":"external","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"new answer"}]}}'
    )
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(new_user + "\n")
        fh.write(new_asst + "\n")


def test_append_only_new_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append one Q+A pair → only 1 new ChunkRecord upserted; existing skipped."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    fake_client = _wire_index_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(jsonl)], cache_dir=str(tmp_path / "cache"), collection="t"
    )

    rc1 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc1 == 0
    first_points = sum(
        len(call.kwargs.get("points") or call.args[1])
        for call in fake_client.upsert.call_args_list
    )
    assert first_points >= 3  # 3 turn pairs in fixture

    # Append a 4th pair to the file
    _append_pair(jsonl, user_uuid="u4", parent="a3", asst_uuid="a4")

    fake_client.reset_mock()
    rc2 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc2 == 0
    new_points = sum(
        len(call.kwargs.get("points") or call.args[1])
        for call in fake_client.upsert.call_args_list
    )
    assert new_points == 1, f"expected exactly 1 new chunk, got {new_points}"


def test_modified_message_purge_then_reinsert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Modify an existing message's content → re-index produces 1 upsert at same chunk_id."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    fake_client = _wire_index_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(jsonl)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    rc1 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc1 == 0
    first_ids = []
    for call in fake_client.upsert.call_args_list:
        for pt in call.kwargs.get("points") or call.args[1]:
            first_ids.append(pt.id)

    # Mutate one user message's content (same uuid u2 — different text)
    raw = jsonl.read_text(encoding="utf-8")
    mutated = raw.replace('"content":"how are you"', '"content":"how are you DOING"')
    assert mutated != raw, "fixture mutation did not apply"
    jsonl.write_text(mutated, encoding="utf-8")

    fake_client.reset_mock()
    rc2 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc2 == 0
    second_ids = []
    for call in fake_client.upsert.call_args_list:
        for pt in call.kwargs.get("points") or call.args[1]:
            second_ids.append(pt.id)

    # Exactly one chunk re-emitted (the modified one)
    assert len(second_ids) == 1, f"expected 1 re-upsert, got {len(second_ids)}"
    # Deterministic chunk_id → the new id matches one of the original ids
    # (overwrites in place, no duplication).
    assert second_ids[0] in first_ids
