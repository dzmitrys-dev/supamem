"""Integration tests for supamem.install.agent_patcher I/O layer (Plan 08.1-03).

Plan 02 shipped the pure kernel (detect_tools_state, patch_yaml, frontmatter_block,
block_sha256). Plan 03 wraps it with the filesystem walker (scan_agent_dirs),
manifest IO (load_manifest / save_manifest / manifest_path), and entry points
patch_all + unpatch_all. These tests cover concurrent-write safety, atomicity,
retry-on-vanish (D-FAIL-04), and symlink skip (P6).

REACH-NN -> test_NAME mapping (per 08.1-RESEARCH.md "Phase Requirements -> Test Map"):
  REACH-01 / REACH-02  -> test_scan_global_only_no_project, test_scan_global_and_project
  REACH-01             -> test_patch_all_writes_manifest
  REACH-03 / REACH-08  -> test_patch_all_idempotent_second_run
  REACH-07             -> test_patch_all_skips_symlinks_with_warning,
                          test_patch_all_handles_vanished_file_with_one_retry,
                          test_patch_all_continues_after_one_file_failure
  REACH-05             -> test_unpatch_all_restores_original_when_sha_matches,
                          test_unpatch_all_skips_when_user_edited,
                          test_unpatch_all_handles_missing_manifest_gracefully
  REACH-06             -> test_atomic_manifest_write_no_partial_state
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).parent / "_fixtures" / "agents"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Mirrors tests/test_doctor.py:9-13 home fixture pattern.

    Also redirects ``SUPAMEM_CACHE_DIR`` so the manifest lands inside
    ``tmp_path`` rather than the developer's real cache. Tests that exercise
    the manifest atomicity assertions rely on this isolation.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache_dir))
    # Speed: shrink the manifest lock timeout in tests.
    monkeypatch.setenv("SUPAMEM_MANIFEST_LOCK_TIMEOUT", "10")
    return tmp_path


def _populate(target_dir: Path, fixtures: list[str]) -> list[Path]:
    """Copy named fixture files into ``target_dir`` (creating it). Returns paths."""
    target_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for name in fixtures:
        src = _FIXTURES / name
        dst = target_dir / name
        shutil.copyfile(src, dst)
        out.append(dst)
    return out


def _reload_patcher():
    """Re-import the patcher module so module-level constants pick up env overrides."""
    import importlib

    import supamem.install.agent_patcher as mod  # noqa: PLC0415

    return importlib.reload(mod)


# ---------------------------------------------------------------------------
# Filesystem walk — scan_agent_dirs
# ---------------------------------------------------------------------------


def test_scan_global_only_no_project(home: Path) -> None:
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    _populate(global_dir, ["csv-patchable.md", "csv-covered.md"])

    result = mod.scan_agent_dirs(global_dir=global_dir, project_dir=None)

    assert len(result) == 2
    scopes = {r[1] for r in result}
    assert scopes == {"global"}
    names = sorted(p.name for p, _ in result)
    assert names == ["csv-covered.md", "csv-patchable.md"]


def test_scan_global_and_project(home: Path, tmp_path: Path) -> None:
    mod = _reload_patcher()
    # Synthetic project tree: <proj_root>/.claude/agents/<file>
    proj_root = tmp_path / "proj"
    proj_agents = proj_root / ".claude" / "agents"
    global_dir = home / ".claude" / "agents"
    _populate(global_dir, ["csv-patchable.md"])
    _populate(proj_agents, ["block-list-patchable.md", "csv-covered.md"])

    result = mod.scan_agent_dirs(global_dir=global_dir, project_dir=proj_agents)

    by_scope: dict[str, list[str]] = {"global": [], "project": []}
    for path, scope in result:
        by_scope[scope].append(path.name)
    assert by_scope["global"] == ["csv-patchable.md"]
    assert sorted(by_scope["project"]) == ["block-list-patchable.md", "csv-covered.md"]


def test_patch_all_skips_symlinks_with_warning(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scan-side coverage: symlinks are excluded AND a stderr warning is emitted.

    Full ``patch_all`` integration of symlink-skip is exercised in Task 2 tests;
    here we assert the scanner alone refuses to follow them (P6 / T-08.1.03-02).
    """
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    real = _populate(global_dir, ["csv-patchable.md"])[0]

    # Add a symlink in the same dir pointing to the real fixture.
    link_target = home / "external" / "linked-agent.md"
    link_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(real, link_target)
    link = global_dir / "linked-agent.md"
    link.symlink_to(link_target)

    result = mod.scan_agent_dirs(global_dir=global_dir, project_dir=None)

    names = [p.name for p, _ in result]
    assert "csv-patchable.md" in names
    assert "linked-agent.md" not in names
    err = capsys.readouterr().err
    assert "skipped symlink" in err
    assert "linked-agent.md" in err


