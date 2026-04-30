"""Tests for MCP project-root resolution (quick-task 260430-wfp).

Covers:
* ``find_project_root`` parent-walk over ``.supamem/config.toml`` and
  ``pyproject.toml [tool.supamem]`` markers.
* Stop conditions (filesystem root, ``$HOME``).
* Stderr fallthrough warning emitted by ``cmd_mcp_server`` when no project
  config is discoverable and the env var is unset.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from supamem.cli import app
from supamem.config import find_project_root


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _clear_supamem_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "SUPAMEM_PROJECT_ROOT",
        "SUPAMEM_CONFIG",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "COLLECTION_NAME",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)


# ── find_project_root ────────────────────────────────────────────────────────


def test_find_root_via_supamem_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path / "repo" / ".supamem" / "config.toml", "[supamem]\ncollection = 'x'\n")
    sub = tmp_path / "repo" / "src" / "deep"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == (tmp_path / "repo").resolve()


def test_find_root_via_pyproject_tool_supamem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(
        tmp_path / "repo" / "pyproject.toml",
        '[tool.supamem]\ncollection = "x"\n',
    )
    sub = tmp_path / "repo" / "pkg"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == (tmp_path / "repo").resolve()


def test_find_root_ignores_pyproject_without_tool_supamem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path / "repo" / "pyproject.toml", '[project]\nname = "other"\n')
    sub = tmp_path / "repo" / "pkg"
    sub.mkdir(parents=True)
    assert find_project_root(sub) is None


def test_find_root_returns_none_when_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sub = tmp_path / "empty"
    sub.mkdir()
    assert find_project_root(sub) is None


def test_find_root_does_not_walk_above_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marker outside $HOME must NOT be picked up."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Marker is above $HOME — should be ignored.
    _write(tmp_path / ".supamem" / "config.toml", "[supamem]\n")
    sub = fake_home / "workspace"
    sub.mkdir()
    assert find_project_root(sub) is None


def test_find_root_handles_malformed_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed pyproject.toml should be skipped, not crash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path / "repo" / "pyproject.toml", "this is not valid toml @#$")
    sub = tmp_path / "repo" / "pkg"
    sub.mkdir(parents=True)
    # No marker found → None (not an exception).
    assert find_project_root(sub) is None


# ── cmd_mcp_server stderr warning ────────────────────────────────────────────


def test_mcp_server_warns_on_default_fallthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env var, no project config in cwd or ancestors → stderr warning."""
    _clear_supamem_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Stub run_stdio so we don't actually start the MCP loop.
    import supamem.cli as cli_mod

    called: dict[str, object] = {}

    def fake_run_stdio(cfg: object) -> None:
        called["cfg"] = cfg

    monkeypatch.setattr("supamem.mcp_server.run_stdio", fake_run_stdio)
    monkeypatch.setattr(cli_mod, "err_console", cli_mod.err_console)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp-server", "--transport", "stdio"])
    assert result.exit_code == 0, result.output
    # Stdout MUST stay JSON-RPC clean.
    assert result.stdout == ""
    # Stderr surfaces the warning.
    assert "default collection" in result.stderr
    assert "SUPAMEM_PROJECT_ROOT" in result.stderr
    # And the stub got the default collection (proves the fallthrough).
    cfg = called["cfg"]
    assert getattr(cfg, "collection") == "dev_memory_tuned_hybrid"


def test_mcp_server_silent_when_project_root_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SUPAMEM_PROJECT_ROOT set → no fallthrough warning, even if collection is the default."""
    _clear_supamem_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUPAMEM_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("supamem.mcp_server.run_stdio", lambda cfg: None)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp-server", "--transport", "stdio"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "default collection" not in result.stderr


def test_mcp_server_silent_when_parent_walk_finds_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent-walk discovery suppresses the warning even without env var."""
    _clear_supamem_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(tmp_path / "repo" / ".supamem" / "config.toml", "[supamem]\ncollection = 'custom'\n")
    sub = tmp_path / "repo" / "deep"
    sub.mkdir()
    monkeypatch.chdir(sub)

    captured: dict[str, object] = {}

    def fake_run_stdio(cfg: object) -> None:
        captured["cfg"] = cfg

    monkeypatch.setattr("supamem.mcp_server.run_stdio", fake_run_stdio)

    runner = CliRunner()
    result = runner.invoke(app, ["mcp-server", "--transport", "stdio"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "default collection" not in result.stderr
    cfg = captured["cfg"]
    # Parent-walk found .supamem/config.toml → custom collection.
    assert getattr(cfg, "collection") == "custom"
