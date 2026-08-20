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
        inner = inner[len("---\n") :]
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

    new_block = block[: match.start()] + new_line + block[match.end() :]
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
        inner = inner[len("---\n") :]
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

    new_text = new_block + text[len(original_block) :]
    patched_sha = block_sha256(new_text)

    fragment: ManifestFragment = {
        "original_frontmatter": original_block,
        "original_frontmatter_sha256": original_sha,
        "patched_frontmatter_sha256": patched_sha,
        "tools_form": tools_form,
    }
    return new_text, fragment


# ---------------------------------------------------------------------------
# I/O layer (Plan 03) — manifest IO, filesystem walker, patch_all, unpatch_all
# ---------------------------------------------------------------------------
#
# Everything below this banner is the I/O wrapper. The pure kernel above MUST
# NOT import anything below; the kernel is unit-testable without disk access.
#
# Pattern references (all mirrored verbatim from Phase 8 rerankers):
#   - manifest_path() honors SUPAMEM_CACHE_DIR override (rerankers/__init__.py:79-88)
#   - save_manifest uses FileLock + temp-file-and-rename (rerankers:165-208)
#   - per-file failures swallowed via err_console (D-FAIL-01..04)
# Threat-model:
#   - T-08.1.03-01: project_dir traversal guard via is_relative_to(find_project_root())
#   - T-08.1.03-02: symlinks excluded with warning (P6)
#   - T-08.1.03-07: oversize files (>1 MiB) skipped with reason

import json  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from importlib.metadata import PackageNotFoundError, version as pkg_version  # noqa: E402
from pathlib import Path  # noqa: E402

import platformdirs  # noqa: E402
from filelock import FileLock, Timeout  # noqa: E402

from supamem.console import err_console, info  # noqa: E402

_MANIFEST_FILENAME = "agent_patches.json"
_MANIFEST_SCHEMA_VERSION = 1
_MANIFEST_LOCK_TIMEOUT_S = int(os.environ.get("SUPAMEM_MANIFEST_LOCK_TIMEOUT", "3600"))
_MAX_AGENT_FILE_BYTES = 1_048_576  # T-08.1.03-07 oversize guard
_VANISH_RETRY_DELAY_S = 0.05  # D-FAIL-04 retry-once after 50ms
_PERMISSION_RETRY_DELAY_S = 0.1  # P10 unpatch retry-once after 100ms


def _supamem_version() -> str:
    """Best-effort package version string. Falls back for editable/dev installs."""
    try:
        return pkg_version("supamem")
    except PackageNotFoundError:  # pragma: no cover — exercised only in dev sandboxes
        return "0.0.0+dev"


@dataclass
class PatchEntry:
    """Per-file outcome surfaced by patch_all (used by doctor in Plan 05)."""

    path: str
    scope: Literal["global", "project"]
    state: DetectedState
    relpath: str


@dataclass
class PatchSummary:
    patched: list[PatchEntry] = field(default_factory=list)
    covered: list[PatchEntry] = field(default_factory=list)
    inheritance: list[PatchEntry] = field(default_factory=list)
    skipped: list[tuple[PatchEntry, str]] = field(default_factory=list)
    would_patch: list[PatchEntry] = field(default_factory=list)
    manifest_path: Optional[Path] = None


@dataclass
class UnpatchSummary:
    restored: list[str] = field(default_factory=list)
    skipped_user_edited: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest IO
# ---------------------------------------------------------------------------


def manifest_path() -> Path:
    """Path to the rolling agent-patches manifest.

    Honors ``SUPAMEM_CACHE_DIR`` env override (mirrors
    ``rerankers._model_cache_dir``). Defaults to
    ``platformdirs.user_cache_dir("supamem")/agent_patches.json``.
    """
    override = os.environ.get("SUPAMEM_CACHE_DIR")
    if override:
        return Path(override) / _MANIFEST_FILENAME
    return Path(platformdirs.user_cache_dir("supamem")) / _MANIFEST_FILENAME


