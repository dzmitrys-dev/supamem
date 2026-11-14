"""Per-client hooks for supamem.

The dispatcher routes ``supamem hook <name>`` to the right module:
- ``claude-code`` / ``opencode``    → PreToolUse(Edit|Write|MultiEdit) memory injection
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
    stdin_text: Optional[str] = None,
) -> int:
    if client == "claude-code":
        from supamem.hooks import claude_code

        if stdin_text is None:
            stdin_text = claude_code.read_stdin_text()
        payload = claude_code.parse_payload(stdin_text)
        resolved = claude_code.resolve_file_path(file_path, stdin_text)
        return claude_code.run(resolved or Path("."), config, payload=payload)
    if client == "opencode":
        from supamem.hooks.opencode import run

        return run(file_path or Path("."), config)
    if client == "session-start":
        from supamem.hooks.session_start import run

        return run(client=None, config=config)
    if client == "claude-code-gate":
        from supamem.hooks.gate_edit import run as run_gate

        return run_gate()
    if client == "cursor-advisory":
        from supamem.hooks.cursor_advisory import run as run_advisory

        return run_advisory()
    raise ValueError(f"supamem: unknown hook client: {client!r}")
