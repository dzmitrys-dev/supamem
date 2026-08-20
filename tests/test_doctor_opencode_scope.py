"""Regression test for WR-04 — doctor watched the wrong AGENTS.md.

``doctor._client_targets()`` returned ``("opencode", Path.home() / "AGENTS.md")``
while ``opencode.install()`` / ``uninstall()`` operate on ``Path.cwd() /
"AGENTS.md"``. So the SM-4d duplicate-block warning was blind to the file that
actually accumulates blocks (any project ``AGENTS.md``), and conversely: a user
who once ran ``supamem install --client opencode`` from ``$HOME`` got duplicates
in ``~/AGENTS.md`` that doctor reported (rc=1, "run supamem repair") but that
``repair`` — run from a project directory — would never touch. Permanently red,
un-actionable.

The target mismatch predates 19.1; SM-4d escalated its consequence by wiring
``block_count > 1`` into ``any_drift``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem import __version__
from supamem.config_io import wrap_managed_block

IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"


@pytest.fixture
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    return cwd


def _rows_for(client: str) -> list[dict]:
    from supamem.doctor import version_drift_report

    return [r for r in version_drift_report() if r["client"] == client]


def test_project_agents_md_is_reported(home: Path, project: Path) -> None:
    """The file the opencode installer actually writes must be visible.

    Pre-fix failure: only ``~/AGENTS.md`` was inspected, so a stale-version
    block in the PROJECT ``AGENTS.md`` — the one install/uninstall touch — was
    invisible and never reported as drift.
    """
    (project / "AGENTS.md").write_text(
        wrap_managed_block(IMPORT_LINE, version="0.2.0") + "\n", encoding="utf-8"
    )

    rows = _rows_for("opencode")
    present = [r for r in rows if r.get("present")]
    assert present, f"the project AGENTS.md must be inspected; got {rows}"
    assert any(r["path"] == str(project / "AGENTS.md") for r in present), rows
    assert any(r.get("drift") for r in present), "stale version must count as drift"


def test_every_reported_row_names_its_path(home: Path, project: Path) -> None:
    """The warning must be locatable — a user cannot act on "opencode has 2
    managed blocks" without knowing WHICH AGENTS.md."""
    block = wrap_managed_block(IMPORT_LINE, version=__version__)
    (project / "AGENTS.md").write_text(block + "\n" + block, encoding="utf-8")

    rows = _rows_for("opencode")
    hit = [r for r in rows if r["path"] == str(project / "AGENTS.md")]
    assert hit, rows
    assert hit[0]["block_count"] == 2, hit


def test_duplicate_warning_text_includes_the_path(
    home: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    block = wrap_managed_block(IMPORT_LINE, version=__version__)
    (project / "AGENTS.md").write_text(block + "\n" + block, encoding="utf-8")

    mod.run_doctor()
    flat = " ".join(capsys.readouterr().out.split())
    assert "managed blocks detected" in flat, flat
    assert "AGENTS.md" in flat, flat


def test_home_agents_md_is_still_reported(home: Path, project: Path) -> None:
    """The legacy scope must not be dropped: a user who once installed from
    $HOME still has that file, and it still needs reporting."""
    (home / "AGENTS.md").write_text(
        wrap_managed_block(IMPORT_LINE, version="0.2.0") + "\n", encoding="utf-8"
    )

    rows = _rows_for("opencode")
    assert any(
        r.get("present") and r["path"] == str(home / "AGENTS.md") for r in rows
    ), rows


def test_both_scopes_reported_without_duplication(home: Path, project: Path) -> None:
    """Both files present -> two distinct rows, one per path."""
    (home / "AGENTS.md").write_text(
        wrap_managed_block(IMPORT_LINE, version="0.2.0") + "\n", encoding="utf-8"
    )
    (project / "AGENTS.md").write_text(
        wrap_managed_block(IMPORT_LINE, version=__version__) + "\n", encoding="utf-8"
    )

    rows = _rows_for("opencode")
    paths = [r["path"] for r in rows if r.get("present")]
    assert sorted(paths) == sorted([str(home / "AGENTS.md"), str(project / "AGENTS.md")]), rows
    assert len(paths) == len(set(paths)), f"no path may be reported twice: {paths}"


def test_no_duplicate_row_when_cwd_is_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from $HOME must not report the same file twice."""
    monkeypatch.chdir(home)
    (home / "AGENTS.md").write_text(
        wrap_managed_block(IMPORT_LINE, version=__version__) + "\n", encoding="utf-8"
    )

    rows = [r for r in _rows_for("opencode") if r.get("present")]
    assert len(rows) == 1, f"cwd == home must collapse to one row: {rows}"
