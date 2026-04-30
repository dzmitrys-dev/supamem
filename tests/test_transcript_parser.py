"""Tests for the Claude Code JSONL parser (Plan 06-01 Task 1 — RED).

Behaviors locked to Phase 06 RESEARCH §Code Examples §1 + §Pitfalls 1-3:
strict envelope schema, fail-loud on mid-file invalid JSON (D-28 / INGEST-05),
warn-and-continue on trailing partial line (D-28 carve-out for SIGKILL
truncation), warn-and-skip on unknown event types in non-last positions
(D-29 forward-compat carve-out).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supamem.indexer.transcript.parser import (
    KNOWN_TYPES,
    parse_jsonl,
    parse_jsonl_text,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def test_parse_simple_session_yields_user_assistant_events() -> None:
    """3-turn fixture yields 6 SessionEvent dicts; uuids stable; sessionId consistent."""
    events = list(parse_jsonl(FIXTURES / "simple_session.jsonl"))
    assert len(events) == 6
    types = [e["type"] for e in events]
    assert types == ["user", "assistant", "user", "assistant", "user", "assistant"]
    uuids = [e["uuid"] for e in events]
    assert uuids == ["u1", "a1", "u2", "a2", "u3", "a3"]
    session_ids = {e["sessionId"] for e in events}
    assert session_ids == {"s1"}
    for e in events:
        assert e["type"] in KNOWN_TYPES


def test_trailing_partial_warns_and_continues(capsys: pytest.CaptureFixture[str]) -> None:
    """D-28: last-line truncation logs warning to err_console; prior events yielded."""
    events = list(parse_jsonl(FIXTURES / "truncated_session.jsonl"))
    assert len(events) == 2
    assert [e["uuid"] for e in events] == ["u1", "a1"]
    captured = capsys.readouterr()
    assert "trailing partial line" in captured.err


def test_midfile_json_error_fails(tmp_path: Path) -> None:
    """D-28 + INGEST-05: mid-file invalid JSON raises JSONDecodeError when iterated."""
    bad = tmp_path / "midfile_bad.jsonl"
    bad.write_text(
        '{"type":"user","uuid":"u1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"user","content":"hi"}}\n'
        '{not valid json at all}\n'
        '{"type":"assistant","uuid":"a1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"assistant","content":[{"type":"text","text":"x"}]}}\n',
        encoding="utf-8",
    )
    with pytest.raises(json.JSONDecodeError):
        list(parse_jsonl(bad))


def test_unknown_type_warn_skip(capsys: pytest.CaptureFixture[str]) -> None:
    """D-29: unknown event type mid-file warns and skips, yields surrounding events."""
    events = list(parse_jsonl(FIXTURES / "unknown_event_session.jsonl"))
    assert len(events) == 2
    assert [e["type"] for e in events] == ["user", "assistant"]
    captured = capsys.readouterr()
    assert "unknown event type" in captured.err


def test_skips_blank_lines(tmp_path: Path) -> None:
    """Empty / whitespace-only lines silently skipped (no warning, no error)."""
    p = tmp_path / "blanks.jsonl"
    p.write_text(
        '{"type":"user","uuid":"u1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"user","content":"hi"}}\n'
        '\n'
        '   \n'
        '\t\n'
        '{"type":"assistant","uuid":"a1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"assistant","content":[{"type":"text","text":"x"}]}}\n',
        encoding="utf-8",
    )
    events = list(parse_jsonl(p))
    assert len(events) == 2


def test_non_object_line_raises(tmp_path: Path) -> None:
    """A line containing a non-dict JSON value (e.g. an array) raises ValueError."""
    p = tmp_path / "non_object.jsonl"
    p.write_text(
        '{"type":"user","uuid":"u1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"user","content":"hi"}}\n'
        '[1,2,3]\n'
        '{"type":"assistant","uuid":"a1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"assistant","content":[{"type":"text","text":"x"}]}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a JSON object"):
        list(parse_jsonl(p))


def test_parse_jsonl_text_basic() -> None:
    """B4: in-memory variant produces identical SessionEvent stream as path variant."""
    text = (FIXTURES / "simple_session.jsonl").read_text(encoding="utf-8")
    text_events = list(parse_jsonl_text(text))
    file_events = list(parse_jsonl(FIXTURES / "simple_session.jsonl"))
    assert text_events == file_events
    assert len(text_events) == 6
    assert text_events[0]["type"] == "user"
    assert text_events[0]["uuid"] == "u1"


def test_known_types_set_locked() -> None:
    """KNOWN_TYPES set per RESEARCH R-01 — line-level event type envelope."""
    assert KNOWN_TYPES == {
        "user",
        "assistant",
        "system",
        "summary",
        "file-history-snapshot",
        "queue-operation",
    }
