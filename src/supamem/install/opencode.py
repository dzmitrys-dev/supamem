"""OpenCode installer — patches ~/.config/opencode/opencode.json + ./AGENTS.md.

Targets:
1. ``~/.config/opencode/opencode.json`` — register MCP server entry under
   ``mcpServers.supamem`` (OpenCode's MCP schema mirrors Claude Code's).
2. ``./AGENTS.md`` — append (inside SUPAMEM managed-block fences) an
   ``@~/.supamem/share/rules/dual-memory.md`` import line. AGENTS.md is the
   shared cross-tool baseline (Apr 2026 convention).

Atomic JSON writes with .bak.<ts>; idempotent on re-run.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from time import time_ns
from typing import Any

from supamem.config_io import (
    atomic_write_json,
    deep_merge_json,
    extract_managed_block,
    wrap_managed_block,
)
from supamem.install._types import InstallResult

log = logging.getLogger("supamem.install.opencode")

AGENTS_MD_IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"

MCP_OVERLAY: dict[str, Any] = {
    "mcpServers": {
        "supamem": {
            "command": "supamem",
            "args": ["mcp-server", "--transport", "stdio"],
            "env": {"DM_MCP_SOURCE": "mcp_opencode"},
        }
    }
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _agents_md_with_import(existing: str) -> str:
    before, owned, after = extract_managed_block(existing)
    if owned and AGENTS_MD_IMPORT_LINE in owned:
        return existing
    block = wrap_managed_block(AGENTS_MD_IMPORT_LINE)
    if owned:
        return f"{before}{block}{after}"
    glue = "\n" if existing and not existing.endswith("\n") else ""
    return f"{existing}{glue}\n{block}\n"


def install(*, dry_run: bool = False) -> InstallResult:
    home = Path.home()
    cwd = Path.cwd()
    cfg_path = home / ".config" / "opencode" / "opencode.json"
    agents_md = cwd / "AGENTS.md"

    written: list[Path] = []
    backups: list[Path] = []
    diffs: list[str] = []

    cur = _read_json(cfg_path)
    merged = deep_merge_json(cur, MCP_OVERLAY)
    res = atomic_write_json(cfg_path, merged, dry_run=dry_run)
    if res.diff:
        diffs.append(res.diff)
    if res.written:
        written.append(cfg_path)
    if res.backup_path:
        backups.append(res.backup_path)

    existing_md = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    new_md = _agents_md_with_import(existing_md)
    if new_md != existing_md:
        if not dry_run:
            if agents_md.exists():
                bak = agents_md.with_name(agents_md.name + f".bak.{time_ns()}")
                bak.write_text(existing_md, encoding="utf-8")
                backups.append(bak)
            agents_md.write_text(new_md, encoding="utf-8")
            written.append(agents_md)
        diffs.append(
            f"--- {agents_md}\n+++ {agents_md}\n"
            f"@@ supamem managed @import block @@\n+{AGENTS_MD_IMPORT_LINE}\n"
        )

    no_op = not written and not diffs
    return InstallResult(
        written_files=written,
        backup_files=backups,
        diff="\n".join(diffs),
        no_op=no_op,
    )


def _strip_supamem_from_mcp(raw: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(raw))
    servers = out.get("mcpServers")
    if isinstance(servers, dict) and "supamem" in servers:
        del servers["supamem"]
        if not servers:
            del out["mcpServers"]
    return out


def uninstall() -> int:
    home = Path.home()
    cwd = Path.cwd()
    cfg_path = home / ".config" / "opencode" / "opencode.json"
    agents_md = cwd / "AGENTS.md"

    if cfg_path.exists():
        cur = _read_json(cfg_path)
        atomic_write_json(cfg_path, _strip_supamem_from_mcp(cur))
    if agents_md.exists():
        body = agents_md.read_text(encoding="utf-8")
        before, _owned, after = extract_managed_block(body)
        if before != body:
            new_body = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
            agents_md.write_text(new_body, encoding="utf-8")
    return 0


__all__ = ["install", "uninstall"]
