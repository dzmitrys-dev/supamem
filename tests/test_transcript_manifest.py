"""Tests for the Manifest ``__transcripts__`` extension (Plan 06-03 Task 1).

Locks the R-04 additive shape: per-message dedupe is keyed by
``(session_uuid, message_uuid, content_hash)`` under a top-level
``__transcripts__`` key in the same JSON manifest. Existing file-keyed
entries roundtrip byte-stable.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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
