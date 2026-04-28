"""Atomic config-write primitives for supamem installers and migrators.

Public API:
- ``atomic_write_json`` — crash-safe JSON write with .bak snapshot and dry-run.
- ``deep_merge_json`` — recursive dict/list merge with explicit replace marker.
- ``wrap_managed_block`` / ``extract_managed_block`` — fence-marker primitives
  for embedding supamem-owned regions inside line-oriented user-edited files.
- ``compute_diff`` — unified-diff helper for ``--dry-run`` callers.
- ``WriteResult`` (dataclass), ``BackupNotWritten`` (exception).

NOTE on diffs: ``compute_diff`` is intentionally low-level. Callers that print
the diff to stdout MUST sanitize secrets first — config files like
``~/.claude.json`` and ``~/.cursor/mcp.json`` may contain MCP-server env blocks
with API keys (see plan 80.6-02 STRIDE T-80.6-02-02).

NOTE on symlinks: ``atomic_write_json`` does not follow ``target.parent``
symlinks that escape ``$HOME`` — installers should resolve and validate the
path before calling.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from supamem import __version__


@dataclass
class WriteResult:
    written: bool
    backup_path: Optional[Path]
    diff: str


class BackupNotWritten(Exception):
    """Raised when a backup snapshot could not be written to disk."""


# ──────────────────────────── deep_merge_json ─────────────────────────────


_REPLACE_MARKER = "__supamem_replace__"


def _is_replace_marker(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get(_REPLACE_MARKER) is True
        and "value" in value
    )


def deep_merge_json(base: dict, overlay: dict) -> dict:
    """Recursively merge ``overlay`` into ``base``.

    - Dicts: merged key-wise.
    - Lists: overlay items NOT already in base (by deep equality) are appended.
    - Scalars: overlay wins.
    - Special replace marker: overlay value
      ``{"__supamem_replace__": True, "value": <X>}`` forces replacement of the
      base value with ``<X>`` instead of merging.
    """
    out = dict(base)
    for key, ov in overlay.items():
        if _is_replace_marker(ov):
            out[key] = ov["value"]
            continue
        if key not in out:
            out[key] = ov
            continue
        existing = out[key]
        if isinstance(existing, dict) and isinstance(ov, dict):
            out[key] = deep_merge_json(existing, ov)
        elif isinstance(existing, list) and isinstance(ov, list):
            merged = list(existing)
            for item in ov:
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        else:
            out[key] = ov
    return out


# ─────────────────────────── atomic_write_json ────────────────────────────


def _serialize(content: dict) -> str:
    """Serialize to canonical JSON. Raises TypeError before any disk write."""
    return json.dumps(content, indent=2, ensure_ascii=False, sort_keys=False)


def _read_existing(target: Path) -> str:
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


def atomic_write_json(
    target: Path,
    content: dict,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> WriteResult:
    """Atomically write ``content`` to ``target`` as pretty JSON.

    Behavior:
    1. Serialize first (raises TypeError on non-serializable input — nothing
       is written if this fails).
    2. If existing content equals new content, return a no-op WriteResult.
    3. Compute a unified diff for callers / dry-run reporting.
    4. If ``dry_run``: return without writing.
    5. Otherwise write ``<target>.bak.<unix_ns>`` (when ``backup=True`` and
       target exists), then a tempfile sibling, fsync, ``os.replace``.

    On replace failure the original target file is left untouched. The tempfile
    may remain on disk; callers can ignore or sweep ``*.tmp.*`` later.
    """
    new_text = _serialize(content)
    old_text = _read_existing(target)
    diff = compute_diff(
        old_text,
        new_text,
        fromfile=str(target),
        tofile=str(target),
    )

    if old_text == new_text:
        return WriteResult(written=False, backup_path=None, diff="")

    if dry_run:
        return WriteResult(written=False, backup_path=None, diff=diff)

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    backup_path: Optional[Path] = None
    if backup and target.exists():
        backup_path = target.with_suffix(target.suffix + f".bak.{time.time_ns()}")
        try:
            backup_path.write_text(old_text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover — surface clearly
            raise BackupNotWritten(
                f"Could not write backup to {backup_path}: {exc}"
            ) from exc

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(parent),
        prefix=target.name + ".tmp.",
        delete=False,
    )
    try:
        tmp.write(new_text)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()

    os.replace(tmp.name, target)

    return WriteResult(written=True, backup_path=backup_path, diff=diff)


# ──────────────────────────── managed-block fences ─────────────────────────


_BEGIN_FMT = "# BEGIN SUPAMEM v{version} MANAGED BLOCK — DO NOT EDIT"
_END_FMT = "# END SUPAMEM v{version} MANAGED BLOCK"
_FENCE_RE = re.compile(
    r"# BEGIN SUPAMEM v[\d\.]+ MANAGED BLOCK — DO NOT EDIT\n"
    r"(?P<owned>.*?)\n"
    r"# END SUPAMEM v[\d\.]+ MANAGED BLOCK",
    re.DOTALL,
)
_BEGIN_RE = re.compile(r"# BEGIN SUPAMEM v[\d\.]+ MANAGED BLOCK — DO NOT EDIT")


def wrap_managed_block(content: str, version: str = __version__) -> str:
    """Wrap ``content`` between BEGIN/END SUPAMEM fence markers."""
    return f"{_BEGIN_FMT.format(version=version)}\n{content}\n{_END_FMT.format(version=version)}"


def extract_managed_block(text: str) -> tuple[str, str, str]:
    """Split ``text`` into ``(before, owned, after)`` around the fence pair.

    Raises ``ValueError`` if multiple BEGIN markers are present.
    """
    if len(_BEGIN_RE.findall(text)) > 1:
        raise ValueError("multiple BEGIN SUPAMEM markers found in text")
    match = _FENCE_RE.search(text)
    if match is None:
        return (text, "", "")
    before = text[: match.start()]
    owned = match.group("owned")
    after = text[match.end():]
    return (before, owned, after)


# ──────────────────────────── compute_diff ─────────────────────────────────


def compute_diff(
    old: str,
    new: str,
    *,
    fromfile: str = "old",
    tofile: str = "new",
) -> str:
    """Unified diff between ``old`` and ``new`` strings."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )
