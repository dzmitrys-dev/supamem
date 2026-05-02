"""Pure-function patcher kernel for Claude Code agent frontmatter (Phase 08.1).

This module is **pure**: NO filesystem I/O, NO logging, NO stdout writes. It
takes a markdown agent file's text in and returns transformed text + manifest
metadata out. The I/O wrapper (`scan_agent_dirs`, `patch_all`, etc.) lives in
Plan 03 and is the only layer allowed to touch disk.

Design references:

- D-COVER-01..03: lenient coverage (any of `mcp__*`, `mcp__supamem__*`, or a
  literal `mcp__supamem__<tool>` counts as already-covered) and end-to-end
  idempotency (running the patcher twice MUST produce zero modifications on
  the second run).
- D-YAML-01..04: ruamel.yaml round-trip mode (`typ='rt'`) is the parser; both
  CSV (`tools: A, B, C`) and block-list (`tools:\n  - A\n  - B`) styles are
  preserved verbatim on append; flow-style sequences (`[A, B]`) are skipped
  per D-YAML-04 because round-trip mutation does not reliably preserve them.
- D-UNDO-03: edit-detection uses SHA-256 of ONLY the frontmatter byte range
  (between leading and trailing `---` delimiters) with `\\r\\n -> \\n`
  newline normalization. Hashing the whole file would over-trigger "user
  edited" warnings on prose-body tweaks.
- Q1 (RESEARCH): `YAML(typ='rt')` is the canonical round-trip preserver.
- Q2 (RESEARCH): non-canonical wildcard forms (`mcp__supamem` without `__*`,
  `mcp__server.*` with single dot) MUST NOT count as covered — `_is_covered`
  rejects them so a misleading literal cannot suppress patching.
- Q4 (RESEARCH): CRLF normalization happens at the SHA layer, not at parse
  time, because we want the SHA stable across line-ending preferences.
- P1..P9 (RESEARCH): pitfall guards (200-line frontmatter scan limit; quote
  stripping in tokenization; flow-style detection via `value.fa.flow_style()`).

Public API used by Plan 03:

    detect_tools_state(text) -> DetectedState
    patch_yaml(text)        -> tuple[str, ManifestFragment | None]
    frontmatter_block(text) -> str | None
    block_sha256(text)      -> str   # hex digest of frontmatter bytes (CRLF-normalized)

Threat model (T-08.1.02-01..04):

- Round-trip mode rejects `!!python/object` tags by default — no arbitrary
  code execution from malformed YAML input.
- Only `YAMLError` is caught in `detect_tools_state`; other exceptions
  propagate so unrelated bugs are not silently swallowed at this layer.
"""
from __future__ import annotations

import hashlib
import re
from io import StringIO
from typing import Literal, Optional, TypedDict

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.error import YAMLError

DetectedState = Literal[
    "covered",
    "inheritance",
    "patchable_csv",
    "patchable_list",
    "skipped:flow",
    "skipped:malformed",
    "skipped:no-frontmatter",
]


class ManifestFragment(TypedDict):
    """Per-file patch record persisted by Plan 03's manifest writer."""

    original_frontmatter: str
    original_frontmatter_sha256: str
    patched_frontmatter_sha256: str
    tools_form: Literal["csv", "block-list"]


_FRONTMATTER_DELIM = "---"
_FRONTMATTER_SCAN_LINES = 200  # P8: bound the regex to avoid pathological scans
_SUPAMEM_TOKEN = "mcp__supamem__*"

# Module-level YAML instance — round-trip preserves CSV scalars, block-list
# style, comments, and quoting (P5: preserve_quotes keeps `'a'` from becoming
# `a` after a load/dump cycle). Default ruamel block-sequence indent is 0
# (`- item` flush with parent), but Claude Code agents follow the prevailing
# YAML convention of 2-space indented siblings (`  - item`); set indent
# accordingly so dumped output matches the user's existing style (D-YAML-03).
_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

