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
    sweep_managed_blocks,
    wrap_managed_block,
)
from supamem.console import info
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
    # SM-4/SM-6: heal duplicate managed blocks and unpaired marker lines
    # BEFORE the strict extract — sweep is a byte-level no-op on healthy text
    # (claude_code twin).
    existing, removed = sweep_managed_blocks(existing)
    if removed:
        info(f"AGENTS.md: swept {removed} stale SUPAMEM managed-block marker(s)")
    before, owned, after = extract_managed_block(existing)
    if owned and AGENTS_MD_IMPORT_LINE in owned:
        return existing
    block = wrap_managed_block(AGENTS_MD_IMPORT_LINE)
    if owned:
        return f"{before}{block}{after}"
    glue = "\n" if existing and not existing.endswith("\n") else ""
    return f"{existing}{glue}\n{block}\n"


def _heal_managed_block_file(path: Path) -> str:
    """SM-6: read ``path`` and, when it carries duplicate managed blocks or
    unpaired marker lines, rewrite it healed — .bak.<time_ns> sibling first.
    No-op on healthy files. (claude_code twin.)

    Returns the (possibly healed) body text, on which the caller's
    ``extract_managed_block`` provably cannot raise: the sweep guarantees at
    most one complete fence pair. See the claude_code twin for why that
    invariant did not hold before CR-01.
    """
    body = path.read_text(encoding="utf-8")
    healed, removed = sweep_managed_blocks(body)
    if not removed:
        return body
    info(
        f"{path.name}: swept {removed} stale SUPAMEM managed-block marker(s) "
        f"(backup: {path.name}.bak.*)"
    )
    bak = path.with_name(path.name + f".bak.{time_ns()}")
    bak.write_text(body, encoding="utf-8")
    path.write_text(healed, encoding="utf-8")
    return healed


def install(*, dry_run: bool = False) -> InstallResult:
    home = Path.home()
    cwd = Path.cwd()
    cfg_path = home / ".config" / "opencode" / "opencode.json"
    agents_md = cwd / "AGENTS.md"

    written: list[Path] = []
    backups: list[Path] = []
    diffs: list[str] = []
    would_write = 0

    cur = _read_json(cfg_path)
    merged = deep_merge_json(cur, MCP_OVERLAY)
    res = atomic_write_json(cfg_path, merged, dry_run=dry_run)
    if res.diff:
        diffs.append(res.diff)
        would_write += 1
    if res.written:
        written.append(cfg_path)
    if res.backup_path:
        backups.append(res.backup_path)

    existing_md = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    new_md = _agents_md_with_import(existing_md)
    if new_md != existing_md:
        would_write += 1
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
        would_write=would_write,
    )


def _strip_supamem_from_mcp(raw: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(raw))
    servers = out.get("mcpServers")
    if isinstance(servers, dict) and "supamem" in servers:
        del servers["supamem"]
        if not servers:
            del out["mcpServers"]
    return out


def uninstall(*, dry_run: bool = False) -> int:
    """Remove supamem from the opencode config and ./AGENTS.md.

    Returns the number of targets changed (real run) or that WOULD change
    (dry run), from the same diff accounting the real writes use.

    ``dry_run=True`` computes every strip against the current content but
    writes nothing (SM-7a) — including on the duplicated-block state (the
    sweep heal runs in-memory only). (claude_code twin.)
    """
    home = Path.home()
    cwd = Path.cwd()
    cfg_path = home / ".config" / "opencode" / "opencode.json"
    agents_md = cwd / "AGENTS.md"
    would_change = 0

    if cfg_path.exists():
        cur = _read_json(cfg_path)
        res = atomic_write_json(cfg_path, _strip_supamem_from_mcp(cur), dry_run=dry_run)
        if res.diff:
            would_change += 1
    if agents_md.exists():
        if dry_run:
            # SM-7a: heal in-memory only — no raise, no write, no .bak.
            body = agents_md.read_text(encoding="utf-8")
            body, _removed = sweep_managed_blocks(body)
        else:
            # SM-6: duplicated managed blocks used to crash uninstall here
            # with an unhandled ValueError — heal (with backup) first.
            body = _heal_managed_block_file(agents_md)
        before, _owned, after = extract_managed_block(body)
        if before != body:
            would_change += 1
            new_body = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
            if not dry_run:
                agents_md.write_text(new_body, encoding="utf-8")
    return would_change


__all__ = ["install", "uninstall"]
