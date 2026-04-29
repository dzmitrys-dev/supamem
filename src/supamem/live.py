"""``supamem live`` — Rich-Live terminal dashboard tailing the audit JSONL.

Why this exists (v0.1.4 visibility design):
- PreToolUse hook injections are silent by design (they save tokens by NOT
  rendering as user-visible UI).
- The SessionStart banner gives session-level visibility but doesn't show
  per-call activity.
- ``supamem live`` is the on-demand observability surface: run it in a
  side terminal alongside Claude Code / Cursor / OpenCode and watch every
  retrieval call as it happens.

Implementation:
- Reads the JSONL audit log written by ``supamem.stats.counter``
- Uses ``watchfiles.awatch`` for OS-native file change notifications
  (falls back to polling if watchfiles isn't installed)
- Rich ``Live`` re-renders a fixed-size table; rotation and resize handled
- Pipe-safe: when ``not stdout.isatty()``, prints plain JSONL lines instead
  of TTY chrome
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("supamem.live")

DEFAULT_AUDIT_PATH_FALLBACK = Path.home() / ".cache" / "supamem" / "audit.jsonl"
RING_BUFFER_SIZE = 50
POLL_INTERVAL_SEC = 0.5
INITIAL_TAIL_LINES = 20


# ── Audit-file path resolution ──────────────────────────────────────────────


def _resolve_audit_path(override: Path | None = None) -> Path:
    """Pick the audit JSONL path: override → platformdirs cache dir → fallback."""
    if override is not None:
        return override
    try:
        import platformdirs

        return Path(platformdirs.user_cache_dir("supamem")) / "audit.jsonl"
    except Exception:
        return DEFAULT_AUDIT_PATH_FALLBACK


# ── JSONL parsing (one record per line; robust to partial / malformed) ──────


def _parse_record(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


def _format_row(rec: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Project an audit record into 6 display columns."""
    ts = rec.get("ts") or rec.get("timestamp") or ""
    if isinstance(ts, (int, float)):
        ts = time.strftime("%H:%M:%S", time.localtime(ts))
    elif isinstance(ts, str) and "T" in ts:
        # ISO 8601 → HH:MM:SS
        ts = ts.split("T", 1)[1].split(".", 1)[0].rstrip("Z")
    kind = str(rec.get("kind", "?"))
    source = str(rec.get("source", "-"))
    outcome = str(rec.get("outcome", "-"))
    tokens = rec.get("injected_tokens") or rec.get("tokens") or 0
    elapsed = rec.get("elapsed_ms")
    elapsed_str = f"{float(elapsed):.1f}ms" if elapsed is not None else "-"
    return ts, kind, source, outcome, str(tokens), elapsed_str


# ── Plain-text fallback (piped output) ──────────────────────────────────────


async def _run_plain(audit_path: Path) -> int:
    """When stdout isn't a TTY, just tail the file and print parsed records."""
    seen_size = 0
    if audit_path.exists():
        seen_size = audit_path.stat().st_size
    while True:
        try:
            if audit_path.exists():
                cur_size = audit_path.stat().st_size
                if cur_size < seen_size:
                    seen_size = 0  # rotation
                if cur_size > seen_size:
                    with audit_path.open("r", encoding="utf-8") as f:
                        f.seek(seen_size)
                        for line in f:
                            rec = _parse_record(line)
                            if rec:
                                print(json.dumps(rec, separators=(",", ":")))
                    seen_size = cur_size
            await asyncio.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            log.warning("plain tail loop: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SEC * 2)


# ── Rich Live dashboard ────────────────────────────────────────────────────


def _build_table(records: deque[dict[str, Any]], audit_path: Path) -> Any:
    """Build a Rich Table snapshot from the ring buffer."""
    from rich.table import Table

    table = Table(
        title=f"🧠 supamem live · {audit_path}",
        title_style="supamem.brand",
        show_lines=False,
        expand=True,
    )
    table.add_column("time", style="supamem.muted", no_wrap=True)
    table.add_column("kind", style="supamem.accent", no_wrap=True)
    table.add_column("source", no_wrap=True)
    table.add_column("outcome", no_wrap=True)
    table.add_column("tokens", justify="right", no_wrap=True)
    table.add_column("p_ms", justify="right", style="supamem.muted", no_wrap=True)
    if not records:
        table.add_row("--", "(waiting for activity)", "", "", "", "")
    else:
        for rec in records:
            row = _format_row(rec)
            outcome_style = (
                "supamem.ok" if row[3] == "injected"
                else "supamem.warn" if row[3] == "no_match"
                else "supamem.err" if row[3] == "error"
                else None
            )
            table.add_row(*row, style=outcome_style)
    return table


async def _run_dashboard(audit_path: Path) -> int:
    """The TTY path: Rich Live + watchfiles (or poll fallback)."""
    from rich.console import Console
    from rich.live import Live

    from supamem.console import THEME

    records: deque[dict[str, Any]] = deque(maxlen=RING_BUFFER_SIZE)

    # Seed with the last N lines from the file if it exists
    if audit_path.exists():
        try:
            with audit_path.open("r", encoding="utf-8") as f:
                tail = f.readlines()[-INITIAL_TAIL_LINES:]
            for line in tail:
                rec = _parse_record(line)
                if rec:
                    records.append(rec)
        except Exception as exc:  # noqa: BLE001
            log.debug("seed read failed: %s", exc)

    console = Console(theme=THEME)

    try:
        from watchfiles import awatch
    except ImportError:
        awatch = None  # type: ignore[assignment]

    seen_size = audit_path.stat().st_size if audit_path.exists() else 0

    def _read_appended() -> None:
        nonlocal seen_size
        if not audit_path.exists():
            seen_size = 0
            return
        cur_size = audit_path.stat().st_size
        if cur_size < seen_size:
            # rotation — re-seed from start
            seen_size = 0
        if cur_size > seen_size:
            with audit_path.open("r", encoding="utf-8") as f:
                f.seek(seen_size)
                for line in f:
                    rec = _parse_record(line)
                    if rec:
                        records.append(rec)
            seen_size = cur_size

    with Live(
        _build_table(records, audit_path),
        console=console,
        refresh_per_second=4,
        screen=False,
        vertical_overflow="visible",
    ) as live:
        try:
            if awatch is not None:
                async for _changes in awatch(
                    str(audit_path.parent),
                    poll_delay_ms=500,
                    stop_event=None,
                ):
                    _read_appended()
                    live.update(_build_table(records, audit_path))
            else:
                # Pure poll fallback
                while True:
                    _read_appended()
                    live.update(_build_table(records, audit_path))
                    await asyncio.sleep(POLL_INTERVAL_SEC)
        except (asyncio.CancelledError, KeyboardInterrupt):
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("live dashboard error: %s", exc)
            return 1
    return 0


# ── Entry point ─────────────────────────────────────────────────────────────


def run_live(audit_path: Path | None = None) -> int:
    """Sync entry called from CLI. Picks TTY vs plain fallback automatically."""
    path = _resolve_audit_path(audit_path)
    is_tty = False
    try:
        is_tty = sys.stdout.isatty()
    except Exception:
        is_tty = False
    coro = _run_dashboard(path) if is_tty else _run_plain(path)
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        return 0


__all__ = [
    "run_live",
    "RING_BUFFER_SIZE",
    "INITIAL_TAIL_LINES",
    "POLL_INTERVAL_SEC",
]
