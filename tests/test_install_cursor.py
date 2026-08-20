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


def test_cursor_install_writes_mcp_json_project_scope_default(
    home: Path, project: Path
) -> None:
    """Default scope is 'project' → write to <cwd>/.cursor/mcp.json (per-workspace)."""
    from supamem.install.cursor import install

    install()
    project_mcp = project / ".cursor" / "mcp.json"
    assert project_mcp.exists()
    raw = json.loads(project_mcp.read_text(encoding="utf-8"))
    assert "supamem" in raw["mcpServers"]
    # Global file must NOT be touched on project-scope install.
    assert not (home / ".cursor" / "mcp.json").exists()


def test_cursor_install_user_scope_writes_global(home: Path, project: Path) -> None:
    """scope='user' preserves legacy behavior (write to ~/.cursor/mcp.json)."""
    from supamem.install.cursor import install

    install(scope="user")
    raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" in raw["mcpServers"]
    # Project-scoped MCP file must NOT exist on user-scope install.
    assert not (project / ".cursor" / "mcp.json").exists()


def test_cursor_install_unknown_scope_raises(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    with pytest.raises(ValueError, match="unknown scope"):
        install(scope="bogus")


def test_cursor_install_preserves_sibling_servers(home: Path, project: Path) -> None:
    from supamem.install.cursor import install

    pre = {"mcpServers": {"other": {"command": "x"}}}
    (project / ".cursor").mkdir()
    (project / ".cursor" / "mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    install()
    raw = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
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
    assert not (project / ".cursor" / "mcp.json").exists()
    assert not (project / ".cursor" / "rules" / "dual-memory.mdc").exists()
    assert result.diff


def test_cursor_uninstall_strips_both_scopes(home: Path, project: Path) -> None:
    """Uninstall must strip supamem from BOTH project and user scopes —
    user may have installed under either or both at different times."""
    from supamem.install.cursor import install, uninstall

    # Pre-existing sibling at project scope.
    (project / ".cursor").mkdir()
    (project / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8"
    )
    # Pre-existing sibling at user scope.
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"other-user": {"command": "y"}, "supamem": {"command": "stale"}}}),
        encoding="utf-8",
    )

    install()  # project scope (default)
    uninstall()
    project_raw = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in project_raw.get("mcpServers", {})
    assert "other" in project_raw["mcpServers"]
    user_raw = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in user_raw.get("mcpServers", {})
    assert "other-user" in user_raw["mcpServers"]


def test_cursor_uninstall_dry_run_changes_nothing(home: Path, project: Path) -> None:
    """SM-7a: fully-installed cursor fixture → uninstall(dry_run=True) leaves
    every target byte-identical — including the .mdc (no unlink under dry-run)
    and the user-scope mcp.json the strip loop must not touch."""
    from supamem.install.cursor import install, uninstall

    install()  # project scope: mcp.json + hooks.json + rules/dual-memory.mdc

    # Seed a legacy user-scope supamem entry so the strip loop has real work
    # it must NOT perform under dry-run.
    (home / ".cursor").mkdir(exist_ok=True)
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"other-user": {"command": "y"}, "supamem": {"command": "stale"}}}
        ),
        encoding="utf-8",
    )

    targets = [
        project / ".cursor" / "mcp.json",
        project / ".cursor" / "hooks.json",
        project / ".cursor" / "rules" / "dual-memory.mdc",
        home / ".cursor" / "mcp.json",
    ]
    before = {str(p): p.read_bytes() for p in targets}

    uninstall(dry_run=True)

    for p in targets:
        assert p.read_bytes() == before[str(p)], f"dry-run uninstall modified {p}"
    # The .mdc must NOT be unlinked under dry-run.
    assert (project / ".cursor" / "rules" / "dual-memory.mdc").exists()
    assert not list((project / ".cursor").rglob("*.bak.*"))
    assert not list((project / ".cursor").rglob("*.tmp.*"))
    assert not list((home / ".cursor").glob("*.bak.*"))


def test_cursor_uninstall_real_still_strips_after_dry_run(
    home: Path, project: Path
) -> None:
    """SM-7a Test 5: real uninstall still strips both scopes after a dry-run
    pass on the same fixture."""
    from supamem.install.cursor import install, uninstall

    install()
    uninstall(dry_run=True)
    uninstall()  # real

    project_raw = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "supamem" not in project_raw.get("mcpServers", {})
    assert not (project / ".cursor" / "rules" / "dual-memory.mdc").exists()


# ---------------------------------------------------------------------------
# SM-8: robust MCP entry (which-resolved command + SUPAMEM_CONFIG pin)
# ---------------------------------------------------------------------------