# ---------------------------------------------------------------------------
# Manifest atomicity — temp-and-rename, FileLock
# ---------------------------------------------------------------------------


def test_atomic_manifest_write_no_partial_state(home: Path) -> None:
    """Two sequential writes leave no .tmp residue; load round-trips the data."""
    import os

    mod = _reload_patcher()

    payload_a = {
        "schema_version": 1,
        "supamem_version": "0.0.0+dev",
        "patches": [{"path": "/a", "scope": "global"}],
    }
    mod.save_manifest(payload_a)
    loaded = mod.load_manifest()
    assert loaded["patches"] == [{"path": "/a", "scope": "global"}]

    payload_b = dict(payload_a)
    payload_b["patches"] = [{"path": "/a", "scope": "global"}, {"path": "/b", "scope": "project"}]
    mod.save_manifest(payload_b)

    cache_dir = Path(os.environ["SUPAMEM_CACHE_DIR"])
    leftover = [p for p in cache_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftover == [], f"atomic rename left tmp files behind: {leftover}"

    final = mod.load_manifest()
    assert len(final["patches"]) == 2


# ---------------------------------------------------------------------------
# patch_all — manifest write, idempotency, failure resilience
# ---------------------------------------------------------------------------


def test_patch_all_writes_manifest(home: Path) -> None:
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    _populate(
        global_dir,
        ["csv-patchable.md", "block-list-patchable.md", "csv-covered.md"],
    )

    summary = mod.patch_all(skip=False)

    assert len(summary.patched) == 2
    assert len(summary.covered) == 1

    # Both patched files now contain the supamem wildcard.
    for entry in summary.patched:
        assert "mcp__supamem__*" in Path(entry.path).read_text(encoding="utf-8")

    manifest = mod.load_manifest()
    assert len(manifest["patches"]) == 2
    paths_in_manifest = {p["path"] for p in manifest["patches"]}
    paths_in_summary = {entry.path for entry in summary.patched}
    assert paths_in_manifest == paths_in_summary


def test_patch_all_idempotent_second_run(home: Path) -> None:
    """D-COVER-03: a second invocation produces zero new manifest entries."""
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    _populate(global_dir, ["csv-patchable.md", "csv-covered.md"])

    first = mod.patch_all(skip=False)
    assert len(first.patched) == 1

    # Snapshot mtimes after first run.
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in global_dir.glob("*.md")}

    second = mod.patch_all(skip=False)
    assert len(second.patched) == 0
    # All files now show as covered (the previously-patched one + the originally covered one).
    assert len(second.covered) == 2

    mtimes_after = {p.name: p.stat().st_mtime_ns for p in global_dir.glob("*.md")}
    assert mtimes_before == mtimes_after, "idempotent run must not rewrite files"


# ---------------------------------------------------------------------------
# patch_all(dry_run=…) — SM-7b: full detection pass, zero writes
# ---------------------------------------------------------------------------


def test_patch_all_dry_run_detects_without_writing(home: Path) -> None:
    """SM-7b: ``patch_all(dry_run=True)`` runs the FULL detection pass —
    would-patch count equals the patchable files — but writes no agent file
    and no manifest. Detection semantics are unchanged by the flag."""
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    paths = _populate(
        global_dir,
        ["csv-patchable.md", "block-list-patchable.md", "csv-covered.md"],
    )
    originals = {p.name: p.read_bytes() for p in paths}
    mtimes = {p.name: p.stat().st_mtime_ns for p in paths}

    dry = mod.patch_all(skip=False, dry_run=True)

    # Same detection outcome as a real run (Pitfall 7): 2 patchable, 1 covered.
    assert len(dry.would_patch) == 2
    assert len(dry.covered) == 1
    assert dry.patched == []
    # Zero writes: files byte+mtime identical, manifest absent.
    for p in paths:
        assert p.read_bytes() == originals[p.name]
        assert p.stat().st_mtime_ns == mtimes[p.name]
    assert not mod.manifest_path().exists()

    # The subsequent REAL run on the identical fixture patches exactly the
    # would-patch set (dry-run did not consume or alter detection state).
    real = mod.patch_all(skip=False)
    assert {e.path for e in real.patched} == {e.path for e in dry.would_patch}
    assert len(real.patched) == 2


