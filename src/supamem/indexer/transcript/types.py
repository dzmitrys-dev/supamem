"""Typed schemas for Claude Code session JSONL events (Phase 06 R-01).

Cross-verified against KyleAMathews/claude-code-ui spec.md, daaain/claude-code-log,
HillviewCap/clog, and Piebald "Messages as Commits". ``tool_use`` is ALWAYS a
content block inside an assistant message; ``tool_result`` is ALWAYS a content
block inside a user message — never top-level events.

``total=False`` because Claude Code adds envelope fields across versions
(e.g. ``parentToolUseId`` / ``agentId`` for sub-agents in 1.0+); strict
typing would break forward-compat without buying us anything we can't get
from D-29's warn-and-skip.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


class ContentBlock(TypedDict, total=False):
    type: Literal["text", "thinking", "tool_use", "tool_result", "image"]
    text: str  # for text / thinking
    id: str  # for tool_use
    name: str  # for tool_use
    input: dict[str, Any]  # for tool_use
    tool_use_id: str  # for tool_result
    content: Any  # for tool_result (str OR list[dict])
    is_error: bool  # for tool_result


class Message(TypedDict, total=False):
    role: Literal["user", "assistant", "system"]
    content: str | list[ContentBlock]
    id: str  # assistant message id (Anthropic API id)
    usage: dict[str, int]


class SessionEvent(TypedDict, total=False):
    # Envelope (shared)
    type: str  # see parser.KNOWN_TYPES
    uuid: str  # stable per-message identifier
    parentUuid: str | None  # forms a DAG (Piebald)
    sessionId: str  # ties events to one session.jsonl
    timestamp: str  # ISO-8601
    cwd: str
    version: str  # claude-code version
    gitBranch: str
    isSidechain: bool  # sub-agent flag — v1 skips when True (Pitfall §5)
    userType: str
    # Payload
    message: Message  # absent on file-history-snapshot, summary
    # summary-only fields
    summary: str
    leafUuid: str
