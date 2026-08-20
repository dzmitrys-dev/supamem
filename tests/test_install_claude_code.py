"""Tests for the Claude Code installer (Plan 80.6-10 Task 2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supamem.config_io import wrap_managed_block

IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"


def _duplicated_claude_md() -> str:
    """SM-4 field-report replica: two fenced blocks (v0.2.0 + v0.3.0a7)."""
    old = wrap_managed_block(IMPORT_LINE, version="0.2.0")
    new = wrap_managed_block(IMPORT_LINE, version="0.3.0a7")
    return f"# User notes\n{old}\nuser middle\n{new}\ntrailing\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sandbox HOME so installer writes never escape tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Separate cwd so project-scope writes don't collide with HOME-sandbox tmp_path."""
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    return cwd


def test_install_writes_project_mcp_json_by_default(home: Path, project: Path) -> None:
    """Default scope='project' → write to <cwd>/.mcp.json (canonical Claude Code per-project path)."""
    from supamem.install.claude_code import install

    result = install()
    assert not result.no_op
    project_mcp = project / ".mcp.json"
    assert project_mcp.exists()
    raw = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert "mcpServers" in raw
    assert "supamem" in raw["mcpServers"]
    assert raw["mcpServers"]["supamem"]["command"] == "supamem"
    assert "mcp-server" in raw["mcpServers"]["supamem"]["args"]
    # Global file must NOT be touched.
    assert not (home / ".claude.json").exists()


def test_install_user_scope_writes_global(home: Path, project: Path) -> None:
    """scope='user' preserves legacy global behavior."""
    from supamem.install.claude_code import install

    install(scope="user")
    raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "supamem" in raw["mcpServers"]
    # Project-scope file must NOT exist on user-scope install.
    assert not (project / ".mcp.json").exists()


