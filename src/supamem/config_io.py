"""Atomic config-write primitives for supamem installers and migrators.

Public API:
- ``atomic_write_json`` — crash-safe JSON write with .bak snapshot and dry-run.
- ``deep_merge_json`` — recursive dict/list merge with explicit replace marker.
- ``wrap_managed_block`` / ``extract_managed_block`` — fence-marker primitives
  for embedding supamem-owned regions inside line-oriented user-edited files.
- ``sweep_managed_blocks`` — heal text carrying duplicate managed blocks
  (SM-4 accumulation) or unpaired marker lines into at most one canonical
  block; byte-level no-op on healthy input. Keeps ``extract_managed_block``
  strict (raises on >1 complete block).
- ``MANAGED_FENCE_RE`` / ``MANAGED_BEGIN_RE`` / ``MANAGED_END_RE`` plus the
  ``count_managed_blocks`` / ``managed_block_versions`` / ``count_orphan_markers``
  helpers — the SINGLE source of truth for the marker grammar. ``doctor`` and
  the installers all read it from here so a "duplicate blocks" report can
  never describe a state the healer does not recognize.
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
    return isinstance(value, dict) and value.get(_REPLACE_MARKER) is True and "value" in value


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
            raise BackupNotWritten(f"Could not write backup to {backup_path}: {exc}") from exc

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

# PEP 440 versions can include alpha/beta/rc/dev suffixes (e.g. 0.2.5a1,
# 1.0.0rc2, 2.1.dev0), so the marker version-token char-class must accept
# letters, digits, dots, plus, minus, and underscore — not just `[\d\.]+`.
# Anchor with leading `v` to keep the marker unambiguous.
_VERSION_TOKEN = r"[\w\.\+\-]+"

# ── ONE source of truth for "what is a managed-block marker?" (CR-01/CR-03) ──
#
# These three patterns are the ONLY place the marker grammar is defined. Every
# consumer — the installers, the healer, and ``supamem doctor`` — imports from
# here. Before 19.1 there were three independent regexes (a fence-pair one and
# a bare-BEGIN one here, plus a third, looser one in ``doctor``) behind comments
# asserting a parity they did not have; ``doctor`` demanded repairs that
# ``repair`` could not perform and the installers raised on states the healer
# could not heal. Parity is now by construction, not by comment.
#
# All three are LINE-ANCHORED: a marker is a marker only when it owns its whole
# line at column 0. A file that merely *documents* the marker — backticked in a
# sentence, indented inside a code sample — is user prose, not a fence. This
# repository's own CLAUDE.md documents it, and used to trip every verb.
#
# Anchoring against `\n` alone is sufficient: this text always arrives via
# ``Path.read_text()``, which applies universal-newline translation, so a CRLF
# file is already `\n`-only by the time it reaches us.
_BEGIN_LINE = rf"# BEGIN SUPAMEM v{_VERSION_TOKEN} MANAGED BLOCK — DO NOT EDIT"
_END_LINE = rf"# END SUPAMEM v{_VERSION_TOKEN} MANAGED BLOCK"

MANAGED_BEGIN_RE = re.compile(
    rf"^# BEGIN SUPAMEM v(?P<version>{_VERSION_TOKEN}) MANAGED BLOCK — DO NOT EDIT$",
    re.MULTILINE,
)
MANAGED_END_RE = re.compile(
    rf"^# END SUPAMEM v(?P<version>{_VERSION_TOKEN}) MANAGED BLOCK$",
    re.MULTILINE,
)
MANAGED_FENCE_RE = re.compile(
    rf"^# BEGIN SUPAMEM v(?P<version>{_VERSION_TOKEN}) MANAGED BLOCK — DO NOT EDIT\n"
    # `owned` may never swallow another BEGIN marker line: a fence pair is the
    # SMALLEST well-formed region. Without this tempered guard, a stale half-
    # fence sitting above a healthy block got absorbed into the pair, so the
    # pair reported the *stale* version — permanent drift that no `install`
    # could clear, because the import line was already present inside `owned`
    # and the writer therefore short-circuited as "already correct".
    rf"(?P<owned>(?:(?!^{_BEGIN_LINE}$).)*?)"
    rf"\n^{_END_LINE}$",
    re.DOTALL | re.MULTILINE,
)


def wrap_managed_block(content: str, version: str = __version__) -> str:
    """Wrap ``content`` between BEGIN/END SUPAMEM fence markers."""
    return f"{_BEGIN_FMT.format(version=version)}\n{content}\n{_END_FMT.format(version=version)}"


def count_managed_blocks(text: str) -> int:
    """Number of COMPLETE BEGIN/END managed-block fence pairs in ``text``.

    This is the canonical "how many managed blocks does this file have?"
    primitive. Counting complete pairs — never bare BEGIN mentions — is what
    keeps ``extract_managed_block``'s raise and ``sweep_managed_blocks``'
    healing talking about the same thing.
    """
    return sum(1 for _ in MANAGED_FENCE_RE.finditer(text))


def managed_block_versions(text: str) -> list[str]:
    """Fence version tokens, in document order, one per complete pair."""
    return [m.group("version") for m in MANAGED_FENCE_RE.finditer(text)]


def _orphan_marker_spans(text: str, pairs: list[re.Match[str]]) -> list[tuple[int, int]]:
    """Line spans of BEGIN/END marker lines NOT part of a complete fence pair.

    Each span covers the whole marker line including its trailing newline, so
    deleting it leaves no blank residue. Returned sorted by start offset.
    """
    pair_spans = [(m.start(), m.end()) for m in pairs]
    spans: list[tuple[int, int]] = []
    for pattern in (MANAGED_BEGIN_RE, MANAGED_END_RE):
        for match in pattern.finditer(text):
            start = match.start()
            if any(lo <= start < hi for lo, hi in pair_spans):
                continue  # this marker is one half of a well-formed fence
            end = match.end()
            if text[end : end + 1] == "\n":
                end += 1
            spans.append((start, end))
    return sorted(spans)


def count_orphan_markers(text: str) -> int:
    """Number of unpaired BEGIN/END marker lines — the malformed-fence state.

    Distinct from ``count_managed_blocks``: a file can hold one healthy block
    AND a leftover half-fence from a hand-edit. Callers (``supamem doctor``)
    must report the two states separately, because they read differently to a
    user even though ``sweep_managed_blocks`` heals both.
    """
    return len(_orphan_marker_spans(text, list(MANAGED_FENCE_RE.finditer(text))))


def extract_managed_block(text: str) -> tuple[str, str, str]:
    """Split ``text`` into ``(before, owned, after)`` around the fence pair.

    Raises ``ValueError`` when more than one COMPLETE fence pair is present —
    the duplicate-accumulation state that ``sweep_managed_blocks`` heals.

    The count is deliberately of complete *pairs*, not of bare BEGIN mentions
    (CR-01). Counting mentions made the raise reachable from states the healer
    could not normalize — a prose mention of the marker, or a hand-mangled END
    fence — so install / uninstall / repair all died with an unhandled
    ``ValueError`` and no recovery path. An unpaired marker is a
    malformed-fence problem for ``sweep_managed_blocks``, never a reason to
    abort the installer.
    """
    if count_managed_blocks(text) > 1:
        raise ValueError("multiple SUPAMEM managed blocks found in text")
    match = MANAGED_FENCE_RE.search(text)
    if match is None:
        return (text, "", "")
    before = text[: match.start()]
    owned = match.group("owned")
    after = text[match.end() :]
    return (before, owned, after)


def sweep_managed_blocks(text: str, version: str = __version__) -> tuple[str, int]:
    """Heal ``text`` so it carries at most one SUPAMEM managed block (SM-4).

    Two anomalies are normalized:

    1. **Duplicate fenced blocks** — accumulated across upgrades when older
       fence regexes could not see prerelease-versioned markers. Whole blocks
       are deduplicated (order-preserving, first occurrence wins) and the
       survivors concatenated into ONE canonical block via
       ``wrap_managed_block`` at ``version``, placed at the FIRST block's
       position. Dedup compares each block's ENTIRE owned string, so a block's
       internal structure — blank lines, ``---`` separators, repeated list
       items — is preserved (WR-06).
    2. **Unpaired BEGIN/END marker lines** — left behind when a user hand-edits
       or mangles a fence. The stray marker *line* is deleted; the surrounding
       text is untouched. Without this, a lone half-fence was permanent: it
       could never be paired again, so ``supamem doctor`` stayed red forever
       with an instruction ``repair`` could not carry out (CR-01/CR-03).

    Text outside the fence pairs — including lines between two duplicate
    blocks — is preserved verbatim.

    Returns ``(healed_text, removed_count)``, where ``removed_count`` is the
    number of anomalies healed (duplicate blocks swept plus orphan marker lines
    dropped). Healthy input — zero or one block and no orphans — is the common
    case and is returned byte-identical with count 0.

    INVARIANT (load-bearing, pinned by
    ``tests/test_managed_block_asymmetry.py::test_sweep_output_is_always_accepted_by_extract``):
    the returned text always holds at most one complete fence pair, so
    ``extract_managed_block`` can never raise on a swept result, and sweeping a
    swept result is a byte-stable fixed point. This is what makes ``repair`` a
    real remedy rather than a no-op.
    """
    matches = list(MANAGED_FENCE_RE.finditer(text))
    orphans = _orphan_marker_spans(text, matches)
    if len(matches) <= 1 and not orphans:
        return text, 0

    # (start, end, replacement) edits, applied left to right. Non-overlapping
    # by construction: orphan spans exclude anything inside a pair span.
    edits: list[tuple[int, int, str]] = []
    if len(matches) > 1:
        # WR-06: deduplicate whole BLOCKS, not individual lines. Line-level
        # dedup silently destroyed any legitimately repeated line inside the
        # managed region — blank lines (collapsing a multi-paragraph region
        # into one paragraph), markdown `---` separators, repeated list items,
        # and any line two otherwise-distinct blocks happened to share. Today
        # the region holds a single @import line so the blast radius was nil,
        # but this is a general-purpose primitive and the loss was silent.
        seen: set[str] = set()
        merged: list[str] = []
        for match in matches:
            owned = match.group("owned")
            if owned not in seen:
                seen.add(owned)
                merged.append(owned)
        canonical = wrap_managed_block("\n".join(merged), version=version)
        for index, match in enumerate(matches):
            edits.append((match.start(), match.end(), canonical if index == 0 else ""))
    edits.extend((start, end, "") for start, end in orphans)
    edits.sort()

    parts: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        parts.append(text[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), max(len(matches) - 1, 0) + len(orphans)


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
