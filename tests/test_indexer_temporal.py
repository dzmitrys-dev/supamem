"""Tests for indexer Phase 9 helpers — TEMP-01 close-old + auto-GC + eager
migration + payload indexes + manifest validity-migration reserved key.

Phase 9 RED stubs (Wave 0). Turn GREEN in Plan 03 (indexer side).

Decision references (see .planning/phases/09-per-source-temporal-validity):
- D-CID-01: ``_chunk_id`` extends to ``uuid5(NAMESPACE_URL,
  f"{file_path}#chunk={idx}#hash={content_hash}")``. Idempotent for unchanged
  content; new uuid for changed content (the literal TEMP-01 mechanism).
- D-CID-02: ``_transcript_chunk_id`` stays content-independent (transcripts
  are append-only by construction).
- D-WINDOW-01: per-file scroll-and-close BEFORE upsert. ONE batched
  ``set_payload({"valid_to": now_iso}, points=ids, wait=True)`` per file
  (Phase 7 D-09 batching invariant).
- D-WINDOW-02: gated by manifest content-hash drift detection — pure no-op
  re-indexes do NOT fire the close-window scroll.
- D-VFROM-01: ``valid_from`` from ``Path.stat().st_mtime`` ISO-8601 UTC.
- D-VFROM-02: zero/negative mtime falls back to ``now()`` with ONE warning
  per indexer run (NOT per file).
- D-GC-01: auto-GC at end of ``supamem index``. Form A — scroll → batch IDs
  → ``client.delete(points_selector=PointIdsList(points=ids))`` (RESEARCH §R-5).
- D-INDEX-01..02: payload indexes on ``valid_to`` (DATETIME) and ``chunker``
  (KEYWORD). Idempotent re-creation; fail-soft + surface via ``err_console``.
- D-NULL-03: optional eager migration sweep (gated by manifest reserved key
  ``__validity_migration__``).

Pitfall references:
- Pitfall 4 (chunk-id collision): ``_chunk_id`` and ``_transcript_chunk_id``
  MUST NEVER produce equal uuids for any reasonable inputs.
- Pitfall 7 (eager-migration ordering): strict order in ``run_index`` is
  ``_ensure_payload_indexes → _eager_validity_migration → existing
  reclassify_sweep → per-file close-old → upsert → _gc_sweep``. The
  validity migration MUST run BEFORE per-file close-old, otherwise legacy
  pre-Phase-9 points have no ``valid_to`` payload and the close-window
  IsEmpty filter would consume them.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qdrant_client.http import models as qmodels

from tests.conftest import _cfg_with_temporal


def _fake_point(pid: int, file_path: str, **payload_extras) -> SimpleNamespace:
    """Build a fake Qdrant scroll record with optional payload keys."""
    payload = {"file_path": file_path, **payload_extras}
    return SimpleNamespace(id=pid, payload=payload)


# ─────────────────────────────────────────────────────────────────────────────
# D-CID-01 / D-CID-02 — _chunk_id content-hash signature
# ─────────────────────────────────────────────────────────────────────────────


def test_chunk_id_signature_requires_content_hash():
    """D-CID-01: ``_chunk_id`` MUST accept ``content_hash`` parameter.

    This locks the API shape so an accidental rollback to the 2-arg form
    (which produces uuid collisions on content drift, breaking TEMP-01)
    fails CI immediately.
    """
    from supamem.indexer import _chunk_id

    sig = inspect.signature(_chunk_id)
    assert "content_hash" in sig.parameters, (
        "D-CID-01: _chunk_id must accept content_hash; rolling back to 2-arg "
        "form breaks TEMP-01 (changed-content uuid stability)"
    )


def test_chunk_id_idempotent_on_unchanged_content():
    """D-CID-01: same (path, idx, hash) → same uuid (idempotent re-index)."""
    from supamem.indexer import _chunk_id

    sig = inspect.signature(_chunk_id)
    if "content_hash" not in sig.parameters:
        pytest.skip("Plan 03 lands content_hash parameter")
    a = _chunk_id("/foo/bar.py", 0, "abc123")
    b = _chunk_id("/foo/bar.py", 0, "abc123")
    assert a == b


def test_chunk_id_changes_on_content_drift():
    """D-CID-01: changed content → DIFFERENT uuid (the supersede mechanism)."""
    from supamem.indexer import _chunk_id

    sig = inspect.signature(_chunk_id)
    if "content_hash" not in sig.parameters:
        pytest.skip("Plan 03 lands content_hash parameter")
    a = _chunk_id("/foo/bar.py", 0, "hash1")
    b = _chunk_id("/foo/bar.py", 0, "hash2")
    assert a != b


def test_chunk_id_does_not_collide_with_transcript_id():
    """Pitfall 4: filesystem and transcript id-spaces must NEVER overlap."""
    from supamem.indexer import _chunk_id, _transcript_chunk_id

    sig = inspect.signature(_chunk_id)
    if "content_hash" not in sig.parameters:
        pytest.skip("Plan 03 lands content_hash parameter")
    fs_id = _chunk_id("/foo/bar.py", 0, "abc123")
    # Reasonable transcript inputs.
    tx_id = _transcript_chunk_id(
        "session-uuid-abc", "message-uuid-xyz", 0
    )
    assert fs_id != tx_id


# ─────────────────────────────────────────────────────────────────────────────
# D-WINDOW-01 — _close_validity_window
# ─────────────────────────────────────────────────────────────────────────────


def _import_close_validity_window():
    try:
        from supamem.indexer import _close_validity_window
    except ImportError:
        pytest.skip("Plan 03 lands _close_validity_window")
    return _close_validity_window


def test_close_validity_window_sets_valid_to_now():
    """D-WINDOW-01: scroll → ONE batched set_payload({"valid_to": now_iso})."""
    fn = _import_close_validity_window()
    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, "/path/to/file"), _fake_point(2, "/path/to/file")],
        None,
    )
    cfg = _cfg_with_temporal()
    fn(client, cfg, "/path/to/file")
    assert client.set_payload.call_count == 1
    call = client.set_payload.call_args
    assert call.kwargs.get("wait") is True
    payload = call.kwargs.get("payload")
    assert payload is not None and "valid_to" in payload
    assert payload["valid_to"] is not None  # iso string, not None
    assert sorted(call.kwargs.get("points", [])) == [1, 2]


def test_close_validity_window_no_points_returns_zero():
    """Empty scroll → returns 0; set_payload NOT called."""
    fn = _import_close_validity_window()
    client = MagicMock()
    client.scroll.return_value = ([], None)
    cfg = _cfg_with_temporal()
    n = fn(client, cfg, "/path/to/file")
    assert n == 0
    assert client.set_payload.call_count == 0


def test_close_validity_window_filter_uses_isempty_and_file_path():
    """D-WINDOW-01: scroll filter is ``file_path == path AND IsEmpty(valid_to)``.

    Idempotent — already-closed chunks (where valid_to is non-empty) are
    excluded so calling twice on the same file is a no-op.
    """
    fn = _import_close_validity_window()
    client = MagicMock()
    client.scroll.return_value = ([], None)
    cfg = _cfg_with_temporal()
    fn(client, cfg, "/path/to/file")
    # Inspect the scroll_filter passed to client.scroll.
    call = client.scroll.call_args
    scroll_filter = call.kwargs.get("scroll_filter") or call.kwargs.get("filter")
    assert scroll_filter is not None, "close-window must pass a scroll filter"
    # Recursively walk to find IsEmptyCondition AND file_path FieldCondition.
    found_isempty = False
    found_file_path = False

    def _walk(node):
        nonlocal found_isempty, found_file_path
        if isinstance(node, qmodels.IsEmptyCondition) and node.is_empty.key == "valid_to":
            found_isempty = True
        if isinstance(node, qmodels.FieldCondition) and node.key == "file_path":
            if getattr(node.match, "value", None) == "/path/to/file":
                found_file_path = True
        for attr in ("must", "should", "must_not"):
            children = getattr(node, attr, None) or []
            for c in children:
                _walk(c)

    _walk(scroll_filter)
    assert found_isempty, "close-window filter MUST include IsEmpty(valid_to)"
    assert found_file_path, "close-window filter MUST match file_path exactly"


def test_close_validity_window_batches_at_512():
    """Phase 7 D-09 batching invariant: 1500 points → 3 set_payload calls.

    NOT one set_payload per point; ONE batched call per scroll page.
    """
    fn = _import_close_validity_window()
    client = MagicMock()
    page1 = ([_fake_point(i, "/p") for i in range(512)], "off1")
    page2 = ([_fake_point(i, "/p") for i in range(512, 1024)], "off2")
    page3 = ([_fake_point(i, "/p") for i in range(1024, 1500)], None)
    client.scroll.side_effect = [page1, page2, page3]
    cfg = _cfg_with_temporal()
    fn(client, cfg, "/p")
    assert client.set_payload.call_count == 3
    sizes = [len(c.kwargs.get("points", [])) for c in client.set_payload.call_args_list]
    assert sizes == [512, 512, 476]


# ─────────────────────────────────────────────────────────────────────────────
# D-NULL-03 — _eager_validity_migration
# ─────────────────────────────────────────────────────────────────────────────


def _import_eager_validity_migration():
    try:
        from supamem.indexer import _eager_validity_migration
    except ImportError:
        pytest.skip("Plan 03 lands _eager_validity_migration")
    return _eager_validity_migration


def test_eager_migration_sets_valid_to_null_on_legacy_points():
    """D-NULL-03: legacy points with no valid_to → batched set_payload({"valid_to": None}).

    Mirrors the classifier-hash sweep shape; gated by manifest reserved key
    ``__validity_migration__`` so it runs at most ONCE per upgrade.
    """
    fn = _import_eager_validity_migration()
    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, "/a.py"), _fake_point(2, "/b.py")],
        None,
    )
    cfg = _cfg_with_temporal()
    fn(client, cfg)
    assert client.set_payload.call_count >= 1
    call = client.set_payload.call_args_list[0]
    payload = call.kwargs.get("payload")
    assert payload is not None and "valid_to" in payload
    assert payload["valid_to"] is None  # explicit-null marker
    assert call.kwargs.get("wait") is True


@pytest.mark.skip(
    reason="Plan 03 wires _eager_validity_migration into run_index — Pitfall 7 ordering"
)
def test_eager_migration_runs_BEFORE_close_old():
    """Pitfall 7: strict ordering invariant.

    ``_ensure_payload_indexes → _eager_validity_migration → reclassify_sweep
    → per-file close-old → upsert → _gc_sweep``.

    The eager migration MUST run BEFORE per-file close-old; otherwise legacy
    points have no ``valid_to`` payload field, the close-window IsEmpty(valid_to)
    filter would consume them, and they'd all be marked superseded on the
    first post-upgrade re-index.
    """
    # Integration-style test — exercised via run_index recording-mock.
    # Plan 03 wiring task sets the call sequence; Wave 0 only encodes the
    # assertion shape.
    from supamem.indexer import run_index  # noqa: F401

    pytest.fail("Pitfall 7 ordering test — Plan 03 wires the call sequence")


# ─────────────────────────────────────────────────────────────────────────────
# D-GC-01 — _gc_sweep (Form A: PointIdsList delete)
# ─────────────────────────────────────────────────────────────────────────────


def _import_gc_sweep():
    try:
        from supamem.indexer import _gc_sweep
    except ImportError:
        pytest.skip("Plan 03 lands _gc_sweep")
    return _gc_sweep


def test_gc_disabled_when_retention_zero():
    """D-GC-01: retention_days=0 is the kept-forever escape hatch.

    Returns 0; client.scroll NOT called — collection-size compliance mode.
    """
    fn = _import_gc_sweep()
    client = MagicMock()
    cfg = _cfg_with_temporal(retention_days=0)
    n = fn(client, cfg, retention_days=0)
    assert n == 0
    assert client.scroll.call_count == 0
    assert client.delete.call_count == 0


def test_gc_uses_form_a_pointidslist_delete():
    """D-GC-01 + RESEARCH §R-5: Form A scroll → PointIdsList delete.

    Server-side ``delete(filter=...)`` is rejected (Form B) because it
    hides the count from doctor + Welford telemetry.
    """
    fn = _import_gc_sweep()
    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(10, "/a"), _fake_point(20, "/b"), _fake_point(30, "/c")],
        None,
    )
    cfg = _cfg_with_temporal(retention_days=90)
    fn(client, cfg, retention_days=90)
    assert client.delete.call_count >= 1
    call = client.delete.call_args_list[0]
    selector = call.kwargs.get("points_selector")
    assert isinstance(selector, qmodels.PointIdsList), (
        "GC must use PointIdsList (Form A); FilterSelector hides count"
    )
    assert sorted(selector.points) == [10, 20, 30]
    assert call.kwargs.get("wait") is True


def test_gc_filter_uses_datetime_range_lt_cutoff():
    """D-GC-01: scroll filter is ``valid_to < (now - retention_days)``."""
    fn = _import_gc_sweep()
    client = MagicMock()
    client.scroll.return_value = ([], None)
    cfg = _cfg_with_temporal(retention_days=90)
    fn(client, cfg, retention_days=90)
    call = client.scroll.call_args
    scroll_filter = call.kwargs.get("scroll_filter") or call.kwargs.get("filter")
    assert scroll_filter is not None
    found_lt = False

    def _walk(node):
        nonlocal found_lt
        if isinstance(node, qmodels.FieldCondition) and node.key == "valid_to":
            r = getattr(node, "range", None)
            if isinstance(r, qmodels.DatetimeRange) and r.lt is not None:
                found_lt = True
        for attr in ("must", "should", "must_not"):
            for c in getattr(node, attr, None) or []:
                _walk(c)

    _walk(scroll_filter)
    assert found_lt, "GC filter MUST be DatetimeRange(lt=cutoff_iso) on valid_to"


def test_gc_batches_at_512():
    """Phase 7 D-09: 1500 expired points → 3 delete calls."""
    fn = _import_gc_sweep()
    client = MagicMock()
    page1 = ([_fake_point(i, "/p") for i in range(512)], "off1")
    page2 = ([_fake_point(i, "/p") for i in range(512, 1024)], "off2")
    page3 = ([_fake_point(i, "/p") for i in range(1024, 1500)], None)
    client.scroll.side_effect = [page1, page2, page3]
    cfg = _cfg_with_temporal(retention_days=90)
    fn(client, cfg, retention_days=90)
    assert client.delete.call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# D-INDEX-01 / D-INDEX-02 — _ensure_payload_indexes
# ─────────────────────────────────────────────────────────────────────────────


def _import_ensure_payload_indexes():
    try:
        from supamem.indexer import _ensure_payload_indexes
    except ImportError:
        pytest.skip("Plan 03 lands _ensure_payload_indexes")
    return _ensure_payload_indexes


def test_ensure_indexes_creates_valid_to_datetime():
    """D-INDEX-01: payload index on ``valid_to`` with DATETIME schema.

    Without this, the always-on Range(gt=now) clause falls back to a
    brute-force scan and degrades latency on large collections.
    """
    fn = _import_ensure_payload_indexes()
    client = MagicMock()
    cfg = _cfg_with_temporal()
    fn(client, cfg)
    calls = client.create_payload_index.call_args_list
    assert any(
        c.kwargs.get("field_name") == "valid_to"
        and c.kwargs.get("field_schema") == qmodels.PayloadSchemaType.DATETIME
        and c.kwargs.get("wait") is True
        for c in calls
    ), "D-INDEX-01: must create_payload_index(field_name='valid_to', field_schema=DATETIME)"


def test_ensure_indexes_creates_chunker_keyword():
    """D-INDEX-02: payload index on ``chunker`` (KEYWORD).

    Lets the transcript-decay loop iterate only candidates with
    ``chunker == 'transcript'`` without a brute-force scan.
    """
    fn = _import_ensure_payload_indexes()
    client = MagicMock()
    cfg = _cfg_with_temporal()
    fn(client, cfg)
    calls = client.create_payload_index.call_args_list
    assert any(
        c.kwargs.get("field_name") == "chunker"
        and c.kwargs.get("field_schema") == qmodels.PayloadSchemaType.KEYWORD
        and c.kwargs.get("wait") is True
        for c in calls
    ), "D-INDEX-02: must create_payload_index(field_name='chunker', field_schema=KEYWORD)"


def test_ensure_indexes_idempotent_on_failure_surfaces_error(capsys):
    """D-INDEX failure mode: surface via err_console; never crash run_index.

    qdrant-client raises on truly-unrecoverable index ops; CLAUDE.md
    forbids silent except-pass on indexing paths — error MUST be surfaced.
    """
    fn = _import_ensure_payload_indexes()
    client = MagicMock()
    client.create_payload_index.side_effect = RuntimeError("boom")
    cfg = _cfg_with_temporal()
    # Should NOT raise; failure surfaces via err_console.
    fn(client, cfg)
    # err_console writes to stderr; captured via capsys.
    err = capsys.readouterr().err
    assert "boom" in err or "RuntimeError" in err or "index" in err.lower(), (
        "ensure_temporal_indexes must surface failures via err_console (CLAUDE.md)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Payload literal — valid_from + valid_to=None on new chunks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="Plan 03 wires payload literal extension in _index_records")
def test_new_chunks_have_valid_from_iso_mtime_and_null_valid_to():
    """D-VFROM-01: new chunks carry ``valid_from = ISO(mtime)`` + ``valid_to = None``.

    Integration-style — Plan 03 wires the payload literal at the upsert
    construction site in indexer/__init__.py.
    """
    pytest.fail("Plan 03 wires payload literal — see PATTERNS §payload-literal-extension")


@pytest.mark.skip(reason="Plan 03 wires zero-mtime fallback warning")
def test_zero_mtime_falls_back_to_now_with_one_warning():
    """D-VFROM-02: st_mtime <= 0 → wall-clock fallback + ONE warning per RUN.

    NOT one warning per file — accumulator pattern (mirrors Phase 7 sweep
    failures) so a corrupted-filesystem batch produces a single line, not
    spam.
    """
    pytest.fail("Plan 03 wires the zero-mtime fallback path")


# ─────────────────────────────────────────────────────────────────────────────
# manifest.validity_migration — D-NULL-03 reserved key
# ─────────────────────────────────────────────────────────────────────────────


def test_manifest_validity_migration_field_default_none():
    """Empty manifest → ``validity_migration is None`` (Phase 8 byte-stable rollback)."""
    from supamem.indexer.manifest import Manifest

    m = Manifest()
    assert hasattr(m, "validity_migration"), (
        "D-NULL-03: Manifest must expose validity_migration field"
    )
    assert m.validity_migration is None


def test_manifest_validity_migration_round_trip(tmp_path):
    """Set version → save → load → preserved; reserved key is ``__validity_migration__``."""
    from supamem.indexer.manifest import Manifest

    path = tmp_path / "manifest.json"
    m = Manifest()
    m.validity_migration = "0.3.0a1"
    m.save(path)

    import json as _json

    raw = _json.loads(path.read_text())
    assert "__validity_migration__" in raw, (
        "D-NULL-03: reserved key MUST be '__validity_migration__' "
        "(parallel to __classifier_hash__)"
    )
    assert raw["__validity_migration__"] == "0.3.0a1"

    loaded = Manifest.load(path)
    assert loaded.validity_migration == "0.3.0a1"


def test_manifest_omits_key_when_none_byte_stable(tmp_path):
    """``validity_migration is None`` → key NOT emitted; Phase 8 manifests round-trip identical.

    Mirrors classifier_hash byte-stable rollback at manifest.py:50-54.
    """
    from supamem.indexer.manifest import Manifest

    path = tmp_path / "manifest.json"
    m = Manifest()
    assert m.validity_migration is None
    m.save(path)

    import json as _json

    raw = _json.loads(path.read_text())
    assert "__validity_migration__" not in raw, (
        "Byte-stable rollback: when validity_migration is None, the key MUST "
        "NOT be emitted (mirrors classifier_hash convention)"
    )
