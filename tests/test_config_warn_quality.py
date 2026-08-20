"""Regression tests for WR-05 / WR-08 — unknown-key warning noise and wording.

WR-05: SM-8 makes every installed MCP entry set
``SUPAMEM_CONFIG=<cwd>/.supamem/config.toml``. ``load_config`` processes that
same file twice — at rung 2 (project config) and again at rung 1a (explicit env
path) — so every unknown-key warning was emitted twice, for exactly the
configuration the 19.1 installer produces. On the MCP stdio path that doubles
the stderr noise on every server start.

WR-08: the ``[supamem]`` warning passed the nested-table first segments as its
``accepted`` list, so a user fixing a typo read "accepted: cache, classifier,
eval, ..." and reasonably concluded ``cache = "..."`` was a valid flat key. It is
not — those are table names, and a scalar at any of them is silently discarded.
The message also said "accepted" twice with two different meanings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem.config import load_config


def _write_project_cfg(tmp_path: Path, body: str) -> Path:
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir(exist_ok=True)
    p = cfg_dir / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# ───────────────── WR-05: one warning per distinct message ──────────────────


def test_supamem_config_pointing_at_project_file_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact SM-8 installer configuration.

    Pre-fix failure: ``grep -c "unknown key"`` -> 2 for a single typo, because
    rungs 2 and 1a both processed the same file.
    """
    cfg_path = _write_project_cfg(tmp_path, "[supamem.eval]\nbogus_eval_key = 42\n")
    monkeypatch.setenv("SUPAMEM_CONFIG", str(cfg_path))

    load_config(tmp_path)

    err = capsys.readouterr().err
    assert err.count("unknown key(s)") == 1, f"expected exactly one warning:\n{err}"
    assert "bogus_eval_key" in err


def test_same_file_via_relative_and_absolute_path_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dedup must be by message, not by path string — a symlinked or
    differently-spelled path to the same file must not double the warning."""
    cfg_path = _write_project_cfg(tmp_path, "[supamem.eval]\nbogus_eval_key = 42\n")
    link = tmp_path / "aliased-config.toml"
    try:
        link.symlink_to(cfg_path)
    except OSError:  # pragma: no cover — platform without symlink permission
        pytest.skip("symlinks unavailable")
    monkeypatch.setenv("SUPAMEM_CONFIG", str(link))

    load_config(tmp_path)

    err = capsys.readouterr().err
    assert err.count("unknown key(s)") == 1, f"expected exactly one warning:\n{err}"


def test_distinct_unknown_keys_still_warn_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dedup must suppress only IDENTICAL messages — two different tables with
    different unknown keys must both be reported."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    _write_project_cfg(
        tmp_path,
        "[supamem]\nbogus_flat = 1\n\n[supamem.eval]\nbogus_eval_key = 42\n",
    )

    load_config(tmp_path)

    err = capsys.readouterr().err
    assert err.count("unknown key(s)") == 2, f"both tables must be reported:\n{err}"
    assert "bogus_flat" in err
    assert "bogus_eval_key" in err


def test_dedup_does_not_leak_across_load_config_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each load_config call gets a fresh dedup scope — a second invocation
    (e.g. a second MCP server start) must still warn."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    _write_project_cfg(tmp_path, "[supamem.eval]\nbogus_eval_key = 42\n")

    load_config(tmp_path)
    capsys.readouterr()
    load_config(tmp_path)

    assert capsys.readouterr().err.count("unknown key(s)") == 1


# ─────────────── WR-08: honest "accepted" list for [supamem] ────────────────


def test_supamem_table_warning_lists_real_flat_field_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pre-fix failure: "accepted: cache, classifier, eval, ..." — those are
    nested TABLE names, and a scalar at any of them is silently discarded."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    _write_project_cfg(tmp_path, "[supamem]\ntypo_key = 1\n")

    load_config(tmp_path)

    err = " ".join(capsys.readouterr().err.split())
    assert "typo_key" in err, err
    # Real flat ResolvedConfig field names must be offered.
    assert "collection" in err, err
    assert "qdrant_url" in err, err
    # "accepted" must appear exactly once — it previously carried two
    # different meanings in one sentence.
    assert err.count("accepted") == 1, err
    # Table names are labelled as tables, not as accepted flat keys.
    assert "nested tables:" in err, err


def test_nested_table_names_are_not_offered_as_flat_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A user must not be able to read a table name out of the accepted-flat
    list. ``eval`` may appear only inside the "nested tables:" clause."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    _write_project_cfg(tmp_path, "[supamem]\ntypo_key = 1\n")

    load_config(tmp_path)

    err = " ".join(capsys.readouterr().err.split())
    accepted_clause = err.split("accepted:", 1)[1].split("(nested tables:", 1)[0]
    for table in ("eval", "hook", "cache", "recency", "temporal", "transcript"):
        assert f" {table}," not in accepted_clause, (
            f"table name {table!r} offered as an accepted flat key: {accepted_clause!r}"
        )
