"""OpenCode session/edit hook.

OpenCode's hook output spec converged on the same JSON shape as Claude Code
during 2026-Q1 (`{hookSpecificOutput: {hookEventName, additionalContext}}`).
We delegate to the Claude Code implementation but tag counter calls with
``source='hook_opencode'`` so per-host usage is observable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from supamem.config import ResolvedConfig
from supamem.hooks import claude_code as _cc

__all__ = ["run"]


def _bump_opencode(kind: str, source: str, tokens: int, latency_ms: float, **kw: Any) -> None:
    # Force the source tag regardless of caller.
    _cc._bump(kind=kind, source="hook_opencode", tokens=tokens, latency_ms=latency_ms, **kw)


def run(file_path: Path, config: ResolvedConfig) -> int:
    original_bump = _cc._bump
    _cc._bump = _bump_opencode  # type: ignore[assignment]
    try:
        return _cc.run(file_path, config)
    finally:
        _cc._bump = original_bump  # type: ignore[assignment]