def test_cursor_repair_round_trip_keeps_robust_stanza(home: Path, project: Path) -> None:
    """SM-8a: a repair round-trip (uninstall + install) must never regress a
    robust stanza to the bare-name/discovery-dependent form — the rebuilt
    command is which-resolved absolute and the SUPAMEM_CONFIG pin survives."""
    import shutil as _shutil

    from supamem.install.cursor import install, uninstall

    (project / ".supamem").mkdir()
    cfg = project / ".supamem" / "config.toml"
    cfg.write_text("[supamem]\ncollection = 'round-trip'\n", encoding="utf-8")

    # Field-report shape: explicit absolute command + config pin.
    robust_cmd = _shutil.which("supamem") or "/opt/supamem/bin/supamem"
    pre = {
        "mcpServers": {
            "supamem": {
                "command": robust_cmd,
                "args": ["mcp-server", "--transport", "stdio"],
                "env": {
                    "DM_MCP_SOURCE": "mcp_cursor",
                    "SUPAMEM_PROJECT_ROOT": str(project.resolve()),
                    "SUPAMEM_CONFIG": str(cfg.resolve()),
                },
            }
        }
    }
    (project / ".cursor").mkdir()
    (project / ".cursor" / "mcp.json").write_text(json.dumps(pre), encoding="utf-8")

    uninstall()
    install()  # rebuild from the template

    raw = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    stanza = raw["mcpServers"]["supamem"]
    # which-equivalence — never a hardcoded absolute path in the assertion.
    assert stanza["command"] == (_shutil.which("supamem") or "supamem")
    if _shutil.which("supamem"):
        assert Path(stanza["command"]).is_absolute()
    assert stanza["env"].get("SUPAMEM_CONFIG") == str(cfg.resolve())
    assert stanza["env"]["SUPAMEM_PROJECT_ROOT"] == str(project.resolve())


def test_cursor_install_emits_supamem_config_pin(home: Path, project: Path) -> None:
    """SM-8b: fresh install in a repo with .supamem/config.toml emits BOTH
    SUPAMEM_PROJECT_ROOT and SUPAMEM_CONFIG pointing at the real files."""
    from supamem.install.cursor import install

    (project / ".supamem").mkdir()
    cfg = project / ".supamem" / "config.toml"
    cfg.write_text("[supamem]\ncollection = 'x'\n", encoding="utf-8")

    install()

    stanza = json.loads(
        (project / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    )["mcpServers"]["supamem"]
    assert stanza["env"]["SUPAMEM_PROJECT_ROOT"] == str(project.resolve())
    assert stanza["env"]["SUPAMEM_CONFIG"] == str(cfg.resolve())


def test_cursor_mcp_entry_falls_back_to_bare_name_when_which_misses(
    home: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SM-8 fallback: when shutil.which cannot resolve supamem, the template
    emits the bare name — never a hardcoded guess, never an exception."""
    import shutil as _shutil

    from supamem.install import cursor as cursor_mod

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    entry = cursor_mod._mcp_supamem_entry(project)
    assert entry["command"] == "supamem"


def test_cursor_install_no_config_env_carries_dm_source_only(
    home: Path, project: Path
) -> None:
    """SM-8: outside a repo (no .supamem/config.toml) the stanza env carries
    DM_MCP_SOURCE only — existing discovery-fallback behavior preserved."""
    from supamem.install.cursor import install

    install()

    stanza = json.loads(
        (project / ".cursor" / "mcp.json").read_text(encoding="utf-8")
    )["mcpServers"]["supamem"]
    assert set(stanza["env"].keys()) == {"DM_MCP_SOURCE"}


# ---------------------------------------------------------------------------
# SM-9: generator-managed destination guard (marker/sibling-manifest skip +
# overwrite-warning floor). Plan 19.1-05.
# ---------------------------------------------------------------------------


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin deterministic Rich output for substring assertions.

    Mirrors the subprocess smoke-test env pinning (AGENTS.md binding): a wide
    COLUMNS so warn messages never wrap mid-token, NO_COLOR so no ANSI codes,
    FORCE_COLOR popped so it cannot re-enable color.
    """
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _write_marked_mdc(project: Path) -> Path:
    """A .cursor/rules/dual-memory.mdc that DIFFERS from the packaged copy
    and carries a conventional generator marker within its first 10 lines."""
    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    target = rules / "dual-memory.mdc"
    target.write_text(
        "---\n"
        "description: harness-managed dual-memory rules\n"
        "---\n"
        "<!-- AUTO-GENERATED by the reporter harness — do not edit -->\n"
        "# Dual Memory (harness-generated)\n"
        "harness-owned content that differs from the packaged copy\n",
        encoding="utf-8",
    )
    return target


def test_cursor_install_skips_generated_mdc_head_marker(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str], wide_console: None
) -> None:
    """SM-9a: an existing differing .mdc whose head carries a generated/
    do-not-edit marker is left byte-identical; the warn names the file and
    both remedies (re-run the generator, or --force-cursor-rules)."""
    from supamem.install.cursor import install

    target = _write_marked_mdc(project)
    before = target.read_bytes()

    result = install()

    assert target.read_bytes() == before, "generator-managed .mdc was modified"
    out = capsys.readouterr().out
    assert "dual-memory.mdc" in out, f"warn must name the file:\n{out}"
    assert "generator-managed" in out
    assert "--force-cursor-rules" in out
    # The skipped copy is not claimed as pending work either.
    assert "dual-memory.mdc copy" not in result.diff


def test_cursor_install_skips_mdc_with_sibling_manifest(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str], wide_console: None
) -> None:
    """SM-9a: no head marker, but a sibling *.manifest.json in .cursor/rules/
    marks the destination directory as generator-managed — same skip outcome
    and the same actionable warn shape."""
    from supamem.install.cursor import install

    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    target = rules / "dual-memory.mdc"
    target.write_text(
        "---\ndescription: harness rules\n---\n# Dual Memory\nharness-owned\n",
        encoding="utf-8",
    )
    (rules / "rules.manifest.json").write_text(
        '{"generator": "reporter-harness"}', encoding="utf-8"
    )
    before = target.read_bytes()

    install()

    assert target.read_bytes() == before, "sibling-manifest .mdc was modified"
    out = capsys.readouterr().out
    assert "dual-memory.mdc" in out
    assert "generator-managed" in out
    assert "--force-cursor-rules" in out