def test_patch_all_dry_run_leaves_existing_manifest_unchanged(home: Path) -> None:
    """SM-7b: an existing manifest's content AND mtime survive a dry-run that
    detects new patchable files (nothing is persisted under the no-op flag)."""
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    _populate(global_dir, ["csv-patchable.md"])
    mod.patch_all(skip=False)  # real run → manifest written
    manifest_p = mod.manifest_path()
    assert manifest_p.is_file()
    before = (manifest_p.read_bytes(), manifest_p.stat().st_mtime_ns)

    # A new patchable file appears after the last real run.
    _populate(global_dir, ["block-list-patchable.md"])
    summary = mod.patch_all(skip=False, dry_run=True)

    assert len(summary.would_patch) == 1
    assert (manifest_p.read_bytes(), manifest_p.stat().st_mtime_ns) == before, (
        "dry-run must not rewrite the patch manifest"
    )


def test_patch_all_handles_vanished_file_with_one_retry(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-FAIL-04: file vanishing mid-scan triggers exactly one retry, then skip."""
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    paths = _populate(global_dir, ["csv-patchable.md"])
    target = paths[0]

    real_read_text = Path.read_text
    calls: dict[str, int] = {"n": 0}

    def flaky_read_text(self: Path, *a, **kw):  # type: ignore[no-untyped-def]
        if self == target:
            calls["n"] += 1
            # Always raise FileNotFoundError so retry exhausts → skip:vanished.
            raise FileNotFoundError(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    summary = mod.patch_all(skip=False)

    # Two read attempts: original + one retry.
    assert calls["n"] == 2
    reasons = [reason for _, reason in summary.skipped]
    assert any("vanished" in r for r in reasons)


def test_patch_all_continues_after_one_file_failure(home: Path) -> None:
    """D-FAIL-01: malformed file is skipped; the good file is still patched."""
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    _populate(global_dir, ["csv-patchable.md", "malformed.md"])

    summary = mod.patch_all(skip=False)

    assert len(summary.patched) == 1
    assert summary.patched[0].path.endswith("csv-patchable.md")
    assert len(summary.skipped) == 1
    skipped_path, reason = summary.skipped[0]
    assert skipped_path.path.endswith("malformed.md")
    assert "malformed" in reason


# ---------------------------------------------------------------------------
# unpatch_all — restore-when-clean, skip-when-edited, missing-manifest
# ---------------------------------------------------------------------------


def test_unpatch_all_restores_original_when_sha_matches(home: Path) -> None:
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    paths = _populate(global_dir, ["csv-patchable.md", "block-list-patchable.md"])
    originals = {p.name: p.read_bytes() for p in paths}

    mod.patch_all(skip=False)
    # Sanity: files were actually mutated.
    for p in paths:
        assert "mcp__supamem__*" in p.read_text(encoding="utf-8")

    summary = mod.unpatch_all()

    assert len(summary.restored) == 2
    for p in paths:
        assert p.read_bytes() == originals[p.name], f"{p.name} not restored byte-identical"

    # Manifest should now be clean (file removed when patches list empties).
    assert not mod.manifest_path().exists()


def test_unpatch_all_skips_when_user_edited(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _reload_patcher()
    global_dir = home / ".claude" / "agents"
    paths = _populate(global_dir, ["csv-patchable.md"])
    target = paths[0]

    mod.patch_all(skip=False)
    patched_text = target.read_text(encoding="utf-8")
    assert "mcp__supamem__*" in patched_text

    # User edits the frontmatter (changing the description triggers SHA drift).
    mutated = patched_text.replace(
        "description: restrictive whitelist, no supamem coverage",
        "description: user-edited line",
    )
    assert mutated != patched_text
    target.write_text(mutated, encoding="utf-8")

    capsys.readouterr()  # clear prior output
    summary = mod.unpatch_all()

    assert summary.restored == []
    assert len(summary.skipped_user_edited) == 1
    assert target.read_text(encoding="utf-8") == mutated  # untouched
    err = capsys.readouterr().err
    assert "edited" in err
    assert target.name in err


def test_unpatch_all_handles_missing_manifest_gracefully(home: Path) -> None:
    mod = _reload_patcher()
    summary = mod.unpatch_all()
    assert summary.restored == []
    assert summary.skipped_user_edited == []
    assert summary.skipped_missing == []