def _empty_manifest() -> dict:
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "supamem_version": _supamem_version(),
        "patches": [],
    }


def load_manifest() -> dict:
    """Read + JSON-parse the manifest; return empty template on any failure.

    Never raises — the manifest is purely informational and a corrupted file
    must not block ``supamem repair``. Forward-compat: if a future
    ``schema_version`` is encountered we emit a warning and return the
    template rather than silently corrupting the on-disk state.
    """
    path = manifest_path()
    if not path.is_file():
        return _empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        err_console.print(
            f"[supamem.warn]agent_patcher: manifest unreadable at {path}: {exc!r}; starting fresh"
        )
        return _empty_manifest()
    if not isinstance(data, dict):
        err_console.print(
            f"[supamem.warn]agent_patcher: manifest root is not an object at {path}; starting fresh"
        )
        return _empty_manifest()
    if data.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        err_console.print(
            f"[supamem.warn]agent_patcher: manifest schema_version="
            f"{data.get('schema_version')!r} (expected {_MANIFEST_SCHEMA_VERSION}); "
            f"starting fresh"
        )
        return _empty_manifest()
    if not isinstance(data.get("patches"), list):
        data["patches"] = []
    return data


def save_manifest(m: dict) -> None:
    """Atomically persist the manifest under a FileLock.

    Mirrors ``rerankers/__init__.py:165-208``: lock at
    ``<manifest_path>.lock`` with timeout governed by
    ``SUPAMEM_MANIFEST_LOCK_TIMEOUT`` (default 3600s; tests override). On
    timeout, raises ``RuntimeError`` after surfacing via err_console — same
    shape as the rerankers prepare path so callers can render a uniform
    message.
    """
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(path) + ".lock"
    try:
        with FileLock(lock_path, timeout=_MANIFEST_LOCK_TIMEOUT_S):
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(m, tmp, indent=2, sort_keys=False)
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()
            os.replace(tmp.name, str(path))
    except Timeout as exc:
        err_console.print(
            f"[supamem.err]agent_patches manifest lock timeout at {lock_path}. "
            f"Another supamem repair may be running. Retry shortly or delete "
            f"the stale lock if no install is active."
        )
        raise RuntimeError(f"supamem: manifest lock timeout at {path}") from exc


# ---------------------------------------------------------------------------
# Filesystem walker
# ---------------------------------------------------------------------------


def _default_global_dir() -> Path:
    return Path.home() / ".claude" / "agents"


def _default_project_dir() -> Optional[Path]:
    # Local import keeps the pure kernel free of supamem.config dependence.
    from supamem.config import find_project_root  # noqa: PLC0415

    root = find_project_root()
    if root is None:
        return None
    return root / ".claude" / "agents"


def _project_root_for(project_dir: Path, *, explicit: bool) -> Optional[Path]:
    """Resolve the canonical project root for a candidate project_dir.

    Used as the traversal-guard reference (T-08.1.03-01).

    Anchoring strategy:
      - When ``project_dir`` was injected explicitly by the caller (tests,
        future API surface), derive the project root structurally from the
        path itself (``<root>/.claude/agents`` -> ``<root>``). This trusts
        the caller's intent: they pointed us at a tree, the guard's job is
        only to reject ``project_dir`` paths that escape that tree (e.g. via
        ``..`` segments).
      - When ``project_dir`` was discovered via ``find_project_root()`` (the
        default), anchor against that same discovery — symmetric, defensive.
    """
    if explicit:
        parents = list(project_dir.parents)
        # Need at least <agents>/.claude/<root>; project_dir itself is
        # parents[-1]'s child, so parents[1] is <root> when layout matches.
        if len(parents) >= 2:
            return parents[1].resolve()
        return None

    from supamem.config import find_project_root  # noqa: PLC0415

    discovered = find_project_root()
    if discovered is not None:
        return discovered.resolve()
    return None


