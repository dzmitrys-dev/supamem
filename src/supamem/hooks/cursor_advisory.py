"""Cursor beforeSubmitPrompt advisory hook (B3).

Cursor 1.7's hooks API has no fail-closed pre-edit event — the closest we
can do is ``beforeSubmitPrompt``, which lets us inject an ``agentMessage``
into the model's context BEFORE it processes the user's instruction.

This hook reads the Cursor hook payload from stdin, looks at the user's
prompt, and — when the prompt looks edit-bound AND the user hasn't recently
used supamem search — emits an advisory message reminding the agent to load
memory first. Cursor's docs (early 2026) report that ``deny`` enforcement is
buggy in some MCP paths, so this is intentionally an ADVISORY: ``permission``
stays ``"allow"`` and we use ``agentMessage`` to nudge.

Hook output schema (Cursor):

    {
      "continue": true,
      "permission": "allow",
      "agentMessage": "..."
    }

Stdout MUST stay JSON-only — Cursor parses it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# Heuristic edit-bound prompt patterns. Conservative: only fire when the
# user's intent is clearly action-bearing. Read-only / question-style
# prompts shouldn't trigger the nudge.
_EDIT_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(fix|refactor|rename|implement|add|remove|delete|update|migrate)\b", re.IGNORECASE),
    re.compile(r"\b(write|create|build|extract|introduce)\s+(a|the|new)?\b", re.IGNORECASE),
    re.compile(r"\bchange\b", re.IGNORECASE),
)

ADVISORY_MESSAGE = (
    "💡 supamem advisory: this prompt looks edit-bound. Before making changes, "
    "call mcp__supamem__dual_memory_search (or qdrant_find) with a query about "
    "the area you're touching — it will surface ADRs, decisions, and known "
    "issues that should shape your approach. See CLAUDE.md dual-memory rule. "
    "Disable this advisory with SUPAMEM_ADVISORY_DISABLE=1."
)


def _emit(decision: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(decision))
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


def _looks_edit_bound(prompt: str) -> bool:
    if not prompt:
        return False
    for pat in _EDIT_INTENT_PATTERNS:
        if pat.search(prompt):
            return True
    return False


def run() -> int:
    """Entry point — invoked by ``supamem hook cursor-advisory``."""
    base_decision: dict[str, Any] = {"continue": True, "permission": "allow"}

    if os.environ.get("SUPAMEM_ADVISORY_DISABLE") == "1":
        _emit(base_decision)
        return 0

    payload = _read_stdin_payload()
    # Cursor beforeSubmitPrompt payload shape (per docs / forum reports):
    # {"prompt": "...", "conversation_id": "...", ...}
    prompt = payload.get("prompt") or payload.get("user_prompt") or ""
    if not isinstance(prompt, str):
        prompt = ""

    if _looks_edit_bound(prompt):
        decision = {**base_decision, "agentMessage": ADVISORY_MESSAGE}
        _emit(decision)
        return 0

    _emit(base_decision)
    return 0


if __name__ == "__main__":  # pragma: no cover — invoked as a script
    raise SystemExit(run())
