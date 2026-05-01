"""Tests for the eager-fetch helper supamem.rerankers.prepare (Plan 08-02).

Behaviors locked to D-FETCH-01, D-FETCH-05, D-FETCH-06, D-FETCH-07.
RED skeleton in Wave 0; impl in Wave 1.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.xfail(
    reason="RED skeleton -- implementation lands in Plan 08-02",
    strict=False,
)


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
