"""Tests for `supamem repair` — migration verb (legacy global → per-workspace)."""
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
    cwd = tmp_path_factory.mktemp("ws")
    monkeypatch.chdir(cwd)
    return cwd


def test_repair_strips_legacy_global_and_writes_project(home: Path, project: Path) -> None:
    """User on legacy global install → repair migrates them to project scope."""
    from supamem.install import repair

    # Simulate legacy install at user scope.
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "supamem": {"command": "supamem", "args": ["mcp-server"]},
                    "other": {"command": "x"},
                }
            }
        ),
        encoding="utf-8",
    )

    rc = repair("claude-code")
    assert rc == 0

    # Global supamem entry should be GONE; sibling preserved.
    user_raw = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert "supamem" not in user_raw.get("mcpServers", {})
    assert "other" in user_raw["mcpServers"]

    # Project-scope file should now EXIST with supamem.
    project_mcp = project / ".mcp.json"
    assert project_mcp.exists()
    proj_raw = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert "supamem" in proj_raw["mcpServers"]


def test_repair_idempotent_on_healthy_install(home: Path, project: Path) -> None:
    """Running repair on an already-healthy project install should not corrupt anything."""
    from supamem.install import install, repair

    install("claude-code")  # default: project scope
    project_mcp = project / ".mcp.json"
    before = project_mcp.read_text(encoding="utf-8")

    rc = repair("claude-code")
    assert rc == 0
    after = project_mcp.read_text(encoding="utf-8")

    # Content must round-trip identically (same supamem entry in both).
    assert json.loads(before)["mcpServers"]["supamem"] == json.loads(after)["mcpServers"]["supamem"]


def test_repair_cursor_strips_global_writes_project(home: Path, project: Path) -> None:
    from supamem.install import repair

    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"supamem": {"command": "supamem"}, "keep": {"command": "y"}}}
        ),
        encoding="utf-8",
    )

    rc = repair("cursor")
    assert rc == 0

    user_raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in user_raw.get("mcpServers", {})
    assert "keep" in user_raw["mcpServers"]
    project_raw = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" in project_raw["mcpServers"]


def test_repair_unknown_client_returns_error(home: Path, project: Path) -> None:
    from supamem.install import repair

    assert repair("not-a-real-client") == 2


def test_repair_no_targets_returns_error(home: Path, project: Path) -> None:
    """Auto-detect: nothing installed → error (nothing to do)."""
    from supamem.install import repair

    assert repair(None) == 2


def test_repair_autodetect_repairs_present_clients(home: Path, project: Path) -> None:
    """Auto-detect: repair every client with any signal of being installed."""
    from supamem.install import repair

    # Stage signals: legacy claude-code at user scope, cursor at project scope.
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"supamem": {"command": "supamem"}}}), encoding="utf-8"
    )
    (project / ".cursor").mkdir()
    (project / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"supamem": {"command": "supamem"}}}), encoding="utf-8"
    )

    rc = repair(None)
    assert rc == 0
    assert (project / ".mcp.json").exists()
    assert (project / ".cursor" / "mcp.json").exists()


def test_repair_with_enforce_search_registers_gate(home: Path, project: Path) -> None:
    """repair --enforce-search must wire the claude-code-gate hook."""
    from supamem.install import repair

    repair("claude-code", enforce_search=True)
    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = json.dumps(settings.get("hooks", {}).get("PreToolUse", []))
    assert "claude-code-gate" in pre
