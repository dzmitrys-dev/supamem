"""RED integration tests for supamem.install.agent_patcher (Plan 08.1-01).

This file is intentionally RED. The module ``supamem.install.agent_patcher`` does
not yet exist; Plan 08.1-03 implements the filesystem walk + manifest IO behavior
that these tests cover. Until then, the import below fails and pytest reports
a collection error — that IS the RED state.

Each stub additionally calls ``pytest.fail("RED: implement in Plan 03")`` so a
future agent who silently un-fails the import without implementing the I/O
helpers still gets a hard FAIL.

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

from pathlib import Path

import pytest


def _import_patcher() -> object:
    """Import the not-yet-existing patcher module.

    Lifted out of module scope so the file collects cleanly; each RED stub
    surfaces ImportError or pytest.fail as the failing signal.
    """
    from supamem.install import agent_patcher  # type: ignore[import-not-found]

    return agent_patcher


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Mirrors tests/test_doctor.py:9-13 home fixture pattern."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Filesystem walk — scan_agent_dirs
# ---------------------------------------------------------------------------


def test_scan_global_only_no_project(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_scan_global_and_project(home: Path, tmp_path: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


# ---------------------------------------------------------------------------
# patch_all — manifest write, idempotency, failure resilience
# ---------------------------------------------------------------------------


def test_patch_all_writes_manifest(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_patch_all_idempotent_second_run(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_patch_all_skips_symlinks_with_warning(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_patch_all_handles_vanished_file_with_one_retry(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_patch_all_continues_after_one_file_failure(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


# ---------------------------------------------------------------------------
# unpatch_all — restore-when-clean, skip-when-edited, missing-manifest
# ---------------------------------------------------------------------------


def test_unpatch_all_restores_original_when_sha_matches(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_unpatch_all_skips_when_user_edited(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


def test_unpatch_all_handles_missing_manifest_gracefully(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")


# ---------------------------------------------------------------------------
# Manifest atomicity — temp-and-rename, FileLock
# ---------------------------------------------------------------------------


def test_atomic_manifest_write_no_partial_state(home: Path) -> None:
    pytest.fail("RED: implement in Plan 03")
