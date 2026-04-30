"""Claude Code PreToolUse gate: deny Edit/Write when no recent supamem search.

Wires into Claude Code's PreToolUse hook system. When the model attempts an
Edit / Write / MultiEdit and has NOT recently called the supamem search MCP
tool (``mcp__supamem__dual_memory_search`` or its alias
``mcp__supamem__qdrant_find``), this hook returns a ``permissionDecision:
deny`` so the model is told to load memory first.

The hook reads the Claude Code PreToolUse payload from stdin:

    {
      "session_id": "...",
      "transcript_path": "/abs/path/to/session.jsonl",
      "cwd": "...",
      "permission_mode": "...",
      "hook_event_name": "PreToolUse",
      "tool_name": "Edit",
      "tool_input": {...}
    }

It writes one line of JSON to stdout (``{"hookSpecificOutput": {...}}``)
and exits 0. Stderr carries human-readable diagnostics. Stdout MUST stay
clean — Claude Code parses it as JSON.

Performance budget: <50 ms. We reverse-scan the JSONL transcript with a
hard byte cap so long sessions don't slow tool calls.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Tool names that count as a "search" (canonical + qdrant alias).
_SEARCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "mcp__supamem__dual_memory_search",
        "mcp__supamem__qdrant_find",
    }
)

# Tools the gate applies to. Match what's installed in settings.json
# PreToolUse[].matcher.
_GATED_TOOL_NAMES: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Reverse-scan cap. 256 KB is enough for ~1k tool-use entries in practice
# without making the gate slow on multi-hour sessions.
_TRANSCRIPT_BYTE_CAP = 256 * 1024

# Hard timeout (ms) the hook will not exceed when scanning. Belt + suspenders
# beyond the byte cap.
_SCAN_TIMEOUT_MS = 50


def _emit_decision(decision: str, reason: str) -> None:
    """Write the Claude Code PreToolUse JSON decision to stdout (one line).

    ``decision`` is one of: ``"allow"``, ``"deny"``, ``"ask"``.
    ``reason`` is shown to the user / model when not ``"allow"``.
    """
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _read_stdin_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _scan_transcript_for_recent_search(transcript_path: Path) -> bool:
    """Return True if a search MCP tool was called recently.

    Recency window — DESIGN DECISION (placeholder, see TODO below).
    """
    if not transcript_path.is_file():
        return False

    try:
        size = transcript_path.stat().st_size
    except OSError:
        return False
    start = max(0, size - _TRANSCRIPT_BYTE_CAP)

    try:
        with transcript_path.open("rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return False

    # If we started mid-line (because of the byte cap), drop the partial first
    # line so json.loads doesn't choke.
    if start > 0:
        nl = blob.find(b"\n")
        if nl == -1:
            return False
        blob = blob[nl + 1 :]

    return _recency_satisfied(blob.splitlines())


def _recency_satisfied(jsonl_lines: list[bytes]) -> bool:
    """Strategy A — STRICT "since last user turn".

    The PreToolUse hook fires before the pending Edit, so the Edit's own
    tool_use is NOT yet in the transcript. We walk the transcript window
    from end backward; every assistant entry seen is prior to the pending
    Edit.

    * If we find an assistant tool_use whose ``name`` is in
      ``_SEARCH_TOOL_NAMES`` before hitting a ``"user"`` entry → allow:
      the agent searched within this turn.
    * If we hit a ``"user"`` entry first → deny: this turn has produced no
      search. The agent must call dual_memory_search before editing.
    * If we exhaust the window without seeing a user boundary → deny: the
      256 KB byte cap chopped off the boundary, we can't prove freshness.
      Agent can override per-session with ``SUPAMEM_GATE_DISABLE=1``.

    Aligns with the project CLAUDE.md "search BEFORE choosing an approach"
    rule. The gate is opt-in via ``--enforce-search`` so users who want
    strictness self-select.
    """
    for raw in reversed(jsonl_lines):
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "user":
            return False
        if etype == "assistant":
            content = entry.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") in _SEARCH_TOOL_NAMES
                ):
                    return True
    return False


def run() -> int:
    """Entry point — invoked by ``supamem hook claude-code-gate``."""
    if os.environ.get("SUPAMEM_GATE_DISABLE") == "1":
        _emit_decision("allow", "supamem gate disabled via SUPAMEM_GATE_DISABLE=1")
        return 0

    payload = _read_stdin_payload()
    tool_name = payload.get("tool_name")
    if tool_name not in _GATED_TOOL_NAMES:
        # Not our concern — let other PreToolUse hooks decide.
        _emit_decision("allow", "tool not gated by supamem")
        return 0

    transcript_path_str = payload.get("transcript_path")
    if not isinstance(transcript_path_str, str):
        # No transcript visible — fail-OPEN (don't block on bad payloads).
        _emit_decision("allow", "no transcript_path in hook payload — gate skipped")
        return 0

    if _scan_transcript_for_recent_search(Path(transcript_path_str)):
        _emit_decision("allow", "recent supamem search found")
        return 0

    _emit_decision(
        "deny",
        (
            "supamem dual_memory_search not called recently. "
            "Run mcp__supamem__dual_memory_search with a query about this change "
            "BEFORE editing — see CLAUDE.md dual-memory rule. "
            "Override once with SUPAMEM_GATE_DISABLE=1 in env."
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — invoked as a script
    raise SystemExit(run())
