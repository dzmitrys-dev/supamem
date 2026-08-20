"""Integration tests for the install/repair/init -> agent_patcher wiring (Plan 08.1-04).

For pure-function tests see ``test_agent_patcher.py``; for filesystem unit
tests see ``test_agent_patcher_fs.py``. These tests drive the install
pipeline end-to-end (direct function call, NOT subprocess) so we can assert
manifest state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


CSV_PATCHABLE_FIXTURE = (
    "---\n"
    "name: csv-patchable\n"
    "description: restrictive whitelist, no supamem coverage\n"
    "tools: Read, Bash, Grep, mcp__context7__*\n"
    "---\n"
    "\n"
    "body\n"
)


@pytest.fixture
def fake_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Redirect ``Path.home()`` and ``SUPAMEM_CACHE_DIR`` to a tmp tree.

    Mirrors ``tests/test_doctor.py`` ``home`` fixture pattern. Crucially,
    monkeypatching ``Path.home`` ensures both:

      * ``install._autodetect()`` reads from the fake home;
      * ``agent_patcher.scan_agent_dirs()`` walks the fake global agents dir;
      * ``agent_patcher.manifest_path()`` lands under ``<tmp>/cache/`` via
        the ``SUPAMEM_CACHE_DIR`` env override.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(tmp_path / "cache"))
    # Belt-and-suspenders: prevent reranker network egress on these tests.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    return tmp_path


def _seed_patchable_agent(home: Path) -> Path:
    agents_dir = home / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agents_dir / "csv-patchable.md"
    agent_file.write_text(CSV_PATCHABLE_FIXTURE, encoding="utf-8")
    return agent_file


def _manifest_path(home: Path) -> Path:
    return home / "cache" / "agent_patches.json"


def test_install_invokes_patcher_and_writes_manifest(
    fake_home: Path,
) -> None:
    """REACH-01 canonical end-to-end: ``install()`` patches the seeded agent
    file and writes a manifest entry.
    """
    agent_file = _seed_patchable_agent(fake_home)
    # claude-code is auto-detected via the presence of .claude.json.
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import install

    rc = install(
        client="claude-code",
        dry_run=True,
        skip_models=True,
        skip_patch_agents=False,
    )
    assert rc == 0, f"install exited non-zero: {rc}"

    manifest_path = _manifest_path(fake_home)
    assert manifest_path.is_file(), (
        f"expected manifest at {manifest_path}, got {list((fake_home / 'cache').iterdir()) if (fake_home / 'cache').exists() else 'cache dir absent'}"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert len(manifest["patches"]) == 1
    entry = manifest["patches"][0]
    assert entry["path"] == str(agent_file)
    # Agent file now contains the supamem wildcard.
    patched_text = agent_file.read_text(encoding="utf-8")
    assert "mcp__supamem__*" in patched_text


def test_install_with_skip_patch_agents_does_not_invoke_patcher(
    fake_home: Path,
) -> None:
    """``skip_patch_agents=True`` short-circuits before any filesystem write:
    no manifest, agent file byte-identical to the seeded fixture."""
    agent_file = _seed_patchable_agent(fake_home)
    original_bytes = agent_file.read_bytes()
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import install

    rc = install(
        client="claude-code",
        dry_run=True,
        skip_models=True,
        skip_patch_agents=True,
    )
    assert rc == 0

    manifest_path = _manifest_path(fake_home)
    assert not manifest_path.exists(), (
        f"expected no manifest with skip_patch_agents=True, got {manifest_path}"
    )
    assert agent_file.read_bytes() == original_bytes, (
        "agent file should be byte-identical when patcher is skipped"
    )


def test_repair_invokes_patcher(
    fake_home: Path,
) -> None:
    """REACH-02: ``repair()`` re-runs the patcher (catches plugin-installed
    agents added since the last install)."""
    agent_file = _seed_patchable_agent(fake_home)
    # repair() detects claude-code via .claude.json existence (same heuristic
    # as install autodetect).
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import repair

    rc = repair(
        client="claude-code",
        dry_run=True,
        skip_models=True,
        skip_patch_agents=False,
    )
    # repair returns the install rc (0 on success).
    assert rc == 0, f"repair exited non-zero: {rc}"

    manifest_path = _manifest_path(fake_home)
    assert manifest_path.is_file(), f"expected manifest at {manifest_path}"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["patches"]) >= 1
    paths = [e["path"] for e in manifest["patches"]]
    assert str(agent_file) in paths
    assert "mcp__supamem__*" in agent_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SM-7a/7b: repair --dry-run changes NOTHING (strict contract, research Q7)
# ---------------------------------------------------------------------------


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Snapshot every file under ``root`` as (bytes, mtime_ns)."""
    out: dict[str, tuple[bytes, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (p.read_bytes(), st.st_mtime_ns)
    return out


def test_repair_dry_run_changes_nothing_end_to_end(
    fake_home: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-7b e2e (strict Q7 contract): ``repair(client, dry_run=True)`` on a
    fully-installed fixture changes NOTHING — every client target
    byte-identical, share dir untouched, patch manifest content+mtime
    unchanged, no model-fetch side effects, and the skip-note names the
    skipped share-dir sync."""
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    agent_file = _seed_patchable_agent(fake_home)
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import install, repair

    # Fully-installed fixture: a REAL install writes client files, patches
    # the agent, and writes the patch manifest.
    assert install(client="claude-code", skip_models=True) == 0
    assert "mcp__supamem__*" in agent_file.read_text(encoding="utf-8")

    targets = [
        cwd / ".mcp.json",
        fake_home / ".claude.json",
        fake_home / ".claude" / "settings.json",
        fake_home / "CLAUDE.md",
        agent_file,
    ]
    manifest = _manifest_path(fake_home)
    assert manifest.is_file()
    before = {str(p): p.read_bytes() for p in targets}
    manifest_before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
    share_before = _tree_snapshot(fake_home / ".supamem")
    assert share_before, "fixture sanity: real install must have synced the share dir"

    capsys.readouterr()  # clear install output
    rc = repair(client="claude-code", dry_run=True, skip_models=True)
    assert rc == 0, f"dry-run repair exited non-zero: {rc}"

    for p in targets:
        assert p.read_bytes() == before[str(p)], f"dry-run repair modified {p}"
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == manifest_before, (
        "dry-run repair must not rewrite the patch manifest"
    )
    assert _tree_snapshot(fake_home / ".supamem") == share_before, (
        "dry-run repair must not touch the share dir"
    )
    assert not list(cwd.glob("*.tmp.*"))
    out = capsys.readouterr().out
    assert "share-dir sync" in out, "dry-run must note the skipped share-dir sync"
