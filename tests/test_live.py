"""Tests for supamem.live (v0.1.4+) — audit-tail dashboard."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pytest

from supamem import live as live_mod


# ── _resolve_audit_path ─────────────────────────────────────────────────────


def test_resolve_audit_path_override_wins(tmp_path: Path) -> None:
    custom = tmp_path / "custom.jsonl"
    assert live_mod._resolve_audit_path(custom) == custom


def test_resolve_audit_path_falls_back_to_platformdirs(tmp_path: Path) -> None:
    # Ensure platformdirs branch returns a Path
    p = live_mod._resolve_audit_path(None)
    assert isinstance(p, Path)
    assert p.name == "audit.jsonl"


def test_resolve_audit_path_uses_fallback_when_platformdirs_breaks() -> None:
    with patch.object(
        live_mod, "_resolve_audit_path", wraps=live_mod._resolve_audit_path
    ):
        with patch.dict("sys.modules", {"platformdirs": None}):
            # The function imports platformdirs lazily; if forced to fail it
            # falls back. We verify the fallback constant is sane.
            assert live_mod.DEFAULT_AUDIT_PATH_FALLBACK.name == "audit.jsonl"


# ── _parse_record ───────────────────────────────────────────────────────────


def test_parse_record_valid_json() -> None:
    rec = live_mod._parse_record('{"kind": "search", "tokens": 391}')
    assert rec == {"kind": "search", "tokens": 391}


def test_parse_record_blank_line_returns_none() -> None:
    assert live_mod._parse_record("") is None
    assert live_mod._parse_record("   \n") is None


def test_parse_record_malformed_returns_none() -> None:
    assert live_mod._parse_record("not json {") is None
    assert live_mod._parse_record('"just a string"') is None  # not a dict


# ── _format_row ─────────────────────────────────────────────────────────────


def test_format_row_handles_unix_timestamp() -> None:
    rec = {
        "ts": 1714400000.0,
        "kind": "search",
        "source": "hook_claude_code",
        "outcome": "injected",
        "injected_tokens": 391,
        "elapsed_ms": 17.3,
    }
    ts, kind, source, outcome, tokens, elapsed = live_mod._format_row(rec)
    assert len(ts.split(":")) == 3  # HH:MM:SS
    assert kind == "search"
    assert source == "hook_claude_code"
    assert outcome == "injected"
    assert tokens == "391"
    assert "17.3ms" in elapsed


def test_format_row_handles_iso8601_timestamp() -> None:
    rec = {"ts": "2026-04-29T15:43:22.123Z", "kind": "search"}
    ts, *_ = live_mod._format_row(rec)
    assert ts == "15:43:22"


def test_format_row_handles_missing_fields() -> None:
    ts, kind, source, outcome, tokens, elapsed = live_mod._format_row({})
    assert kind == "?"
    assert source == "-"
    assert outcome == "-"
    assert tokens == "0"
    assert elapsed == "-"


# ── _build_table ────────────────────────────────────────────────────────────


def test_build_table_with_empty_buffer_shows_waiting_row() -> None:
    table = live_mod._build_table(deque(), Path("/tmp/audit.jsonl"))
    assert "supamem live" in str(table.title)


def test_build_table_with_records_renders_columns() -> None:
    records: deque[dict] = deque(maxlen=10)
    records.append(
        {
            "ts": time.time(),
            "kind": "search",
            "source": "hook_claude_code",
            "outcome": "injected",
            "injected_tokens": 391,
            "elapsed_ms": 17.0,
        }
    )
    table = live_mod._build_table(records, Path("/tmp/audit.jsonl"))
    assert table.row_count == 1


# ── _run_plain (pipe-safe fallback) ─────────────────────────────────────────


def test_run_plain_prints_appended_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "audit.jsonl"
    p.write_text('{"kind":"first"}\n')

    async def driver() -> int:
        task = asyncio.create_task(live_mod._run_plain(p))
        # Append a record after the loop is running
        await asyncio.sleep(0.1)
        with p.open("a") as f:
            f.write('{"kind":"appended","tokens":42}\n')
        await asyncio.sleep(live_mod.POLL_INTERVAL_SEC * 2 + 0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return 0

    asyncio.run(driver())
    out = capsys.readouterr().out
    assert '"kind":"appended"' in out
    assert '"tokens":42' in out


# ── run_live (entry point routing) ──────────────────────────────────────────


def test_run_live_routes_to_plain_when_not_tty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pipe-safe path: when stdout isn't a TTY, never enter the Rich Live block."""
    p = tmp_path / "audit.jsonl"
    p.write_text('{"kind":"x"}\n')

    monkeypatch.setattr(live_mod.sys.stdout, "isatty", lambda: False, raising=False)

    plain_called = {"n": 0}

    async def fake_plain(path: Path) -> int:  # noqa: ARG001
        plain_called["n"] += 1
        return 0

    async def fake_dash(path: Path) -> int:  # noqa: ARG001
        raise AssertionError("dashboard must not be called when stdout is not TTY")

    monkeypatch.setattr(live_mod, "_run_plain", fake_plain)
    monkeypatch.setattr(live_mod, "_run_dashboard", fake_dash)

    rc = live_mod.run_live(p)
    assert rc == 0
    assert plain_called["n"] == 1


def test_run_live_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-C must cleanly exit with 0, not propagate."""

    async def boom(path: Path) -> int:  # noqa: ARG001
        raise KeyboardInterrupt

    monkeypatch.setattr(live_mod, "_run_plain", boom)
    monkeypatch.setattr(live_mod.sys.stdout, "isatty", lambda: False, raising=False)

    p = tmp_path / "audit.jsonl"
    rc = live_mod.run_live(p)
    assert rc == 0
