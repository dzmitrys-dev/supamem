"""Tests for ``supamem.hooks.claude_code`` (Plan 80.6-07 Task 1).

Locks the hook contract:
- is_code_target gate filters non-source paths
- derive_query strips configurable noise tokens
- run() emits one line of valid hookSpecificOutput JSON to stdout
- Marker file at /tmp/<slug>-queried-YYYYMMDD touched on success
- counter.bump invoked with (kind='search', source='hook_claude_code')
- Fail-soft: any exception → exit 0 + empty additionalContext
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.hooks.claude_code import derive_query, is_code_target, run


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "test_hook"}
    base.update(overrides)
    return ResolvedConfig(**base)


def test_is_code_target_accepts_src_paths() -> None:
    assert is_code_target(Path("src/foo.py")) is True
    assert is_code_target(Path("/abs/repo/src/foo.ts")) is True
    assert is_code_target(Path("tests/test_x.py")) is True


def test_is_code_target_rejects_md_in_src() -> None:
    assert is_code_target(Path("src/foo.md")) is False
    assert is_code_target(Path("src/foo.json")) is False
    assert is_code_target(Path("src/foo.lock")) is False


def test_is_code_target_rejects_outside_src() -> None:
    assert is_code_target(Path("docs/foo.py")) is False
    assert is_code_target(Path("README.md")) is False


def test_derive_query_strips_drop_tokens() -> None:
    out = derive_query(Path("src/the_chat_service.py"), drop_tokens=["the", "a"])
    assert "chat" in out
    assert "service" in out
    assert "the" not in out.split()


def test_derive_query_handles_generic_stem() -> None:
    """Generic stem like 'models' → fall back to parent + stem."""
    out = derive_query(Path("src/billing/models.py"), drop_tokens=[])
    assert "billing" in out


def test_run_emits_hookspecificoutput_on_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mock backend; capture stdout; assert valid JSON with hookSpecificOutput key."""
    import supamem.hooks.claude_code as mod
    from supamem.retrieval.types import RetrievedChunk

    fake_backend = MagicMock()
    fake_backend.query.return_value = [
        RetrievedChunk(id="1", text="alpha context", score=0.9, source_path="a.md"),
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)
    monkeypatch.setattr(mod, "_marker_dir", lambda: tmp_path)

    cap = io.StringIO()
    real = sys.stdout
    sys.stdout = cap
    try:
        rc = run(Path("src/foo.py"), _cfg())
    finally:
        sys.stdout = real

    assert rc == 0
    payload = json.loads(cap.getvalue().strip())
    assert "hookSpecificOutput" in payload
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "PreToolUse"
    assert "additionalContext" in inner
    assert "alpha context" in inner["additionalContext"]


def test_run_failsoft_on_qdrant_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Backend raises → exit 0, empty additionalContext."""
    import supamem.hooks.claude_code as mod

    fake_backend = MagicMock()
    fake_backend.query.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)
    monkeypatch.setattr(mod, "_marker_dir", lambda: tmp_path)

    cap = io.StringIO()
    real = sys.stdout
    sys.stdout = cap
    try:
        rc = run(Path("src/foo.py"), _cfg())
    finally:
        sys.stdout = real

    assert rc == 0
    payload = json.loads(cap.getvalue().strip() or "{}")
    inner = payload.get("hookSpecificOutput", {})
    assert inner.get("additionalContext", "") == ""


def test_run_touches_marker_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After run, /tmp/<slug>-queried-YYYYMMDD exists."""
    import supamem.hooks.claude_code as mod

    fake_backend = MagicMock()
    fake_backend.query.return_value = []
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)
    monkeypatch.setattr(mod, "_marker_dir", lambda: tmp_path)

    cap = io.StringIO()
    real = sys.stdout
    sys.stdout = cap
    try:
        run(Path("src/foo.py"), _cfg())
    finally:
        sys.stdout = real

    today = date.today().strftime("%Y%m%d")
    matches = list(tmp_path.glob(f"*-queried-{today}"))
    assert matches, f"no marker file in {tmp_path}: {list(tmp_path.iterdir())}"


def test_run_calls_counter_bump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """counter.bump invoked with kind='search', source='hook_claude_code'."""
    import supamem.hooks.claude_code as mod
    from supamem.retrieval.types import RetrievedChunk

    fake_backend = MagicMock()
    fake_backend.query.return_value = [
        RetrievedChunk(id="1", text="x", score=0.5, source_path="a.md"),
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)
    monkeypatch.setattr(mod, "_marker_dir", lambda: tmp_path)

    bump_calls: list[dict[str, Any]] = []

    def fake_bump(kind: str, source: str, tokens: int, latency_ms: float, **kw: Any) -> None:
        bump_calls.append({"kind": kind, "source": source, "tokens": tokens, "latency_ms": latency_ms})

    monkeypatch.setattr(mod, "_bump", fake_bump)

    cap = io.StringIO()
    real = sys.stdout
    sys.stdout = cap
    try:
        run(Path("src/foo.py"), _cfg(cache_dir=str(tmp_path)))
    finally:
        sys.stdout = real

    assert bump_calls, "expected counter.bump to be invoked"
    assert bump_calls[0]["kind"] == "search"
    assert bump_calls[0]["source"] == "hook_claude_code"
