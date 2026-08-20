"""Tests for the OpenCode installer (Plan 80.6-10 Task 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supamem.config_io import wrap_managed_block

IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"


def _duplicated_agents_md() -> str:
    """SM-4 field-report replica: two fenced blocks (v0.2.0 + v0.3.0a7)."""
    old = wrap_managed_block(IMPORT_LINE, version="0.2.0")
    new = wrap_managed_block(IMPORT_LINE, version="0.3.0a7")
    return f"# Project notes\n{old}\nuser middle\n{new}\ntrailing\n"


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


def test_opencode_install_heals_duplicated_agents_md(
    home: Path, project: Path
) -> None:
    """SM-6a-opencode: duplicated AGENTS.md (two blocks) — install completes
    without ValueError and leaves exactly one merged block at the current
    version, user text preserved."""
    from supamem import __version__
    from supamem.install.opencode import install

    (project / "AGENTS.md").write_text(_duplicated_agents_md(), encoding="utf-8")

    install()  # must NOT raise ValueError

    body = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert body.count("BEGIN SUPAMEM") == 1
    assert f"BEGIN SUPAMEM v{__version__} MANAGED BLOCK" in body
    assert body.count(IMPORT_LINE) == 1
    assert "# Project notes" in body
    assert "user middle" in body
    assert "trailing" in body


def test_opencode_uninstall_heals_duplicated_agents_md(
    home: Path, project: Path
) -> None:
    """SM-6a-opencode: duplicated AGENTS.md — uninstall completes without
    ValueError, leaves ZERO managed blocks, preserves user content, and
    writes a .bak.<time_ns> sibling before the sweep rewrite."""
    from supamem.install.opencode import uninstall

    duplicated = _duplicated_agents_md()
    (project / "AGENTS.md").write_text(duplicated, encoding="utf-8")

    uninstall()  # must NOT raise ValueError

    body = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "BEGIN SUPAMEM" not in body
    assert IMPORT_LINE not in body
    assert "# Project notes" in body
    assert "user middle" in body
    assert "trailing" in body
    baks = list(project.glob("AGENTS.md.bak.*"))
    assert baks, "expected a .bak.<time_ns> sibling for the sweep rewrite"
    assert any(bak.read_text(encoding="utf-8") == duplicated for bak in baks)


def test_opencode_install_healthy_file_is_byte_identical_noop(
    home: Path, project: Path
) -> None:
    """SM-4b: a file with exactly one current-version block — running the
    install path leaves AGENTS.md byte-identical and creates no .bak."""
    from supamem import __version__
    from supamem.config_io import wrap_managed_block as wrap
    from supamem.install.opencode import install

    healthy = (
        "# Project notes\n"
        + wrap(IMPORT_LINE, version=__version__)
        + "\nsome trailing user text\n"
    )
    target = project / "AGENTS.md"
    target.write_text(healthy, encoding="utf-8")

    install()

    assert target.read_text(encoding="utf-8") == healthy
    assert not list(project.glob("AGENTS.md.bak.*"))
