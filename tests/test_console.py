"""Smoke tests for the shared rich console module.

Verifies:
- Theme keys resolve (no KeyError on render)
- Helper fns produce non-empty styled output
- ``working`` context manager is a real Progress
- ``status_table`` returns a Table with the expected columns
- Console honors ``NO_COLOR`` (via env var) and non-TTY (via ``force_terminal=False``)
"""
from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from supamem import console as console_mod
from supamem.console import (
    CREDIT_LINE,
    THEME,
    banner,
    err,
    info,
    ok,
    status_cell,
    status_table,
    step,
    warn,
    working,
)


def _captured(fn, *args, **kwargs) -> str:
    """Run a console helper against a fresh non-color Console and return text."""
    buf = io.StringIO()
    fake = Console(theme=THEME, file=buf, force_terminal=False, no_color=True, width=120)
    monkey_console = console_mod.console
    monkey_err = console_mod.err_console
    console_mod.console = fake
    console_mod.err_console = fake
    try:
        fn(*args, **kwargs)
    finally:
        console_mod.console = monkey_console
        console_mod.err_console = monkey_err
    return buf.getvalue()


@pytest.mark.parametrize(
    "fn, msg, marker",
    [
        (ok, "all good", "all good"),
        (warn, "be careful", "be careful"),
        (err, "broken", "broken"),
        (info, "fyi", "fyi"),
        (step, "doing thing", "doing thing"),
    ],
)
def test_status_helpers_render(fn, msg, marker) -> None:
    out = _captured(fn, msg)
    assert marker in out


def test_banner_renders_title_and_subtitle() -> None:
    out = _captured(banner, "supamem", "v0.1.0")
    assert "supamem" in out
    assert "v0.1.0" in out


def test_credit_line_mentions_softchat_and_softskillz() -> None:
    assert "SoftChat" in CREDIT_LINE
    assert "SoftSkillz" in CREDIT_LINE
    assert "softchat.ru" in CREDIT_LINE
    assert "softskillz.ai" in CREDIT_LINE


def test_working_returns_progress() -> None:
    with working("probing") as prog:
        assert isinstance(prog, Progress)


def test_status_table_columns() -> None:
    tbl = status_table("Doctor")
    assert isinstance(tbl, Table)
    assert [c.header for c in tbl.columns] == ["Check", "Status", "Detail"]


@pytest.mark.parametrize(
    "state, expected",
    [
        ("ok", "ok"),
        ("warn", "warn"),
        ("err", "fail"),
        ("skip", "skip"),
    ],
)
def test_status_cell_states(state: str, expected: str) -> None:
    assert expected in status_cell(state)


def test_no_color_env_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich respects NO_COLOR — output should still render plain text."""
    monkeypatch.setenv("NO_COLOR", "1")
    out = _captured(ok, "no color")
    assert "no color" in out
