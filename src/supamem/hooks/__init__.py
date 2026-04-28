"""Per-client hooks for supamem.

The dispatcher routes ``supamem hook <client>`` to the right module:
- ``claude-code`` → :mod:`supamem.hooks.claude_code`
- ``opencode``    → :mod:`supamem.hooks.opencode`
- ``cursor``      → snapshot regen lives in :mod:`supamem.hooks.cursor`
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
    raise ValueError(f"supamem: unknown hook client: {client!r}")
