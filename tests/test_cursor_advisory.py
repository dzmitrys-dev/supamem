"""Tests for the Cursor beforeSubmitPrompt advisory hook (B3).

The Cursor hook surface (1.7+) cannot fail-closed before edits — the closest
event is ``beforeSubmitPrompt``, where we can inject an ``agentMessage``
nudge but cannot block. These tests cover:

* JSON output contract (Cursor parses stdout as JSON)
* Edit-bound prompt detection heuristic
* Skip path for read-only / question-style prompts
* Env-disabled override
* Installer wiring (advisory entry registered, idempotent, strippable)
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from supamem.hooks import cursor_advisory


def _run_with_payload(
    payload: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = cursor_advisory.run()
    assert rc == 0
    captured = capsys.readouterr()
    return json.loads(captured.out.strip())


def test_advisory_fires_on_edit_intent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run_with_payload(
        {"prompt": "fix the bug in auth.py"}, monkeypatch, capsys
    )
    assert out["continue"] is True
    assert out["permission"] == "allow"
    assert "supamem advisory" in out["agentMessage"].lower()
    assert "dual_memory_search" in out["agentMessage"]


def test_no_advisory_on_question_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read-only / question-style prompts should NOT trigger the nudge."""
    out = _run_with_payload(
        {"prompt": "What does this function return?"}, monkeypatch, capsys
    )
    assert out["continue"] is True
    assert "agentMessage" not in out


def test_no_advisory_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SUPAMEM_ADVISORY_DISABLE", "1")
    out = _run_with_payload(
        {"prompt": "refactor the auth module"}, monkeypatch, capsys
    )
    assert "agentMessage" not in out


def test_handles_missing_prompt_field(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run_with_payload({}, monkeypatch, capsys)
    assert out["continue"] is True
    assert "agentMessage" not in out


def test_handles_malformed_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json {"))
    rc = cursor_advisory.run()
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True


def test_stdout_is_single_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "implement auth"})))
    cursor_advisory.run()
    out_text = capsys.readouterr().out
    assert out_text.endswith("\n")
    assert out_text.count("\n") == 1
    json.loads(out_text)


def test_dispatcher_routes_cursor_advisory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from supamem.config import ResolvedConfig
    from supamem.hooks import dispatch

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "add feature x"})))
    rc = dispatch(client="cursor-advisory", file_path=None, config=ResolvedConfig())
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert "agentMessage" in out


# ── Installer wiring ────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path_factory.mktemp("ws")
    monkeypatch.chdir(cwd)
    return cwd


def test_install_registers_advisory_hook(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    install()
    hooks = json.loads((project / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    bsp = json.dumps(hooks.get("beforeSubmitPrompt", []))
    assert "cursor-advisory" in bsp


def test_install_advisory_idempotent(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    first = install()
    second = install()
    assert first.no_op is False
    assert second.no_op is True
    hooks = json.loads((project / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    # Exactly one advisory entry, not duplicated.
    bsp = hooks.get("beforeSubmitPrompt", [])
    advisory_entries = [
        e for e in bsp
        if "cursor-advisory" in (
            " ".join(e.get("command", [])) if isinstance(e.get("command"), list)
            else str(e.get("command", ""))
        )
    ]
    assert len(advisory_entries) == 1


def test_uninstall_strips_advisory(home: Path, project: Path) -> None:
    from supamem.install.cursor import install, uninstall

    # Pre-existing user beforeSubmitPrompt entry must be preserved.
    (project / ".cursor").mkdir()
    (project / ".cursor" / "hooks.json").write_text(
        json.dumps({"beforeSubmitPrompt": [{"command": ["echo", "user-hook"]}]}),
        encoding="utf-8",
    )

    install()
    uninstall()
    hooks = json.loads((project / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    bsp = hooks.get("beforeSubmitPrompt", [])
    flat = json.dumps(bsp)
    assert "cursor-advisory" not in flat
    assert "user-hook" in flat
