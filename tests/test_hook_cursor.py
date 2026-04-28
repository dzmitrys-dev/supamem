"""Tests for ``supamem.hooks.cursor.run_snapshot`` (Plan 80.6-07 Task 2).

Locks the `.cursor/rules/dual-memory-snapshot.mdc` regenerator contract:
- Scrolls Qdrant via mocked client → renders top-k entries to a Markdown file
- Hard cap at 500 lines (D-36 token budget guard)
- Atomic write via tempfile + os.replace
- Sanitizes QDRANT_URL / QDRANT_API_KEY in any error output
- Recency-weighted scoring (newer payload wins on tie)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.hooks.cursor import run_snapshot


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "test_cursor"}
    base.update(overrides)
    return ResolvedConfig(**base)


def _scroll_points(count: int, *, fixed_ts: float = 1.7e9) -> tuple[list[Any], None]:
    """Build ``count`` MagicMock scroll points with deterministic payloads."""
    points: list[Any] = []
    for i in range(count):
        p = MagicMock()
        p.id = f"p{i}"
        p.payload = {
            "document": f"chunk content {i}",
            "source": f"src/file_{i}.py",
            "indexed_at_epoch": fixed_ts + i,
        }
        points.append(p)
    return (points, None)  # qdrant scroll returns (points, next_offset)


def test_run_snapshot_writes_mdc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "dual-memory-snapshot.mdc"
    fake_client = MagicMock()
    fake_client.scroll.return_value = _scroll_points(5)

    import supamem.hooks.cursor as mod

    monkeypatch.setattr(mod, "_get_client", lambda cfg: fake_client)
    rc = run_snapshot(config=_cfg(), output_path=out_path)
    assert rc == 0
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    assert body.count("chunk content") >= 1


def test_run_snapshot_caps_at_500_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "dual-memory-snapshot.mdc"
    fake_client = MagicMock()
    fake_client.scroll.return_value = _scroll_points(1000)

    import supamem.hooks.cursor as mod

    monkeypatch.setattr(mod, "_get_client", lambda cfg: fake_client)
    rc = run_snapshot(config=_cfg(), output_path=out_path)
    assert rc == 0
    line_count = len(out_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 500, f"line cap breached: {line_count}"


def test_run_snapshot_atomic_via_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.replace is the atomic-rename primitive — verify it's called."""
    out_path = tmp_path / "dual-memory-snapshot.mdc"
    fake_client = MagicMock()
    fake_client.scroll.return_value = _scroll_points(2)

    import supamem.hooks.cursor as mod

    monkeypatch.setattr(mod, "_get_client", lambda cfg: fake_client)

    captured: dict[str, Any] = {}
    real_replace = os.replace

    def spy_replace(src: Any, dst: Any) -> None:
        captured["src"] = str(src)
        captured["dst"] = str(dst)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    rc = run_snapshot(config=_cfg(), output_path=out_path)
    assert rc == 0
    assert captured.get("dst") == str(out_path)
    assert captured.get("src", "") and captured["src"] != str(out_path)


def test_run_snapshot_sanitizes_qdrant_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If a Qdrant exception leaks the URL, stderr must redact it."""
    secret_url = "https://leak.example:6333"
    monkeypatch.setenv("QDRANT_URL", secret_url)

    fake_client = MagicMock()
    fake_client.scroll.side_effect = RuntimeError(f"connection failed to {secret_url}")

    import supamem.hooks.cursor as mod

    monkeypatch.setattr(mod, "_get_client", lambda cfg: fake_client)
    rc = run_snapshot(config=_cfg(), output_path=tmp_path / "out.mdc")
    assert rc == 0  # fail-soft
    captured = capsys.readouterr()
    combined = captured.err + captured.out
    assert secret_url not in combined
    assert "QDRANT_URL_REDACTED" in combined


def test_run_snapshot_recency_weighted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two points with different indexed_at_epoch — newer should rank first."""
    out_path = tmp_path / "dual-memory-snapshot.mdc"

    older = MagicMock()
    older.id = "old"
    older.payload = {
        "document": "OLDER_DOC",
        "source": "src/old.py",
        "indexed_at_epoch": 1.0e9,  # 2001
    }
    newer = MagicMock()
    newer.id = "new"
    newer.payload = {
        "document": "NEWER_DOC",
        "source": "src/new.py",
        "indexed_at_epoch": 1.7e9,  # 2023
    }
    fake_client = MagicMock()
    fake_client.scroll.return_value = ([older, newer], None)

    import supamem.hooks.cursor as mod

    monkeypatch.setattr(mod, "_get_client", lambda cfg: fake_client)
    rc = run_snapshot(config=_cfg(), output_path=out_path, top_k=2)
    assert rc == 0
    body = out_path.read_text(encoding="utf-8")
    # Newer must appear before older in the rendered list.
    assert body.index("NEWER_DOC") < body.index("OLDER_DOC")
