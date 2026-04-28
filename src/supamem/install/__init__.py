"""Per-client installer dispatcher for supamem.

``install(client, *, dry_run)`` always syncs the canonical share dir first,
then routes to the client-specific module. ``uninstall(client)`` removes only
the managed-block region — user-edited content outside the fences is preserved.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from supamem.console import err, info, ok
from supamem.install.share import ensure_share_dir

log = logging.getLogger("supamem.install")

VALID_CLIENTS = ("claude-code", "cursor", "opencode")


def _autodetect() -> Optional[str]:
    """Return the single matching client name, or None if 0 or >1 detected."""
    home = Path.home()
    candidates: list[str] = []
    if (home / ".claude.json").exists() or (home / ".claude").exists():
        candidates.append("claude-code")
    if (home / ".cursor" / "mcp.json").exists() or (home / ".cursor").exists():
        candidates.append("cursor")
    if (home / ".config" / "opencode").exists():
        candidates.append("opencode")
    return candidates[0] if len(candidates) == 1 else None


def install(client: Optional[str], *, dry_run: bool = False) -> int:
    """Install supamem into the named client (or auto-detect)."""
    if client is None:
        client = _autodetect()
        if client is None:
            err("could not auto-detect a single installed client; pass --client X")
            return 2
        info(f"auto-detected client: {client}")

    if client not in VALID_CLIENTS:
        err(f"unknown client: {client!r} (valid: {', '.join(VALID_CLIENTS)})")
        return 2

    written = ensure_share_dir()
    if written:
        ok(f"synced {len(written)} share artifact(s)")

    if client == "claude-code":
        from supamem.install import claude_code

        result = claude_code.install(dry_run=dry_run)
    elif client == "cursor":
        from supamem.install import cursor as cursor_install

        result = cursor_install.install(dry_run=dry_run)
    elif client == "opencode":
        from supamem.install import opencode

        result = opencode.install(dry_run=dry_run)
    else:  # pragma: no cover — VALID_CLIENTS guard above
        return 2

    if result.no_op:
        info(f"{client}: already installed (no-op)")
    elif dry_run:
        info(f"{client}: dry-run — would write {len(result.written_files)} file(s)")
    else:
        ok(f"{client}: installed ({len(result.written_files)} file(s) written)")
    return 0


def uninstall(client: Optional[str]) -> int:
    if client is None:
        client = _autodetect()
        if client is None:
            err("could not auto-detect a single client; pass --client X")
            return 2

    if client == "claude-code":
        from supamem.install import claude_code

        return claude_code.uninstall()
    if client == "cursor":
        from supamem.install import cursor as cursor_install

        return cursor_install.uninstall()
    if client == "opencode":
        from supamem.install import opencode

        return opencode.uninstall()
    err(f"unknown client: {client!r}")
    return 2


__all__ = ["install", "uninstall"]