# Regex for the leading frontmatter block. Matches `---\n<body>\n---\n` only
# at absolute file start (P2: frontmatter `---` is a markdown convention,
# distinct from YAML doc separators that can appear mid-file).
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# Tools-line regex (multiline) used by the CSV patch path to substitute the
# whole `tools: ...` line in place while preserving leading indentation.
_TOOLS_LINE_RE = re.compile(r"^(?P<prefix>tools:[ \t]*)(?P<value>.*)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# frontmatter_block + block_sha256
# ---------------------------------------------------------------------------


def frontmatter_block(text: str) -> Optional[str]:
    """Return the frontmatter block (delimiters + body) or None.

    The returned string includes the leading `---\\n` and trailing `---\\n`
    so callers can splice it back unchanged. Scan is bounded to the first
    ``_FRONTMATTER_SCAN_LINES`` lines (P8 sanity guard).
    """
    head = "\n".join(text.splitlines(keepends=False)[:_FRONTMATTER_SCAN_LINES])
    if not head.endswith("\n"):
        head += "\n"
    match = _FRONTMATTER_RE.match(head)
    if match is None:
        return None
    return match.group(0)


def block_sha256(text: str) -> str:
    """SHA-256 hex of the frontmatter block, CRLF-normalized.

    Per D-UNDO-03 / Q4: hash ONLY the frontmatter bytes (so prose-body edits
    don't trigger "user edited since patch" warnings) and normalize line
    endings so CRLF/LF differences don't change the digest.

    Returns the empty-bytes SHA when there is no frontmatter — this gives a
    deterministic value rather than raising, so callers can hash unchanged.
    """
    block = frontmatter_block(text) or ""
    normalized = block.replace("\r\n", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


# ---------------------------------------------------------------------------
# Coverage detection
# ---------------------------------------------------------------------------


def _strip_token(token: str) -> str:
    """Strip whitespace and surrounding single/double quotes (P5)."""
    t = token.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1].strip()
    return t


def _is_covered(tokens: list[str]) -> bool:
    """Lenient coverage per D-COVER-01.

    Returns True iff any token is exactly `mcp__*`, exactly `mcp__supamem__*`,
    or a `mcp__supamem__<literal>` (literal must be non-empty, not just `*`).

    Rejects (Q2 risk note):
      - `mcp__supamem` (no `__*` suffix) — incomplete form
      - any token containing `*.` substring — single-dot server pattern
        (`mcp__server.*`) does not match Claude Code's wildcard semantics
    """
    for raw in tokens:
        tok = _strip_token(raw)
        if not tok:
            continue
        # Q2 rejects: non-canonical forms must NOT count as covered
        if tok == "mcp__supamem":
            continue
        if "*." in tok:
            continue
        if tok == "mcp__*":
            return True
        if tok == "mcp__supamem__*":
            return True
        if tok.startswith("mcp__supamem__") and len(tok) > len("mcp__supamem__"):
            # Literal supamem tool (e.g. mcp__supamem__qdrant_find).
            return True
    return False


def _is_flow_style(value: object) -> bool:
    """True if a ruamel CommentedSeq is in flow style (`[a, b]`).

    Per D-YAML-04 we cannot reliably round-trip mutate flow sequences without
    reformatting (P1 verification confirmed this), so they are skipped.
    """
    if not isinstance(value, CommentedSeq):
        return False
    fa = getattr(value, "fa", None)
    if fa is None:
        return False
    flow = fa.flow_style()
    return bool(flow)


def detect_tools_state(text: str) -> DetectedState:
    """Classify an agent file's frontmatter into one of the DetectedState values.

    See module docstring for the full state machine. Catches only YAMLError;
    other exceptions propagate so unrelated bugs surface (D-FAIL-01 swallow
    happens at the I/O wrapper layer in Plan 03, not here).
    """
    block = frontmatter_block(text)
    if block is None:
        return "skipped:no-frontmatter"

    # Strip the leading and trailing `---` fences before handing to ruamel —
    # markdown frontmatter delimiters are not YAML doc separators (P2).
    inner = block
    if inner.startswith("---\n"):
        inner = inner[len("---\n"):]
    if inner.endswith("\n---\n"):
        inner = inner[: -len("\n---\n")]
    elif inner.endswith("---\n"):
        inner = inner[: -len("---\n")]

    try:
        data = _yaml.load(inner)
    except YAMLError:
        return "skipped:malformed"

    # Empty document or non-mapping root: treat as inheritance.
    if data is None or not hasattr(data, "get"):
        return "inheritance"

    if "tools" not in data:
        return "inheritance"

    tools_value = data["tools"]
    if tools_value is None:
        return "inheritance"

    # Empty string or empty sequence -> inheritance.
    if isinstance(tools_value, str) and tools_value.strip() == "":
        return "inheritance"

    if isinstance(tools_value, CommentedSeq):
        if _is_flow_style(tools_value):
            return "skipped:flow"
        if len(tools_value) == 0:
            return "inheritance"
        tokens = [_strip_token(str(item)) for item in tools_value]
        return "covered" if _is_covered(tokens) else "patchable_list"

    if isinstance(tools_value, str):
        tokens = [_strip_token(t) for t in tools_value.split(",")]
        return "covered" if _is_covered(tokens) else "patchable_csv"

    # Unknown shape (number, mapping, etc.) — treat as inheritance to be safe;
    # Plan 03 will surface this as a doctor warning if needed.
    return "inheritance"


# ---------------------------------------------------------------------------
# patch_yaml (Task 2 — implemented in this same module)
# ---------------------------------------------------------------------------


def _patch_csv_line(block: str) -> tuple[str, bool]:
    """Append `mcp__supamem__*` to the `tools:` CSV value within a frontmatter block.

    Returns (new_block, had_spaces). Spacing detection (P3): if any token had a
    leading space prior to strip, we use ``", "`` as the join separator;
    otherwise we use ``","`` so a `Read,Bash` whitelist becomes
    `Read,Bash,mcp__supamem__*` (no fabricated spaces).

    Inline comments on the tools line (e.g. ``tools: Read, Bash  # note``) are
    preserved by appending the new token before the comment marker.
    """
    match = _TOOLS_LINE_RE.search(block)
    if match is None:
        # detect_tools_state should have ruled this out; defensive fallthrough.
        raise RuntimeError("patch_csv_line called on block without tools: line")

    value_part = match.group("value")

    # Split off any inline comment so we don't mangle it. ruamel preserves the
    # comment when round-tripping, but here we operate textually to keep
    # whitespace exactly as the user wrote it (D-YAML-03).
    comment = ""
    if "#" in value_part:
        # Be conservative: only treat ``#`` as a comment when preceded by
        # whitespace; tokens themselves should not legitimately contain ``#``
        # in a Claude Code tools whitelist.
        hash_idx = value_part.find("#")
        # Walk back to find any whitespace gap.
        while hash_idx > 0 and value_part[hash_idx - 1] in (" ", "\t"):
            hash_idx -= 1
        comment = value_part[hash_idx:]
        value_part = value_part[:hash_idx]

    raw_split = value_part.split(",")
    had_spaces = any(t and t[0] in (" ", "\t") for t in raw_split[1:])
    tokens = [_strip_token(t) for t in raw_split if _strip_token(t)]
    tokens.append(_SUPAMEM_TOKEN)
    joiner = ", " if had_spaces else ","
    new_value = joiner.join(tokens)

    # Reassemble the line, preserving any inline comment (with its leading
    # whitespace) on the right-hand side.
    new_line = match.group("prefix") + new_value
    if comment:
        # Ensure at least one space before the comment if the original had one.
        if not new_line.endswith((" ", "\t")) and comment[0] not in (" ", "\t"):
            new_line += "  "
        new_line += comment

    new_block = block[: match.start()] + new_line + block[match.end():]
    return new_block, had_spaces


def _patch_block_list(block: str) -> str:
    """Append a list item via ruamel round-trip on the frontmatter block.

    Round-trip preserves comments and indentation (P1 verified). Re-emit the
    body and rewrap with `---\\n` / `---\\n` markers since ruamel doesn't
    emit them — markdown frontmatter is not a YAML document separator.
    """
    # Strip the leading and trailing fence to get the raw YAML body.
    inner = block
    if inner.startswith("---\n"):
        inner = inner[len("---\n"):]
    if inner.endswith("\n---\n"):
        inner = inner[: -len("\n---\n")]
    elif inner.endswith("---\n"):
        inner = inner[: -len("---\n")]

    data = _yaml.load(inner)
    tools = data["tools"]
    if not isinstance(tools, CommentedSeq):
        raise RuntimeError("patch_block_list called on non-sequence tools")
    tools.append(_SUPAMEM_TOKEN)

    out = StringIO()
    _yaml.dump(data, out)
    rendered = out.getvalue()
    if not rendered.endswith("\n"):
        rendered += "\n"
    return f"---\n{rendered}---\n"


def patch_yaml(text: str) -> tuple[str, Optional[ManifestFragment]]:
    """Pure patcher entrypoint.

    Returns ``(new_text, fragment)``. For non-patchable states, returns
    ``(text, None)`` unchanged. End-to-end idempotency (D-COVER-03): feeding
    the output back into ``patch_yaml`` MUST detect ``"covered"`` and produce
    ``(unchanged_text, None)``.
    """
    state = detect_tools_state(text)
    if state not in ("patchable_csv", "patchable_list"):
        return text, None

    original_block = frontmatter_block(text)
    if original_block is None:
        # Defensive: the state machine should have prevented this branch.
        raise RuntimeError(f"patch_yaml: state={state} but no frontmatter block")

    original_sha = block_sha256(text)

    if state == "patchable_csv":
        new_block, _ = _patch_csv_line(original_block)
        tools_form: Literal["csv", "block-list"] = "csv"
    else:  # patchable_list
        new_block = _patch_block_list(original_block)
        tools_form = "block-list"

    new_text = new_block + text[len(original_block):]
    patched_sha = block_sha256(new_text)

    fragment: ManifestFragment = {
        "original_frontmatter": original_block,
        "original_frontmatter_sha256": original_sha,
        "patched_frontmatter_sha256": patched_sha,
        "tools_form": tools_form,
    }
    return new_text, fragment