def test_install_unknown_scope_raises(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    with pytest.raises(ValueError, match="unknown scope"):
        install(scope="bogus")


def test_install_injects_project_root_when_run_in_repo(
    home: Path, project: Path
) -> None:
    """When installed from a directory containing .supamem/config.toml, the
    MCP entry carries SUPAMEM_PROJECT_ROOT for hosts that ignore cwd."""
    from supamem.install.claude_code import install

    (project / ".supamem").mkdir()
    (project / ".supamem" / "config.toml").write_text(
        "[supamem]\ncollection = 'project-a'\n", encoding="utf-8"
    )

    install()
    raw = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    env = raw["mcpServers"]["supamem"]["env"]
    assert env["SUPAMEM_PROJECT_ROOT"] == str(project.resolve())
    assert env["DM_MCP_SOURCE"] == "mcp_claude_code"


def test_install_no_project_root_when_outside_repo(home: Path, project: Path) -> None:
    """When installed from a directory WITHOUT .supamem/config.toml, no
    SUPAMEM_PROJECT_ROOT is written — parent-walk fallback handles discovery,
    a stale absolute path would actively mislead callers."""
    from supamem.install.claude_code import install

    install()
    raw = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    env = raw["mcpServers"]["supamem"]["env"]
    assert "SUPAMEM_PROJECT_ROOT" not in env


def test_install_preserves_sibling_mcp_servers(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    pre = {"mcpServers": {"other": {"command": "other-bin", "args": ["x"]}}}
    (project / ".mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    raw = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert "other" in raw["mcpServers"]
    assert "supamem" in raw["mcpServers"]
    assert raw["mcpServers"]["other"]["command"] == "other-bin"


def test_install_adds_pretooluse_hook_to_settings(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    install()
    settings_path = home / ".claude" / "settings.json"
    assert settings_path.exists()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    pre_hooks = raw.get("hooks", {}).get("PreToolUse", [])
    flat = json.dumps(pre_hooks)
    assert "supamem hook claude-code" in flat


def test_install_appends_claude_md_import(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    install()
    claude_md = home / "CLAUDE.md"
    assert claude_md.exists()
    body = claude_md.read_text(encoding="utf-8")
    assert "@~/.supamem/share/rules/dual-memory.md" in body


def test_install_idempotent_second_run_no_op(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    first = install()
    second = install()
    assert first.no_op is False
    assert second.no_op is True


def test_install_dry_run_writes_nothing(home: Path, project: Path) -> None:
    from supamem.install.claude_code import install

    result = install(dry_run=True)
    assert not (project / ".mcp.json").exists()
    assert not (home / ".claude" / "settings.json").exists()
    assert not (home / "CLAUDE.md").exists()
    assert result.diff  # something would have changed


def test_install_creates_bak_files(home: Path, project: Path) -> None:
    """Backups are created for files that already existed before install."""
    from supamem.install.claude_code import install

    pre = {"mcpServers": {"other": {"command": "x", "args": []}}}
    (project / ".mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    baks = list(project.glob(".mcp.json.bak.*"))
    assert baks, "expected a .bak.<ts> sibling for the existing .mcp.json"


def test_uninstall_strips_both_scopes(home: Path, project: Path) -> None:
    """Uninstall must strip supamem from BOTH project and user scopes."""
    from supamem.install.claude_code import install, uninstall

    # Pre-existing supamem at user scope (e.g. legacy install) and a sibling.
    (home / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"other": {"command": "y"}, "supamem": {"command": "stale"}}}
        ),
        encoding="utf-8",
    )

    install()  # default: project scope
    uninstall()

    project_raw = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in project_raw.get("mcpServers", {})
    user_raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "supamem" not in user_raw.get("mcpServers", {})
    assert "other" in user_raw["mcpServers"]


def test_uninstall_removes_only_managed_block(home: Path, project: Path) -> None:
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


def test_install_heals_duplicated_managed_blocks(home: Path, project: Path) -> None:
    """SM-6a: field-report replica (two blocks in ~/CLAUDE.md) — install must
    complete without ValueError and leave exactly ONE merged block at the
    current version, user text preserved."""
    from supamem import __version__
    from supamem.install.claude_code import install

    (home / "CLAUDE.md").write_text(_duplicated_claude_md(), encoding="utf-8")

    install()  # must NOT raise ValueError

    body = (home / "CLAUDE.md").read_text(encoding="utf-8")
    assert body.count("BEGIN SUPAMEM") == 1
    assert f"BEGIN SUPAMEM v{__version__} MANAGED BLOCK" in body
    assert body.count(IMPORT_LINE) == 1
    assert "# User notes" in body
    assert "user middle" in body
    assert "trailing" in body


def test_uninstall_heals_duplicated_managed_blocks(home: Path, project: Path) -> None:
    """SM-6a: duplicated ~/CLAUDE.md — uninstall must complete without
    ValueError and leave ZERO managed blocks, user content preserved."""
    from supamem.install.claude_code import uninstall

    (home / "CLAUDE.md").write_text(_duplicated_claude_md(), encoding="utf-8")

    uninstall()  # must NOT raise ValueError

    body = (home / "CLAUDE.md").read_text(encoding="utf-8")
    assert "BEGIN SUPAMEM" not in body
    assert IMPORT_LINE not in body
    assert "# User notes" in body
    assert "user middle" in body
    assert "trailing" in body


def test_sweep_rewrite_creates_bak_sibling(home: Path, project: Path) -> None:
    """Any sweep-induced rewrite of ~/CLAUDE.md leaves a .bak.<time_ns>
    sibling containing the pre-rewrite (duplicated) content."""
    from supamem.install.claude_code import uninstall

    duplicated = _duplicated_claude_md()
    (home / "CLAUDE.md").write_text(duplicated, encoding="utf-8")

    uninstall()

    baks = list(home.glob("CLAUDE.md.bak.*"))
    assert baks, "expected a .bak.<time_ns> sibling for the sweep rewrite"
    assert any(bak.read_text(encoding="utf-8") == duplicated for bak in baks)
