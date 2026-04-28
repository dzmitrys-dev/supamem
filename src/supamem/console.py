"""Shared Rich console + theme for all supamem CLI output.

A single ``Console`` instance owns the visual identity. Every CLI subcommand
imports ``console`` from here so the palette, spinners, panels, and tables
stay coherent across the whole tool. Honors ``NO_COLOR`` and non-TTY stdout
automatically (Rich detects and degrades gracefully).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.theme import Theme

THEME = Theme(
    {
        "supamem.brand": "bold magenta",
        "supamem.accent": "cyan",
        "supamem.muted": "dim",
        "supamem.ok": "bold green",
        "supamem.warn": "bold yellow",
        "supamem.err": "bold red",
        "supamem.info": "bold blue",
        "supamem.kbd": "reverse",
    }
)

console = Console(theme=THEME, highlight=False, soft_wrap=False)
err_console = Console(theme=THEME, stderr=True, highlight=False)


def banner(title: str, subtitle: str | None = None) -> None:
    body = f"[supamem.brand]{title}[/supamem.brand]"
    if subtitle:
        body += f"\n[supamem.muted]{subtitle}[/supamem.muted]"
    console.print(Panel.fit(body, border_style="supamem.accent", padding=(0, 2)))


def ok(msg: str) -> None:
    console.print(f"[supamem.ok]✓[/supamem.ok] {msg}")


def warn(msg: str) -> None:
    console.print(f"[supamem.warn]⚠[/supamem.warn] {msg}")


def err(msg: str) -> None:
    err_console.print(f"[supamem.err]✗[/supamem.err] {msg}")


def info(msg: str) -> None:
    console.print(f"[supamem.info]→[/supamem.info] {msg}")


def step(msg: str) -> None:
    console.print(f"  [supamem.muted]·[/supamem.muted] {msg}")


@contextmanager
def working(label: str) -> Iterator[Progress]:
    """Render a spinner + elapsed time while a long op runs.

    Usage::

        with working("Probing Qdrant…") as prog:
            prog.add_task("probe", total=None)
            do_work()
    """
    progress = Progress(
        SpinnerColumn(style="supamem.accent"),
        TextColumn("[supamem.muted]{task.description}[/supamem.muted]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        progress.add_task(label, total=None)
        yield progress


def status_table(title: str) -> Table:
    """Return a styled Table for `doctor`-style multi-row status output."""
    table = Table(
        title=f"[supamem.brand]{title}[/supamem.brand]",
        title_justify="left",
        border_style="supamem.accent",
        show_header=True,
        header_style="supamem.muted",
        expand=False,
    )
    table.add_column("Check", style="supamem.accent", no_wrap=True)
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Detail", style="supamem.muted", overflow="fold")
    return table


def status_cell(state: str) -> str:
    """Render a status cell — pass 'ok'/'warn'/'err'/'skip'."""
    return {
        "ok": "[supamem.ok]✓ ok[/supamem.ok]",
        "warn": "[supamem.warn]⚠ warn[/supamem.warn]",
        "err": "[supamem.err]✗ fail[/supamem.err]",
        "skip": "[supamem.muted]– skip[/supamem.muted]",
    }.get(state, state)


CREDIT_LINE = (
    "[supamem.muted]Delivered by[/supamem.muted] "
    "[supamem.brand]SoftChat[/supamem.brand][supamem.muted] · [/supamem.muted]"
    "[supamem.brand]SoftSkillz[/supamem.brand][supamem.muted] — "
    "https://app.softchat.ru · https://softskillz.ai[/supamem.muted]"
)
