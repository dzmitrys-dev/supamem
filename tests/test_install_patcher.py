"""Integration tests for the install/repair/init -> agent_patcher wiring (Plan 08.1-04).

For pure-function tests see ``test_agent_patcher.py``; for filesystem unit
tests see ``test_agent_patcher_fs.py``. These tests drive the install
pipeline end-to-end (direct function call, NOT subprocess) so we can assert
manifest state.
"""
from __future__ import annotations

import json
import re
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
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REACH-01 canonical end-to-end: ``install()`` patches the seeded agent
    file and writes a manifest entry.

    Runs REAL (no dry_run): under the SM-7a strict contract a dry-run
    install skips the patcher entirely, so real-patch assertions must use a
    sandboxed cwd + real install.
    """
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    agent_file = _seed_patchable_agent(fake_home)
    # claude-code is auto-detected via the presence of .claude.json.
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import install

    rc = install(
        client="claude-code",
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
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REACH-02: ``repair()`` re-runs the patcher (catches plugin-installed
    agents added since the last install).

    Runs REAL (no dry_run): under the SM-7a strict contract a dry-run
    repair performs no patcher writes.
    """
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    agent_file = _seed_patchable_agent(fake_home)
    # repair() detects claude-code via .claude.json existence (same heuristic
    # as install autodetect).
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")

    from supamem.install import repair

    rc = repair(
        client="claude-code",
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
    # Redirect the module-level share-dir constant (bound at import with the
    # REAL home) so the share sync lands inside the sandbox and the snapshot
    # comparison is meaningful.
    monkeypatch.setattr(
        "supamem.install.share.DEFAULT_SHARE_DIR", fake_home / ".supamem" / "share"
    )
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


# ---------------------------------------------------------------------------
# SM-7c: truthful accounting + phrasing (dry-run predicts the real run)
# ---------------------------------------------------------------------------


@pytest.fixture
def project(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Sandbox cwd so project-scope install writes never escape tmp."""
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    return cwd


def _parse_count(out: str, pattern: str) -> int:
    m = re.search(pattern, out)
    assert m, f"pattern {pattern!r} not found in output:\n{out}"
    return int(m.group(1))


def _seed_claude_fixture(fake_home: Path) -> None:
    _seed_patchable_agent(fake_home)
    (fake_home / ".claude.json").write_text("{}", encoding="utf-8")


def test_dry_run_count_equals_real_run_writes_install(
    fake_home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-7c accounting invariant (install path): the dry-run would-write
    count derives from the same diff accounting the real run uses — on an
    identical fixture, dry-run count == files the real run writes, and the
    patcher's would-patch count == the real run's patched count."""
    from supamem.install import install

    _seed_claude_fixture(fake_home)

    capsys.readouterr()
    assert install(client="claude-code", dry_run=True, skip_models=True) == 0
    dry_out = capsys.readouterr().out
    would_write = _parse_count(dry_out, r"would write (\d+) file\(s\)")
    would_patch = _parse_count(dry_out, r"would patch (\d+) subagent file\(s\)")
    # Dry-run wrote nothing.
    assert not _manifest_path(fake_home).exists()
    for p in (project / ".mcp.json", fake_home / ".claude" / "settings.json"):
        assert not p.exists()

    capsys.readouterr()
    assert install(client="claude-code", skip_models=True) == 0
    real_out = capsys.readouterr().out
    wrote = _parse_count(real_out, r"installed \((\d+) file\(s\) written\)")
    patched = _parse_count(real_out, r"patched (\d+) subagent file\(s\)")

    assert would_write == wrote, (
        f"dry-run predicted {would_write} writes, real run wrote {wrote}"
    )
    assert would_patch == patched


def test_dry_run_count_equals_real_run_writes_repair(
    fake_home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-7c accounting invariant (repair path): the dry-run strip-and-
    reinstall prediction equals the real repair's reinstall write count on
    an identical healthy fixture."""
    from supamem.install import install, repair

    _seed_claude_fixture(fake_home)
    assert install(client="claude-code", skip_models=True) == 0  # healthy fixture

    capsys.readouterr()
    assert repair(client="claude-code", dry_run=True, skip_models=True) == 0
    dry_out = capsys.readouterr().out
    would_rewrite = _parse_count(dry_out, r"would rewrite (\d+) file\(s\)")

    capsys.readouterr()
    assert repair(client="claude-code", skip_models=True) == 0
    real_out = capsys.readouterr().out
    wrote = _parse_count(real_out, r"installed \((\d+) file\(s\) written\)")

    assert would_rewrite == wrote, (
        f"dry-run repair predicted {would_rewrite} rewrites, real repair reinstalled {wrote}"
    )


def test_dry_run_phrasing_never_claims_performed_work(
    fake_home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-7c phrasing: dry-run output contains NO ✓-glyph past-tense work
    claims; detected patchable files render as 'would patch'.

    WR-02: this guard used to assert only ``"✓" not in dry_out``, and its
    fixture had no ``CLAUDE.md`` at all — so the managed-block sweep branch was
    never entered and its past-tense ``info()`` line (rendered with ``→``, not
    ``✓``) sailed straight through. The fixture now seeds a duplicated
    ``CLAUDE.md`` so the sweep DOES run, and the assertion rejects any
    past-tense work verb rather than one glyph.
    """
    from supamem.config_io import wrap_managed_block
    from supamem.install import install

    _seed_claude_fixture(fake_home)
    # Seed the state that makes the sweep branch fire.
    import_line = "@~/.supamem/share/rules/dual-memory.md"
    dup = wrap_managed_block(import_line, version="0.2.0")
    dup2 = wrap_managed_block(import_line, version="0.3.0a7")
    claude_md = fake_home / "CLAUDE.md"
    claude_md.write_text(f"# notes\n{dup}\nmiddle\n{dup2}\ntail\n", encoding="utf-8")
    before_md = claude_md.read_text(encoding="utf-8")

    capsys.readouterr()
    install(client="claude-code", dry_run=True, skip_models=True)
    dry_out = capsys.readouterr().out

    assert "✓" not in dry_out, f"dry-run output must not claim performed work:\n{dry_out}"
    assert "would patch 1 subagent file(s)" in dry_out
    # The sweep branch must actually have been exercised by this fixture —
    # this phrase is present in both the buggy and the fixed wording, so it
    # asserts coverage without also asserting the tense.
    assert "managed-block marker(s)" in dry_out, (
        f"fixture no longer reaches the sweep branch:\n{dry_out}"
    )
    assert "would sweep" in dry_out, dry_out
    # No past-tense work verb anywhere in a dry run.
    for verb in ("swept", "wrote", "patched", "synced", "installed", "removed", "written"):
        assert verb not in dry_out, (
            f"dry-run output claims performed work with {verb!r}:\n{dry_out}"
        )
    # And the dry run really did write nothing.
    assert claude_md.read_text(encoding="utf-8") == before_md


def test_real_run_idempotent_second_pass_renders_info_not_ok(
    fake_home: Path,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-7c phrasing: a second REAL install with zero new patch work
    renders covered files as info ('already reachable'), never a
    ✓-patched claim."""
    from supamem.install import install

    _seed_claude_fixture(fake_home)

    install(client="claude-code", skip_models=True)
    capsys.readouterr()
    install(client="claude-code", skip_models=True)
    second_out = capsys.readouterr().out

    assert "✓ patched" not in second_out
    assert "1 subagent file(s) already reachable" in second_out
