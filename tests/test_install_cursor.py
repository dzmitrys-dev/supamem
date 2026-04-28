"""Tests for the Cursor installer (Plan 80.6-10 Task 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A separate cwd for project-scoped Cursor files (.cursor/rules, .cursor/hooks.json)."""
    cwd = tmp_path_factory.mktemp("project")
    monkeypatch.chdir(cwd)
    return cwd


def test_cursor_install_writes_mcp_json(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    install()
    raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" in raw["mcpServers"]


def test_cursor_install_preserves_sibling_servers(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    pre = {"mcpServers": {"other": {"command": "x"}}}
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "other" in raw["mcpServers"]
    assert "supamem" in raw["mcpServers"]


def test_cursor_install_copies_mdc_to_local_cursor_rules(home: Path, project: Path) -> None:
    """SC-3 documented exception: cursor .mdc is COPIED, not referenced."""
    from supamem.install.cursor import install

    install()
    target = project / ".cursor" / "rules" / "dual-memory.mdc"
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "dual" in body.lower()


def test_cursor_install_adds_session_start_snapshot(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    install()
    hooks_path = project / ".cursor" / "hooks.json"
    assert hooks_path.exists()
    raw = json.loads(hooks_path.read_text(encoding="utf-8"))
    flat = json.dumps(raw)
    assert "supamem" in flat
    assert "snapshot" in flat


def test_cursor_install_idempotent(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    first = install()
    second = install()
    assert first.no_op is False
    assert second.no_op is True


def test_cursor_install_dry_run(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    result = install(dry_run=True)
    assert not (home / ".cursor" / "mcp.json").exists()
    assert not (project / ".cursor" / "rules" / "dual-memory.mdc").exists()
    assert result.diff


def test_cursor_uninstall_removes_block(home: Path, project: Path) -> None:
    from supamem.install.cursor import install, uninstall

    pre = {"mcpServers": {"other": {"command": "x"}}}
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    uninstall()
    raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in raw["mcpServers"]
    assert "other" in raw["mcpServers"]
