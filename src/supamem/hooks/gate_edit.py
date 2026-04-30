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
    """Walk transcript lines (oldest first within our window) and decide.

    TODO(daisy): RECENCY-WINDOW DESIGN DECISION — please choose ONE strategy.

    The transcript is JSONL where each line is one of:
      - {"type": "user", "message": {...}, ...}
      - {"type": "assistant", "message": {"content": [{"type": "tool_use",
            "name": "...", "input": {...}}, ...]}, ...}
      - {"type": "tool_result", ...}

    Three viable strategies (pick one and implement here, ~5-10 lines):

    A) "since last user turn" — STRICT. Walk lines from end backward; if we
       hit a search tool_use BEFORE we hit the previous "user" entry, allow.
       Otherwise deny. Forces a fresh search every time the user gives a new
       instruction. Risk: feels naggy on iterative back-and-forth edits.

    B) "within last N tool calls" (e.g. N=20) — RELAXED. Count tool_use
       entries from end; if any of the most recent N is a search, allow.
       Decouples gating from conversation structure. Risk: a stale search
       can paper over a topic shift.

    C) "within last X seconds wall-clock" (e.g. X=600) — TIME-WINDOW. Compare
       transcript line timestamps. Simple. Risk: ignores semantic structure
       entirely; long-paused sessions get re-prompted.

    Recommended: (A) for hard enforcement aligned with the project CLAUDE.md
    "search BEFORE choosing an approach" rule. Switch to (B) if it feels too
    nagging in practice — the gate is opt-in via install flag anyway.

    Until you decide, this returns True (always allow) so the hook is a
    no-op. Replace the body with one of the strategies above.
    """
    # PLACEHOLDER — see TODO. Returning True keeps the gate inert until the
    # recency strategy is locked in.
    _ = jsonl_lines  # silence lint while placeholder lives
    return True


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
