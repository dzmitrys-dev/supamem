"""Tests for the Claude Code edit-gate hook (B2).

Covers:
* Stdout-clean JSON contract for the PreToolUse `permissionDecision` payload
* Allow-paths: non-gated tools, missing transcript, env-disabled
* Deny-path: gated tool with no recent search

Recency-window strategy is currently a placeholder (returns True) — when the
recency strategy is locked in, add a deny-test that constructs a transcript
without a recent search and asserts `permissionDecision == "deny"`.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from supamem.hooks import gate_edit


def _run_with_payload(
    payload: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict, str]:
    """Drive gate_edit.run() with a fake stdin payload. Returns (exit, stdout_json, stderr)."""
    raw = json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    rc = gate_edit.run()
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip()) if captured.out.strip() else {}
    return rc, out, captured.err


def test_allow_when_tool_not_gated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _err = _run_with_payload(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}, monkeypatch, capsys
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "not gated" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_allow_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SUPAMEM_GATE_DISABLE", "1")
    rc, out, _err = _run_with_payload(
        {"tool_name": "Edit"}, monkeypatch, capsys
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "disabled" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_allow_when_no_transcript_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail-OPEN on malformed payloads — never block on bad hook input."""
    rc, out, _err = _run_with_payload(
        {"tool_name": "Edit"},  # no transcript_path
        monkeypatch,
        capsys,
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_deny_when_transcript_missing_on_disk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """When transcript file is referenced but doesn't exist, deny — no proof of search."""
    rc, out, _err = _run_with_payload(
        {
            "tool_name": "Edit",
            "transcript_path": str(tmp_path / "does-not-exist.jsonl"),
        },
        monkeypatch,
        capsys,
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "dual_memory_search" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_allow_when_transcript_exists_with_placeholder_strategy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Placeholder strategy returns True for any non-empty existing transcript.

    REPLACE THIS TEST when the recency-window strategy is locked in: it
    should construct a transcript without a recent search and assert deny,
    plus a separate test that includes a recent search and asserts allow.
    """
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    rc, out, _err = _run_with_payload(
        {"tool_name": "Edit", "transcript_path": str(transcript)},
        monkeypatch,
        capsys,
    )
    assert rc == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_stdout_is_single_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Code parses stdout as JSON — must be one valid object, no chatter."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"})))
    gate_edit.run()
    out_text = capsys.readouterr().out
    # Exactly one trailing newline, exactly one parseable JSON object.
    assert out_text.endswith("\n")
    assert out_text.count("\n") == 1
    json.loads(out_text)  # raises if not valid JSON


def test_dispatcher_routes_claude_code_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`supamem hook claude-code-gate` must dispatch into gate_edit.run."""
    from supamem.config import ResolvedConfig
    from supamem.hooks import dispatch

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash"})))
    rc = dispatch(client="claude-code-gate", file_path=None, config=ResolvedConfig())
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert "hookSpecificOutput" in out


def test_install_enforce_search_registers_gate_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install(enforce_search=True) must add a PreToolUse entry for the gate."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from supamem.install.claude_code import install

    install(enforce_search=True)
    settings = json.loads(
        (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    pre = json.dumps(settings.get("hooks", {}).get("PreToolUse", []))
    assert "supamem hook claude-code-gate" in pre
    # Default (injection) hook must still be registered too.
    assert "supamem hook claude-code " in pre or "supamem hook claude-code\"" in pre


def test_install_default_does_not_register_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default install (no --enforce-search) must NOT register the gate."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    from supamem.install.claude_code import install

    install()
    settings = json.loads(
        (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    pre = json.dumps(settings.get("hooks", {}).get("PreToolUse", []))
    assert "claude-code-gate" not in pre
