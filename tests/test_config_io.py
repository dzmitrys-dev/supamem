"""Tests for supamem.config_io (plan 80.6-02)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from supamem.config_io import (
    BackupNotWritten,
    atomic_write_json,
    compute_diff,
    deep_merge_json,
    extract_managed_block,
    wrap_managed_block,
)


# ──────────────────────────── atomic_write_json ────────────────────────────


def test_atomic_write_creates_bak_then_replaces(tmp_path: Path) -> None:
    target = tmp_path / "cfg.json"
    target.write_text('{"old": true}')
    result = atomic_write_json(target, {"new": True})
    assert result.written is True
    assert result.backup_path is not None and result.backup_path.exists()
    assert result.backup_path.name.startswith("cfg.json.bak.")
    assert '"new"' in target.read_text()
    assert '"old"' in result.backup_path.read_text()


def test_atomic_write_no_op_when_identical(tmp_path: Path) -> None:
    target = tmp_path / "cfg.json"
    atomic_write_json(target, {"k": 1})
    result2 = atomic_write_json(target, {"k": 1})
    assert result2.written is False
    assert result2.backup_path is None


def test_atomic_write_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "cfg.json"
    target.write_text('{"old": true}')
    mtime_before = target.stat().st_mtime_ns
    result = atomic_write_json(target, {"new": True}, dry_run=True)
    assert result.written is False
    assert result.diff != ""
    assert "+" in result.diff
    assert target.stat().st_mtime_ns == mtime_before
    assert target.read_text() == '{"old": true}'


def test_atomic_write_rejects_non_serializable(tmp_path: Path) -> None:
    target = tmp_path / "cfg.json"
    with pytest.raises(TypeError):
        atomic_write_json(target, {"k": object()})
    assert not target.exists()  # nothing written before failure


def test_atomic_write_crash_leaves_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cfg.json"
    target.write_text('{"old": true}')
    real_replace = os.replace

    def boom(src: str | Path, dst: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"new": True})
    monkeypatch.setattr(os, "replace", real_replace)
    assert target.read_text() == '{"old": true}'  # original intact


# ──────────────────────────── deep_merge_json ─────────────────────────────


def test_deep_merge_dicts_recursive() -> None:
    result = deep_merge_json({"a": {"b": 1}}, {"a": {"c": 2}})
    assert result == {"a": {"b": 1, "c": 2}}


def test_deep_merge_list_dedup_by_equality() -> None:
    result = deep_merge_json(
        {"hooks": [{"x": 1}]},
        {"hooks": [{"x": 1}, {"x": 2}]},
    )
    assert result == {"hooks": [{"x": 1}, {"x": 2}]}


def test_deep_merge_replace_marker() -> None:
    result = deep_merge_json(
        {"a": [9, 9]},
        {"a": {"__supamem_replace__": True, "value": [1, 2]}},
    )
    assert result == {"a": [1, 2]}


# ──────────────────────────── managed-block fences ─────────────────────────


def test_managed_block_roundtrip() -> None:
    wrapped = wrap_managed_block("hello\nworld", version="0.1.0")
    assert "BEGIN SUPAMEM v0.1.0 MANAGED BLOCK" in wrapped
    before, owned, after = extract_managed_block(wrapped)
    assert before == ""
    assert owned == "hello\nworld"
    assert after == ""


def test_managed_block_preserves_user_edits() -> None:
    inner = wrap_managed_block("supamem-owned line", version="0.1.0")
    text = f"user line\n{inner}\nafter line\n"
    before, owned, after = extract_managed_block(text)
    assert before == "user line\n"
    assert owned == "supamem-owned line"
    assert after.endswith("after line\n")


def test_managed_block_rejects_multiple_begins() -> None:
    inner = wrap_managed_block("a", version="0.1.0")
    inner2 = wrap_managed_block("b", version="0.1.0")
    text = f"{inner}\n{inner2}"
    with pytest.raises(ValueError):
        extract_managed_block(text)


# ──────────────────────────── compute_diff ─────────────────────────────────


def test_compute_diff_unified_format() -> None:
    diff = compute_diff(
        old="line one\nline two\n",
        new="line one\nline TWO\n",
        fromfile="old",
        tofile="new",
    )
    assert "--- old" in diff
    assert "+++ new" in diff
    assert "@@" in diff


# Sanity: BackupNotWritten is exported (referenced by callers later)
def test_backup_not_written_is_exception() -> None:
    assert issubclass(BackupNotWritten, Exception)
