"""Tests for the eager-fetch helper supamem.rerankers.prepare (Plan 08-02).

Behaviors locked to D-FETCH-01, D-FETCH-03, D-FETCH-05, D-FETCH-06, D-FETCH-07.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest


def test_prepare_writes_to_supamem_cache(tmp_cache_dir, monkeypatch):
    from supamem.rerankers import prepare
    called = {}

    def _fake_snapshot(*, repo_id, cache_dir, allow_patterns=None, **kw):
        called["repo_id"] = repo_id
        called["cache_dir"] = cache_dir
        target = Path(cache_dir) / repo_id.replace("/", "--") / "snap"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}")
        return str(target)

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _fake_snapshot)
    out = prepare("mixedbread-ai/mxbai-rerank-base-v2")
    assert called["repo_id"] == "mixedbread-ai/mxbai-rerank-base-v2"
    assert str(out).startswith(str(tmp_cache_dir))


def test_prepare_offline_raises_actionable_error(tmp_cache_dir, network_blocked):
    from supamem.rerankers import prepare
    with pytest.raises(Exception):
        prepare("mixedbread-ai/mxbai-rerank-base-v2")


def test_filelock_prevents_concurrent_corruption(tmp_cache_dir, monkeypatch):
    seen_lock = {"exists": False}

    def _fake_snapshot(*, repo_id, cache_dir, **kw):
        lock_path = Path(cache_dir) / ".lock"
        seen_lock["exists"] = lock_path.exists()
        target = Path(cache_dir) / repo_id.replace("/", "--") / "snap"
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _fake_snapshot)
    from supamem.rerankers import prepare
    prepare("test/x")
    assert seen_lock["exists"] is True


def test_retry_on_transient_then_succeeds(tmp_cache_dir, monkeypatch):
    """3-attempt exponential backoff per D-FETCH-07 (failure UX)."""
    from supamem.rerankers import prepare
    calls = {"n": 0}

    def _flaky(*, repo_id, cache_dir, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        target = Path(cache_dir) / repo_id.replace("/", "--") / "snap"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}")
        return str(target)

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _flaky)
    monkeypatch.setattr("supamem.rerankers._BACKOFF_BASE_S", 0.0)
    prepare("test/x")
    assert calls["n"] == 3


def test_writes_expected_manifest_json(tmp_cache_dir, monkeypatch):
    """Plan 08-03's doctor needs _expected_manifest.json for partial-download detection."""
    from supamem.rerankers import prepare

    def _fake(*, repo_id, cache_dir, **kw):
        target = Path(cache_dir) / repo_id.replace("/", "--") / "snap"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text('{"a":1}')
        (target / "model.safetensors").write_bytes(b"x" * 1024)
        return str(target)

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _fake)
    snap = prepare("test/x")
    m = json.loads((Path(snap) / "_expected_manifest.json").read_text())
    assert m["total_bytes"] >= 1024
    assert "config.json" in m["files"]
    assert "model.safetensors" in m["files"]


def test_filelock_held_during_snapshot(tmp_cache_dir, monkeypatch):
    """W5: lock must actually be HELD during the protected work — not merely 'file exists'."""
    from filelock import FileLock, Timeout

    contention_seen = {"timeout": False}

    def _fake(*, repo_id, cache_dir, **kw):
        lock_path = Path(cache_dir) / ".lock"

        def _contender():
            try:
                FileLock(str(lock_path), timeout=0.1).acquire()
            except Timeout:
                contention_seen["timeout"] = True

        t = threading.Thread(target=_contender)
        t.start()
        t.join(timeout=2.0)
        target = Path(cache_dir) / repo_id.replace("/", "--") / "snap"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}")
        return str(target)

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _fake)
    from supamem.rerankers import prepare
    prepare("test/x")
    assert contention_seen["timeout"] is True, (
        "Lock was NOT held during snapshot_download — concurrent acquire succeeded"
    )


def test_repair_skips_prepare_when_manifest_matches(tmp_cache_dir, monkeypatch):
    """W4: D-FETCH-03 idempotency — manifest match short-circuits the network roundtrip."""
    from supamem.rerankers import _manifest_matches, _model_cache_dir

    cache_root = _model_cache_dir()
    slug = "test--mock-model"
    snap = cache_root / f"models--{slug}" / "snapshots" / "deadbeef"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "config.json").write_text('{"x":1}')
    size = (snap / "config.json").stat().st_size
    manifest = {
        "files": {"config.json": size},
        "total_bytes": size,
        "schema": 1,
    }
    (snap / "_expected_manifest.json").write_text(json.dumps(manifest))

    called = {"n": 0}

    def _must_not_call(**kw):
        called["n"] += 1
        raise AssertionError("snapshot_download called despite manifest match")

    monkeypatch.setattr("supamem.rerankers.snapshot_download", _must_not_call)

    assert _manifest_matches("test/mock-model") is True
    from supamem.rerankers import prepare
    prepare("test/mock-model")
    assert called["n"] == 0
