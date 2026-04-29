"""Tests for supamem.hooks.session_start (v0.1.4+)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from supamem.config import ResolvedConfig
from supamem.hooks.session_start import (
    MAX_BANNER_CHARS,
    _detect_client,
    _emit_payload,
    build_banner,
    run,
)


# ── build_banner ────────────────────────────────────────────────────────────


def test_build_banner_starts_with_emoji_and_version() -> None:
    cfg = ResolvedConfig(collection="proj-coll")
    with patch(
        "supamem.hooks.session_start._probe_collection",
        return_value=("proj-coll", 0),
    ):
        banner = build_banner(cfg)
    assert banner.startswith("🧠 supamem v")
    assert "proj-coll" in banner
    assert "audit" in banner


def test_build_banner_with_qdrant_unreachable_marks_so() -> None:
    cfg = ResolvedConfig(collection="proj-coll")
    with patch(
        "supamem.hooks.session_start._probe_collection",
        return_value=("proj-coll", None),
    ):
        banner = build_banner(cfg)
    assert "qdrant unreachable" in banner


def test_build_banner_truncates_oversize() -> None:
    cfg = ResolvedConfig(collection="x" * 500)
    with patch(
        "supamem.hooks.session_start._probe_collection",
        return_value=("x" * 500, 1),
    ):
        banner = build_banner(cfg)
    assert len(banner) <= MAX_BANNER_CHARS
    assert banner.endswith("…")


def test_build_banner_with_no_collection_omits_chunk_count() -> None:
    cfg = ResolvedConfig(collection="")
    with patch(
        "supamem.hooks.session_start._probe_collection",
        return_value=(None, None),
    ):
        banner = build_banner(cfg)
    assert "🧠 supamem v" in banner
    assert "chunks" not in banner


# ── _detect_client (env sniff) ──────────────────────────────────────────────


def test_detect_client_claude_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("CURSOR", raising=False)
    assert _detect_client() == "claude-code"


def test_detect_client_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("OPENCODE", "1")
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("CURSOR", raising=False)
    assert _detect_client() == "opencode"


def test_detect_client_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.setenv("CURSOR_AGENT", "1")
    assert _detect_client() == "cursor"


def test_detect_client_default_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("CLAUDECODE", "OPENCODE", "CURSOR_AGENT", "CURSOR"):
        monkeypatch.delenv(var, raising=False)
    assert _detect_client() == "claude-code"


# ── _emit_payload (dual-format JSON) ────────────────────────────────────────


def test_emit_payload_has_both_camelcase_and_snakecase_keys() -> None:
    payload = _emit_payload("hello banner")
    # Camel for Claude Code
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert payload["hookSpecificOutput"]["additionalContext"] == "hello banner"
    # Snake for Cursor / OpenCode forks
    assert payload["additional_context"] == "hello banner"


# ── run (end-to-end) ────────────────────────────────────────────────────────


def test_run_writes_json_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = ResolvedConfig(collection="proj-coll")
    with patch(
        "supamem.hooks.session_start._probe_collection",
        return_value=("proj-coll", 5),
    ):
        rc = run(client="claude-code", config=cfg)
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "🧠 supamem" in parsed["hookSpecificOutput"]["additionalContext"]
    assert "proj-coll" in parsed["additional_context"]


def test_run_never_raises_on_internal_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hook must NEVER block session start, even if every internal call explodes."""
    with patch(
        "supamem.hooks.session_start.build_banner",
        side_effect=RuntimeError("kaboom"),
    ):
        rc = run(client="claude-code", config=ResolvedConfig())
    assert rc == 0  # fail-soft per hook discipline
