"""Tests for the OpenCode installer (Plan 80.6-10 Task 3)."""
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
    cwd = tmp_path_factory.mktemp("project")
    monkeypatch.chdir(cwd)
    return cwd


def test_opencode_install_writes_mcp_entry(home: Path, project: Path) -> None:
    from supamem.install.opencode import install

    install()
    cfg = home / ".config" / "opencode" / "opencode.json"
    assert cfg.exists()
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    flat = json.dumps(raw)
    assert "supamem" in flat
    assert "mcp-server" in flat


def test_opencode_install_appends_agents_md_import(home: Path, project: Path) -> None:
    from supamem.install.opencode import install

    install()
    agents_md = project / "AGENTS.md"
    assert agents_md.exists()
    body = agents_md.read_text(encoding="utf-8")
    assert "@~/.supamem/share/rules/dual-memory.md" in body


def test_opencode_install_idempotent(home: Path, project: Path) -> None:
    from supamem.install.opencode import install

    first = install()
    second = install()
    assert first.no_op is False
    assert second.no_op is True


def test_opencode_install_dry_run(home: Path, project: Path) -> None:
    from supamem.install.opencode import install

    result = install(dry_run=True)
    assert not (home / ".config" / "opencode" / "opencode.json").exists()
    assert not (project / "AGENTS.md").exists()
    assert result.diff


def test_opencode_uninstall_removes_block(home: Path, project: Path) -> None:
    from supamem.install.opencode import install, uninstall

    install()
    uninstall()
    cfg_path = home / ".config" / "opencode" / "opencode.json"
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        flat = json.dumps(raw)
        assert "supamem" not in flat
    agents_md = project / "AGENTS.md"
    if agents_md.exists():
        body = agents_md.read_text(encoding="utf-8")
        assert "BEGIN SUPAMEM" not in body