def test_cursor_install_skips_generated_hooks_merge(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str], wide_console: None
) -> None:
    """SM-9a: a .cursor/hooks.json carrying a generated marker is NOT merged
    — byte-identical, same warn shape naming the file and the remedies."""
    from supamem.install.cursor import install

    cursor_dir = project / ".cursor"
    cursor_dir.mkdir()
    hooks = cursor_dir / "hooks.json"
    hooks.write_text(
        "// auto-generated by the reporter harness — do not edit\n"
        '{"sessionStart": [{"command": ["harness-tool"], "timeout": 30}]}\n',
        encoding="utf-8",
    )
    before = hooks.read_bytes()

    result = install()

    assert hooks.read_bytes() == before, "generator-managed hooks.json was modified"
    out = capsys.readouterr().out
    assert "hooks.json" in out, f"warn must name the file:\n{out}"
    assert "generator-managed" in out
    assert "--force-cursor-rules" in out
    # The skipped merge is not claimed as pending work either.
    assert "hooks.json" not in result.diff


def test_cursor_install_overwrites_unmarked_differing_mdc_with_warning(
    home: Path, project: Path, capsys: pytest.CaptureFixture[str], wide_console: None
) -> None:
    """SM-9b (Pitfall 9 lock): an existing .mdc with NO marker that differs
    from the packaged copy IS updated (legitimate upgrade path intact), now
    visibly — an overwrite warning names the replaced file."""
    from supamem.install.cursor import _packaged_mdc_source, install

    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    target = rules / "dual-memory.mdc"
    target.write_text(
        "---\ndescription: old packaged copy\n---\n# Dual Memory (old)\n",
        encoding="utf-8",
    )

    install()

    assert target.read_bytes() == _packaged_mdc_source().read_bytes(), (
        "unmarked differing .mdc must still be upgraded (Pitfall 9)"
    )
    out = capsys.readouterr().out
    assert "dual-memory.mdc" in out, f"overwrite warn must name the file:\n{out}"
    assert "overwriting existing" in out


# ---------------------------------------------------------------------------
# SM-9c: --force-cursor-rules escape hatch (dispatcher-plumbed). Plan 19.1-05.
# ---------------------------------------------------------------------------


def test_dispatcher_force_cursor_rules_overrides_marker_skip(
    home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    wide_console: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SM-9c: routed through the install() dispatcher, force_cursor_rules=True
    overrides ONLY the generated-marker skip — the marked .mdc IS copied —
    while the overwrite-warning floor still prints."""
    from supamem.install import install as dispatcher_install
    from supamem.install.cursor import _packaged_mdc_source

    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(home / ".cache" / "supamem"))

    target = _write_marked_mdc(project)

    rc = dispatcher_install(
        client="cursor",
        skip_models=True,
        force_cursor_rules=True,
    )

    assert rc == 0
    assert target.read_bytes() == _packaged_mdc_source().read_bytes(), (
        "force must let the copy proceed past the generated marker"
    )
    out = capsys.readouterr().out
    assert "appears generator-managed" not in out, "force must suppress the skip warn"
    assert "overwriting existing" in out, "the overwrite floor still prints under force"