def scan_agent_dirs(
    global_dir: Optional[Path] = None,
    project_dir: Optional[Path] = None,
) -> list[tuple[Path, Literal["global", "project"]]]:
    """Enumerate ``*.md`` agent files in both global and project scopes.

    Defaults: ``global_dir = ~/.claude/agents``,
    ``project_dir = <find_project_root()>/.claude/agents`` (if discoverable).

    Symlinks are EXCLUDED with a stderr warning (P6 / T-08.1.03-02). Project
    scope is guarded against path traversal (T-08.1.03-01): the resolved
    ``project_dir`` MUST be inside the resolved project root, otherwise the
    whole project scope is dropped with a warning.
    """
    project_dir_explicit = project_dir is not None
    if global_dir is None:
        global_dir = _default_global_dir()
    if project_dir is None:
        project_dir = _default_project_dir()

    out: list[tuple[Path, Literal["global", "project"]]] = []

    candidates: list[tuple[Optional[Path], Literal["global", "project"]]] = [
        (global_dir, "global"),
        (project_dir, "project"),
    ]

    for candidate_dir, scope in candidates:
        if candidate_dir is None:
            continue
        if not candidate_dir.exists() or not candidate_dir.is_dir():
            continue

        # T-08.1.03-01 traversal guard for project scope only — global scope
        # is anchored at Path.home() and trusted by construction.
        if scope == "project":
            try:
                resolved_dir = candidate_dir.resolve()
            except OSError:
                err_console.print(
                    f"[supamem.warn]agent_patcher: cannot resolve project agent dir "
                    f"{candidate_dir}; skipping"
                )
                continue
            project_root = _project_root_for(candidate_dir, explicit=project_dir_explicit)
            if project_root is None or not resolved_dir.is_relative_to(project_root):
                err_console.print(
                    f"[supamem.warn]agent_patcher: project agent dir {resolved_dir} "
                    f"is outside project root; skipping (path-traversal guard)"
                )
                continue

        for md in sorted(candidate_dir.glob("*.md")):
            if md.is_symlink():
                try:
                    target = md.resolve()
                except OSError:
                    target = md
                err_console.print(f"[supamem.warn]agent_patcher: skipped symlink: {md} -> {target}")
                continue
            if not md.is_file():
                continue
            out.append((md, scope))

    return out


# ---------------------------------------------------------------------------
# Atomic file write + retry helpers
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via temp-file-and-rename.

    Uses ``newline=""`` so the kernel-emitted line endings (always ``\\n``)
    survive verbatim — Python's text-mode default would translate ``\\n``
    to the platform separator on Windows, which would silently change the
    bytes on disk and invalidate the manifest's frontmatter SHA on the
    next pass.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def _read_with_retry(path: Path, retries: int = 1) -> str:
    """Read text with one retry on FileNotFoundError (D-FAIL-04).

    A brief sleep between attempts gives a racing tool (e.g. plugin reinstall
    in flight) a chance to finish renaming the file into place. Final
    ``FileNotFoundError`` propagates so the caller can record
    ``skipped: vanished``.
    """
    last: Optional[FileNotFoundError] = None
    for attempt in range(retries + 1):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            last = exc
            if attempt < retries:
                time.sleep(_VANISH_RETRY_DELAY_S)
    assert last is not None  # for type-checker — loop only exits via return or raise
    raise last


