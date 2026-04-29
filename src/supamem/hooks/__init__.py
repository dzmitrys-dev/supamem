"""Per-client hooks for supamem.

The dispatcher routes ``supamem hook <name>`` to the right module:
- ``claude-code`` / ``opencode``    → PreToolUse(Edit|Write) memory injection
- ``cursor``                        → snapshot regen
- ``session-start`` (v0.1.4+)       → cross-client SessionStart banner
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from supamem.config import ResolvedConfig

__all__ = ["dispatch"]


def dispatch(
    client: str,
    file_path: Optional[Path],
    config: ResolvedConfig,
) -> int:
    if client == "claude-code":
        from supamem.hooks.claude_code import run

        return run(file_path or Path("."), config)
    if client == "opencode":
        from supamem.hooks.opencode import run

        return run(file_path or Path("."), config)
    if client == "session-start":
        from supamem.hooks.session_start import run

        return run(client=None, config=config)
    raise ValueError(f"supamem: unknown hook client: {client!r}")
