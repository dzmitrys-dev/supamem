"""Claude Code session JSONL parser (Phase 06 — R-01).

Reads ``~/.claude/projects/<project>/<session>.jsonl`` line-by-line and yields
typed :class:`SessionEvent` dicts. Tolerates trailing-partial-line truncation
only (D-28: real Claude Code sessions truncate the last line on SIGKILL).
Unknown event types in non-last positions warn-and-skip (D-29 — Anthropic
adds event kinds without breaking existing ones); mid-file invalid JSON
raises (INGEST-05 fail-loud).

This module owns BOTH ``parse_jsonl(path)`` (filesystem) and
``parse_jsonl_text(text)`` (in-memory) — the latter is consumed by the
chunker in Plan 06-02 (resolves checker B4: chunker stays a pure consumer).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from supamem.console import err_console

from .types import SessionEvent

# Top-level (line-level) event types we recognize, per RESEARCH §R-01
# cross-verified against 4 third-party Claude Code parsers.
KNOWN_TYPES: set[str] = {
    "user",
    "assistant",
    "system",
    "summary",
    "file-history-snapshot",
    "queue-operation",
}


def _parse_lines(lines: list[str], *, label: str) -> Iterator[SessionEvent]:
    """Shared per-line parsing policy for ``parse_jsonl`` and ``parse_jsonl_text``.

    Failure policy (D-28 / D-29):
    - Blank / whitespace-only line → silently skip.
    - Last-line :class:`json.JSONDecodeError` → warn to ``err_console``, drop, continue.
    - Mid-file :class:`json.JSONDecodeError` → re-raise (INGEST-05 fail-loud).
    - Non-dict JSON value → :class:`ValueError`.
    - Unknown ``type`` value → warn to ``err_console``, skip line, continue.
    """
    n = len(lines)
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            if i == n - 1:
                err_console.print(
                    f"[yellow]warn[/]: {label}: trailing partial line dropped "
                    f"(line {i + 1}, likely active session SIGKILL truncation)"
                )
                continue
            raise  # mid-file → fail loudly per INGEST-05
        if not isinstance(obj, dict):
            raise ValueError(f"{label}:{i + 1}: line is not a JSON object")
        evt_type = obj.get("type")
        if evt_type not in KNOWN_TYPES:
            err_console.print(
                f"[yellow]warn[/]: {label}:{i + 1}: unknown event type "
                f"{evt_type!r}, skipping (forward-compat carve-out per D-29)"
            )
            continue
        yield obj  # type: ignore[misc]


def parse_jsonl(path: Path) -> Iterator[SessionEvent]:
    """Yield :class:`SessionEvent` dicts from a Claude Code session JSONL file.

    See :func:`_parse_lines` for the per-line failure policy.
    """
    with path.open(encoding="utf-8") as f:
        lines = f.readlines()
    yield from _parse_lines(lines, label=path.name)


def parse_jsonl_text(text: str, *, label: str = "<text>") -> Iterator[SessionEvent]:
    """Yield :class:`SessionEvent` dicts from an in-memory JSONL string.

    Consumed by ``chunk_transcript`` in Plan 06-02; keeps parser ownership
    here so the chunker stays a pure consumer (B4).

    See :func:`_parse_lines` for the per-line failure policy.
    """
    lines = text.splitlines()
    yield from _parse_lines(lines, label=label)
