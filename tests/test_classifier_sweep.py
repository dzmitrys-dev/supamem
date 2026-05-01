"""Hash-drift sweep tests for Phase 7 D-08 / D-09 / D-10.

Locks:
- ``_classifier_hash`` is deterministic AND order-sensitive (sort_keys=False).
- ``_reclassify_sweep`` groups updates by new_room (≤ N set_payload calls
  per scroll page), skips points whose room is already correct, and uses
  ``wait=True`` for idempotency under interruption (R-03).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from supamem.config import ResolvedConfig
from supamem.indexer import _classifier_hash, _reclassify_sweep


def _fake_point(pid: int, file_path: str, room: object = "__missing__") -> SimpleNamespace:
    """Build a fake Qdrant scroll record with optional ``room`` payload."""
    payload: dict = {"file_path": file_path}
    if room != "__missing__":
        payload["room"] = room
    return SimpleNamespace(id=pid, payload=payload)


# ───── _classifier_hash ────────────────────────────────────────────────────


def test_classifier_hash_deterministic() -> None:
    rooms = {"backend": ["src"], "tests": ["tests"]}
    h1 = _classifier_hash(rooms)
    h2 = _classifier_hash(rooms)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_classifier_hash_reflects_priority_order() -> None:
    """D-01a coupling: dict insertion order ENCODES classifier priority
    (first-match-wins). Reordering rooms changes classification outcomes,
    so the digest MUST trip the sweep gate. Uses sort_keys=False (default).
    """
    h1 = _classifier_hash({"a": ["x"], "b": ["y"]})
    h2 = _classifier_hash({"b": ["y"], "a": ["x"]})
    assert h1 != h2


def test_classifier_hash_changes_on_keyword_change() -> None:
    h1 = _classifier_hash({"backend": ["src"]})
    h2 = _classifier_hash({"backend": ["src", "lib"]})
    assert h1 != h2


# ───── _reclassify_sweep ───────────────────────────────────────────────────


def test_reclassify_sweep_groups_by_new_room() -> None:
    """3 distinct new_rooms → ≤ 3 set_payload calls (one per group)."""
    client = MagicMock()
    client.scroll.return_value = (
        [
            _fake_point(1, "src/a.py"),
            _fake_point(2, "tests/b.py"),
            _fake_point(3, "docs/c.md"),
        ],
        None,  # offset=None → loop terminates
    )
    cfg = ResolvedConfig(collection="test_collection")
    n = _reclassify_sweep(client, cfg, batch=512)
    assert n == 3
    assert client.set_payload.call_count <= 3
    # Idempotency: every set_payload call uses wait=True (R-03)
    for call in client.set_payload.call_args_list:
        assert call.kwargs.get("wait") is True
        assert call.kwargs.get("collection_name") == "test_collection"


def test_reclassify_sweep_skips_unchanged_rooms() -> None:
    """A point already classified as 'backend' must NOT be re-payloaded."""
    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, "src/a.py", room="backend")],
        None,
    )
    cfg = ResolvedConfig(collection="test_collection")
    n = _reclassify_sweep(client, cfg, batch=512)
    assert n == 0
    assert client.set_payload.call_count == 0


def test_reclassify_sweep_pre_phase7_missing_room_triggers_update() -> None:
    """Pre-Phase-7 collections (no 'room' in payload) → drift from None to
    classified room → set_payload runs (R-04 backward-compat sweep)."""
    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, "src/a.py")],  # no "room" key at all
        None,
    )
    cfg = ResolvedConfig(collection="test_collection")
    n = _reclassify_sweep(client, cfg, batch=512)
    assert n == 1
    assert client.set_payload.call_count == 1
    call = client.set_payload.call_args
    assert call.kwargs["payload"] == {"room": "backend"}
    assert call.kwargs["points"] == [1]


def test_reclassify_sweep_skips_points_without_file_path() -> None:
    """Defensive: if a point lacks file_path, it cannot be re-classified."""
    client = MagicMock()
    point_no_path = SimpleNamespace(id=99, payload={})
    client.scroll.return_value = ([point_no_path], None)
    cfg = ResolvedConfig(collection="test_collection")
    n = _reclassify_sweep(client, cfg, batch=512)
    assert n == 0
    assert client.set_payload.call_count == 0
