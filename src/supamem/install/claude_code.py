"""Claude Code installer — patches ~/.claude.json + ~/.claude/settings.json + ~/CLAUDE.md.

Targets:
1. ``~/.claude.json`` — register MCP server entry under ``mcpServers.supamem``.
2. ``~/.claude/settings.json`` — add a ``hooks.PreToolUse`` entry that delegates
   to ``supamem hook claude-code --file-path "$CLAUDE_FILE_PATH"``.
3. ``~/CLAUDE.md`` — append (inside SUPAMEM managed-block fences) an
   ``@~/.supamem/share/rules/dual-memory.md`` import line.

All edits are atomic (config_io.atomic_write_json + .bak.<ts>). Idempotent —
re-running detects identical content and reports ``no_op=True``. Uninstall
removes only managed-block content / shape-matched supamem keys; user content
outside the fences and other MCP server entries are preserved.
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

log = logging.getLogger("supamem.install.claude_code")

CLAUDE_MD_IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"

MCP_OVERLAY: dict[str, Any] = {
    "mcpServers": {
        "supamem": {
            "command": "supamem",
            "args": ["mcp-server", "--transport", "stdio"],
            "env": {"DM_MCP_SOURCE": "mcp_claude_code"},
        }
    }
}

HOOKS_OVERLAY: dict[str, Any] = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'supamem hook claude-code --file-path "$CLAUDE_FILE_PATH"',
                        "timeout": 30,
                    }
                ],
            }
        ],
        # SessionStart banner (v0.1.5+) — one-line status injected at session
        # open. Gives users visible evidence supamem is alive without polluting
        # per-edit flow. Honors SUPAMEM_BANNER_DISABLE=1 if a user opts out.
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "supamem hook session-start",
                        "timeout": 10,
                    }
                ],
            }
        ],
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


def _hook_present(settings: dict[str, Any], event: str, needle: str) -> bool:
    """Idempotency check — does any hook under ``event`` already invoke ``needle``?"""
    for entry in settings.get("hooks", {}).get(event, []) or []:
        for h in entry.get("hooks", []) or []:
            if needle in str(h.get("command", "")):
                return True
    return False


def _hook_already_present(settings: dict[str, Any]) -> bool:
    """Legacy alias preserved for tests — checks the PreToolUse Edit/Write hook."""
    return _hook_present(settings, "PreToolUse", "supamem hook claude-code")


def _settings_with_hook(existing: dict[str, Any]) -> dict[str, Any]:
    """Add PreToolUse + SessionStart hooks if absent. Idempotent per event."""
    merged = json.loads(json.dumps(existing))
    hooks_root = merged.setdefault("hooks", {})

    if not _hook_present(merged, "PreToolUse", "supamem hook claude-code"):
        hooks_root.setdefault("PreToolUse", []).extend(
            HOOKS_OVERLAY["hooks"]["PreToolUse"]
        )

    # SessionStart banner (v0.1.5+). Skip if any supamem session-start entry
    # already exists (covers users who installed v0.1.4 by hand earlier).
    if not _hook_present(merged, "SessionStart", "supamem hook session-start"):
        hooks_root.setdefault("SessionStart", []).extend(
            HOOKS_OVERLAY["hooks"]["SessionStart"]
        )

    return merged


def _claude_md_with_import(existing: str) -> str:
    before, owned, after = extract_managed_block(existing)
    if owned and CLAUDE_MD_IMPORT_LINE in owned:
        return existing
    block = wrap_managed_block(CLAUDE_MD_IMPORT_LINE)
    if owned:
        return f"{before}{block}{after}"
    glue = "\n" if existing and not existing.endswith("\n") else ""
    return f"{existing}{glue}\n{block}\n"


def install(*, dry_run: bool = False) -> InstallResult:
    home = Path.home()
    claude_json = home / ".claude.json"
    settings_json = home / ".claude" / "settings.json"
    claude_md = home / "CLAUDE.md"

    written: list[Path] = []
    backups: list[Path] = []
    diffs: list[str] = []

    cur = _read_json(claude_json)
    merged = deep_merge_json(cur, MCP_OVERLAY)
    res = atomic_write_json(claude_json, merged, dry_run=dry_run)
    if res.diff:
        diffs.append(res.diff)
    if res.written:
        written.append(claude_json)
    if res.backup_path:
        backups.append(res.backup_path)

    cur_s = _read_json(settings_json)
    merged_s = _settings_with_hook(cur_s)
    res_s = atomic_write_json(settings_json, merged_s, dry_run=dry_run)
    if res_s.diff:
        diffs.append(res_s.diff)
    if res_s.written:
        written.append(settings_json)
    if res_s.backup_path:
        backups.append(res_s.backup_path)

    existing_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    new_md = _claude_md_with_import(existing_md)
    if new_md != existing_md:
        if not dry_run:
            if claude_md.exists():
                bak = claude_md.with_name(claude_md.name + f".bak.{time_ns()}")
                bak.write_text(existing_md, encoding="utf-8")
                backups.append(bak)
            claude_md.write_text(new_md, encoding="utf-8")
            written.append(claude_md)
        diffs.append(
            f"--- {claude_md}\n+++ {claude_md}\n"
            f"@@ supamem managed @import block @@\n+{CLAUDE_MD_IMPORT_LINE}\n"
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


def _strip_supamem_hook(settings: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(settings))
    pre = out.get("hooks", {}).get("PreToolUse", [])
    cleaned: list[Any] = []
    for entry in pre:
        kept_hooks = [
            h for h in entry.get("hooks", []) or []
            if "supamem hook claude-code" not in str(h.get("command", ""))
        ]
        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            cleaned.append(new_entry)
    if cleaned:
        out["hooks"]["PreToolUse"] = cleaned
    elif "hooks" in out:
        out["hooks"].pop("PreToolUse", None)
        if not out["hooks"]:
            del out["hooks"]
    return out


def uninstall() -> int:
    home = Path.home()
    claude_json = home / ".claude.json"
    settings_json = home / ".claude" / "settings.json"
    claude_md = home / "CLAUDE.md"

    if claude_json.exists():
        cur = _read_json(claude_json)
        atomic_write_json(claude_json, _strip_supamem_from_mcp(cur))

    if settings_json.exists():
        cur = _read_json(settings_json)
        atomic_write_json(settings_json, _strip_supamem_hook(cur))

    if claude_md.exists():
        body = claude_md.read_text(encoding="utf-8")
        before, _owned, after = extract_managed_block(body)
        if before != body:
            new_body = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
            claude_md.write_text(new_body, encoding="utf-8")
    return 0


__all__ = ["install", "uninstall"]
