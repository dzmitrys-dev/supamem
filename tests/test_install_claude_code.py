"""Tests for the Claude Code installer (Plan 80.6-10 Task 2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox HOME so installer writes never escape tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_install_creates_mcp_entry_in_claude_json(home: Path) -> None:
    from supamem.install.claude_code import install

    result = install()
    assert not result.no_op
    raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "mcpServers" in raw
    assert "supamem" in raw["mcpServers"]
    assert raw["mcpServers"]["supamem"]["command"] == "supamem"
    assert "mcp-server" in raw["mcpServers"]["supamem"]["args"]


def test_install_injects_project_root_when_run_in_repo(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When installed from a directory containing .supamem/config.toml, the
    MCP entry must carry SUPAMEM_PROJECT_ROOT in env so multi-project
    machines don't collapse to a single global value (mirrors cursor behavior)."""
    from supamem.install.claude_code import install

    workspace = tmp_path / "workspace"
    (workspace / ".supamem").mkdir(parents=True)
    (workspace / ".supamem" / "config.toml").write_text(
        "[supamem]\ncollection = 'project-a'\n", encoding="utf-8"
    )
    monkeypatch.chdir(workspace)

    install()
    raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    env = raw["mcpServers"]["supamem"]["env"]
    assert env["SUPAMEM_PROJECT_ROOT"] == str(workspace.resolve())
    assert env["DM_MCP_SOURCE"] == "mcp_claude_code"


def test_install_no_project_root_when_outside_repo(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When installed from a directory WITHOUT .supamem/config.toml, no
    SUPAMEM_PROJECT_ROOT should be written — the parent-walk fallback in
    cmd_mcp_server handles discovery, and a stale absolute path would
    actively mislead callers."""
    from supamem.install.claude_code import install

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    install()
    raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    env = raw["mcpServers"]["supamem"]["env"]
    assert "SUPAMEM_PROJECT_ROOT" not in env


def test_install_preserves_sibling_mcp_servers(home: Path) -> None:
    from supamem.install.claude_code import install

    pre = {"mcpServers": {"other": {"command": "other-bin", "args": ["x"]}}}
    (home / ".claude.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "other" in raw["mcpServers"]
    assert "supamem" in raw["mcpServers"]
    assert raw["mcpServers"]["other"]["command"] == "other-bin"


def test_install_adds_pretooluse_hook_to_settings(home: Path) -> None:
    from supamem.install.claude_code import install

    install()
    settings_path = home / ".claude" / "settings.json"
    assert settings_path.exists()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    pre_hooks = raw.get("hooks", {}).get("PreToolUse", [])
    flat = json.dumps(pre_hooks)
    assert "supamem hook claude-code" in flat


def test_install_appends_claude_md_import(home: Path) -> None:
    from supamem.install.claude_code import install

    install()
    claude_md = home / "CLAUDE.md"
    assert claude_md.exists()
    body = claude_md.read_text(encoding="utf-8")
    assert "@~/.supamem/share/rules/dual-memory.md" in body


def test_install_idempotent_second_run_no_op(home: Path) -> None:
    from supamem.install.claude_code import install

    first = install()
    second = install()
    assert first.no_op is False
    assert second.no_op is True


def test_install_dry_run_writes_nothing(home: Path) -> None:
    from supamem.install.claude_code import install

    result = install(dry_run=True)
    assert not (home / ".claude.json").exists()
    assert not (home / ".claude" / "settings.json").exists()
    assert not (home / "CLAUDE.md").exists()
    assert result.diff  # something would have changed


def test_install_creates_bak_files(home: Path) -> None:
    """Backups are created for files that already existed before install."""
    from supamem.install.claude_code import install

    pre = {"mcpServers": {"other": {"command": "x", "args": []}}}
    (home / ".claude.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    baks = list(home.glob(".claude.json.bak.*"))
    assert baks, "expected a .bak.<ts> sibling for the existing claude.json"


def test_uninstall_removes_only_managed_block(home: Path) -> None:
    from supamem.install.claude_code import install, uninstall

    # User pre-existing CLAUDE.md content the uninstaller must NOT touch.
    user_content = "# User notes\n\nSome notes here.\n"
    (home / "CLAUDE.md").write_text(user_content, encoding="utf-8")

    install()
    uninstall()

    body = (home / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Some notes here." in body
    assert "@~/.supamem/share/rules/dual-memory.md" not in body
    assert "BEGIN SUPAMEM" not in body