# ---------------------------------------------------------------------------
# Entry points: patch_all, unpatch_all
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """ISO-8601 timestamp with trailing ``Z`` (D-UNDO-04)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_entry(
    path: Path, scope: Literal["global", "project"], state: DetectedState
) -> PatchEntry:
    try:
        rel = path.relative_to(path.parent.parent.parent)
    except ValueError:
        rel = path
    return PatchEntry(path=str(path), scope=scope, state=state, relpath=str(rel))


def patch_all(skip: bool = False, *, dry_run: bool = False) -> PatchSummary:
    """Walk both agent scopes and patch every restrictive ``tools:`` whitelist.

    Idempotent (D-COVER-03): a second run produces zero new manifest entries
    because already-patched files now match coverage. Per-file failures
    skip-with-warning and never abort (D-FAIL-01..03 — mirrors
    ``_maybe_prepare_models`` swallow). Mid-scan vanish triggers one retry
    (D-FAIL-04).

    ``dry_run=True`` (SM-7b) runs the FULL detection pass unchanged and
    performs none of the writes: detected-but-unwritten entries land in
    ``summary.would_patch`` instead of being patched, and the manifest is
    neither written nor rewritten. The flag changes WHAT is written, never
    WHAT is detected.
    """
    summary = PatchSummary(manifest_path=manifest_path())
    if skip:
        info("--skip-patch-agents: skipping subagent reachability patch")
        return summary

    manifest = load_manifest()
    # Index existing entries by absolute path so re-patches replace rather
    # than duplicate (idempotent re-runs after a partial-state recovery).
    existing_by_path: dict[str, dict] = {p["path"]: p for p in manifest.get("patches", [])}

    candidates = scan_agent_dirs()
    new_entries: list[dict] = []
    any_changes = False

    for path, scope in candidates:
        entry = _make_entry(path, scope, "inheritance")  # state filled in below

        # T-08.1.03-07 oversize guard.
        try:
            size = path.stat().st_size
        except OSError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: stat-failed: {exc!r}")
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, f"stat-failed: {exc!r}"))
            continue
        if size > _MAX_AGENT_FILE_BYTES:
            err_console.print(
                f"[supamem.warn]agent_patcher: skipped {path}: oversize "
                f"({size} > {_MAX_AGENT_FILE_BYTES} bytes)"
            )
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, "oversize"))
            continue

        try:
            content = _read_with_retry(path)
        except FileNotFoundError:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: vanished")
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, "vanished"))
            continue
        except PermissionError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: read-only: {exc!r}")
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, "read-only"))
            continue
        except OSError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: io-error: {exc!r}")
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, f"io-error: {exc!r}"))
            continue

        try:
            state = detect_tools_state(content)
        except Exception as exc:  # noqa: BLE001 — pure-kernel safety net (D-FAIL-03)
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: unexpected: {exc!r}")
            entry.state = "skipped:malformed"
            summary.skipped.append((entry, f"unexpected: {exc!r}"))
            continue

        entry.state = state

        if state == "covered":
            summary.covered.append(entry)
            continue
        if state == "inheritance":
            summary.inheritance.append(entry)
            continue
        if state.startswith("skipped:"):
            reason = state[len("skipped:") :]
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: {reason}")
            summary.skipped.append((entry, reason))
            continue

        # patchable_csv / patchable_list
        try:
            new_text, fragment = patch_yaml(content)
        except Exception as exc:  # noqa: BLE001 — kernel safety net (D-FAIL-03)
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: patch-failed: {exc!r}")
            summary.skipped.append((entry, f"patch-failed: {exc!r}"))
            continue
        if fragment is None:
            # Defensive: detect_state said patchable but kernel returned no fragment.
            summary.skipped.append((entry, "patch-noop"))
            continue

        if dry_run:
            # SM-7b: detection ran in full; only the write is withheld.
            summary.would_patch.append(entry)
            continue

        try:
            _atomic_write_text(path, new_text)
        except PermissionError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: read-only: {exc!r}")
            summary.skipped.append((entry, "read-only"))
            continue
        except OSError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: skipped {path}: io-error: {exc!r}")
            summary.skipped.append((entry, f"io-error: {exc!r}"))
            continue

        record = {
            "path": str(path),
            "scope": scope,
            "patched_at": _utc_now_iso(),
            "supamem_version": _supamem_version(),
            "original_frontmatter": fragment["original_frontmatter"],
            "original_frontmatter_sha256": fragment["original_frontmatter_sha256"],
            "patched_frontmatter_sha256": fragment["patched_frontmatter_sha256"],
            "tools_form": fragment["tools_form"],
        }
        existing_by_path[str(path)] = record
        new_entries.append(record)
        summary.patched.append(entry)
        any_changes = True

    if any_changes and not dry_run:
        manifest["patches"] = list(existing_by_path.values())
        manifest["supamem_version"] = _supamem_version()
        try:
            save_manifest(manifest)
        except RuntimeError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: manifest save failed: {exc!r}")

    return summary


def unpatch_all() -> UnpatchSummary:
    """Restore every patched agent whose frontmatter SHA still matches.

    Files whose frontmatter SHA has drifted (user-edited or plugin-rewritten)
    are skipped with a stderr warning naming the path (D-UNDO-02). Files
    listed in the manifest but missing from disk are skipped silently with
    an info log. After processing, the manifest is rewritten to retain only
    skipped-user-edited entries. When no entries remain, the manifest file
    is removed for a clean uninstall.
    """
    summary = UnpatchSummary()
    manifest_p = manifest_path()
    if not manifest_p.is_file():
        # No manifest = nothing was ever patched. Friendly no-op.
        return summary

    manifest = load_manifest()
    patches = manifest.get("patches", [])
    if not patches:
        try:
            manifest_p.unlink()
        except OSError:
            pass
        return summary

    retained: list[dict] = []

    for entry in patches:
        path_str = entry.get("path", "")
        path = Path(path_str)
        if not path.is_file():
            info(f"agent_patcher: manifest entry stale (file missing): {path_str}")
            summary.skipped_missing.append(path_str)
            # Drop stale entries — the file is gone, restoration is moot.
            continue

        try:
            current = path.read_text(encoding="utf-8")
        except OSError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: cannot read {path}: {exc!r}; skipping")
            summary.skipped_user_edited.append(path_str)
            retained.append(entry)
            continue

        current_sha = block_sha256(current)
        if current_sha != entry.get("patched_frontmatter_sha256"):
            err_console.print(
                f"[supamem.warn]agent {path} has been edited since supamem patched it; "
                f"manual cleanup required"
            )
            summary.skipped_user_edited.append(path_str)
            retained.append(entry)
            continue

        original_block = entry.get("original_frontmatter", "")
        # Reconstruct the file: original frontmatter + body slice past the
        # patched frontmatter block.
        patched_block = frontmatter_block(current)
        if patched_block is None:
            err_console.print(
                f"[supamem.warn]agent {path} no longer has a frontmatter block; "
                f"skipping restoration"
            )
            summary.skipped_user_edited.append(path_str)
            retained.append(entry)
            continue
        new_text = original_block + current[len(patched_block) :]

        write_ok = False
        for attempt in range(2):
            try:
                _atomic_write_text(path, new_text)
                write_ok = True
                break
            except PermissionError:
                # P10 — race with active Claude Code session on Windows; retry once.
                if attempt == 0:
                    time.sleep(_PERMISSION_RETRY_DELAY_S)
                    continue
                err_console.print(
                    f"[supamem.warn]agent_patcher: cannot write {path} (read-only); "
                    f"skipping restoration"
                )
                summary.skipped_user_edited.append(path_str)
                retained.append(entry)
                break
            except OSError as exc:
                err_console.print(
                    f"[supamem.warn]agent_patcher: io-error restoring {path}: {exc!r}"
                )
                summary.skipped_user_edited.append(path_str)
                retained.append(entry)
                break

        if write_ok:
            summary.restored.append(path_str)

    # Rewrite manifest with only the entries we couldn't safely restore.
    if retained:
        manifest["patches"] = retained
        manifest["supamem_version"] = _supamem_version()
        try:
            save_manifest(manifest)
        except RuntimeError as exc:
            err_console.print(f"[supamem.warn]agent_patcher: manifest rewrite failed: {exc!r}")
    else:
        # All entries either restored or stale — clean state, drop the manifest.
        try:
            manifest_p.unlink()
        except OSError:
            pass

    return summary
