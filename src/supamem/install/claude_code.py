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
    sweep_managed_blocks,
    wrap_managed_block,
)
from supamem.console import info
from supamem.install._types import InstallResult

log = logging.getLogger("supamem.install.claude_code")

CLAUDE_MD_IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"


def _mcp_supamem_entry(cwd: Path) -> dict[str, Any]:
    """Claude Code MCP stanza — inject SUPAMEM_PROJECT_ROOT when bootstrapped in-repo.

    Mirrors ``cursor._mcp_supamem_entry``: when the user runs
    ``supamem install --client claude-code`` from a directory containing
    ``.supamem/config.toml``, the absolute workspace path is wired into the
    MCP server entry's ``env`` block so Claude Code's MCP subprocess resolves
    the workspace's collection regardless of cwd. Without this, the global
    ``~/.claude.json`` mcpServers entry has no way to point at *this* project
    on a multi-project machine — every install overwrites the prior one.
    """
    env: dict[str, str] = {"DM_MCP_SOURCE": "mcp_claude_code"}
    if (cwd / ".supamem" / "config.toml").is_file():
        env["SUPAMEM_PROJECT_ROOT"] = str(cwd.resolve())
    return {
        "command": "supamem",
        "args": ["mcp-server", "--transport", "stdio"],
        "env": env,
    }


def _mcp_overlay(cwd: Path) -> dict[str, Any]:
    return {"mcpServers": {"supamem": _mcp_supamem_entry(cwd)}}


GATE_EDIT_HOOK_ENTRY: dict[str, Any] = {
    "matcher": "Edit|Write|MultiEdit",
    "hooks": [
        {
            "type": "command",
            "command": "supamem hook claude-code-gate",
            "timeout": 5,
        }
    ],
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


def _settings_with_hook(
    existing: dict[str, Any], *, enforce_search: bool = False
) -> dict[str, Any]:
    """Add PreToolUse + SessionStart hooks if absent. Idempotent per event.

    When ``enforce_search`` is True, also register the
    ``supamem hook claude-code-gate`` PreToolUse entry that DENIES
    Edit/Write/MultiEdit when no recent ``mcp__supamem__dual_memory_search``
    is found in the session transcript. This is opt-in because surprise-
    blocking is hostile UX on first-run.
    """
    merged = json.loads(json.dumps(existing))
    hooks_root = merged.setdefault("hooks", {})

    if not _hook_present(merged, "PreToolUse", "supamem hook claude-code"):
        hooks_root.setdefault("PreToolUse", []).extend(HOOKS_OVERLAY["hooks"]["PreToolUse"])

    if enforce_search and not _hook_present(merged, "PreToolUse", "supamem hook claude-code-gate"):
        hooks_root.setdefault("PreToolUse", []).append(GATE_EDIT_HOOK_ENTRY)

    # SessionStart banner (v0.1.5+). Skip if any supamem session-start entry
    # already exists (covers users who installed v0.1.4 by hand earlier).
    if not _hook_present(merged, "SessionStart", "supamem hook session-start"):
        hooks_root.setdefault("SessionStart", []).extend(HOOKS_OVERLAY["hooks"]["SessionStart"])

    return merged


def _claude_md_with_import(existing: str) -> str:
    # SM-4/SM-6: heal duplicate managed blocks BEFORE the strict extract —
    # sweep is a byte-level no-op on healthy text, so only duplicated files
    # (the accumulated-upgrade state that used to crash install) change.
    existing, removed = sweep_managed_blocks(existing)
    if removed:
        info(f"CLAUDE.md: swept {removed} duplicate SUPAMEM managed block(s)")
    before, owned, after = extract_managed_block(existing)
    if owned and CLAUDE_MD_IMPORT_LINE in owned:
        return existing
    block = wrap_managed_block(CLAUDE_MD_IMPORT_LINE)
    if owned:
        return f"{before}{block}{after}"
    glue = "\n" if existing and not existing.endswith("\n") else ""
    return f"{existing}{glue}\n{block}\n"


def _heal_managed_block_file(path: Path) -> str:
    """SM-6: read ``path`` and, when it carries duplicate managed blocks,
    rewrite it healed — .bak.<time_ns> sibling first (mirrors the install
    write path's backup pattern). Returns the (possibly healed) body text so
    the caller's ``extract_managed_block`` cannot raise on it. No-op on
    healthy files.
    """
    body = path.read_text(encoding="utf-8")
    healed, removed = sweep_managed_blocks(body)
    if not removed:
        return body
    info(
        f"{path.name}: swept {removed} duplicate SUPAMEM managed block(s) "
        f"(backup: {path.name}.bak.*)"
    )
    bak = path.with_name(path.name + f".bak.{time_ns()}")
    bak.write_text(body, encoding="utf-8")
    path.write_text(healed, encoding="utf-8")
    return healed


def install(
    *, dry_run: bool = False, scope: str = "project", enforce_search: bool = False
) -> InstallResult:
    """Install supamem MCP entry + hooks + CLAUDE.md import for Claude Code.

    ``scope`` controls where the MCP entry is written:

    * ``"project"`` (default) — ``<cwd>/.mcp.json`` (committable, team-shared).
      Per Anthropic docs, project-scope `.mcp.json` is the canonical
      per-workspace mechanism and is loaded with higher precedence than
      user-scope. Required for multi-project machines.
    * ``"user"`` — ``~/.claude.json`` ``mcpServers.supamem`` (legacy global).
      Last install wins on multi-project machines; kept for users who want
      supamem available across every project with one collection.

    PreToolUse + SessionStart hooks (``~/.claude/settings.json``) and the
    CLAUDE.md ``@import`` line are always written user-global — they describe
    behavior of the user's Claude Code session, not of any single workspace.
    """
    home = Path.home()
    cwd = Path.cwd()
    settings_json = home / ".claude" / "settings.json"
    claude_md = home / "CLAUDE.md"

    if scope == "project":
        mcp_target = cwd / ".mcp.json"
    elif scope == "user":
        mcp_target = home / ".claude.json"
    else:
        raise ValueError(
            f"claude-code install: unknown scope {scope!r} (expected 'project' or 'user')"
        )

    written: list[Path] = []
    backups: list[Path] = []
    diffs: list[str] = []
    would_write = 0

    cur = _read_json(mcp_target)
    merged = deep_merge_json(cur, _mcp_overlay(cwd))
    res = atomic_write_json(mcp_target, merged, dry_run=dry_run)
    if res.diff:
        diffs.append(res.diff)
        would_write += 1
    if res.written:
        written.append(mcp_target)
    if res.backup_path:
        backups.append(res.backup_path)

    cur_s = _read_json(settings_json)
    merged_s = _settings_with_hook(cur_s, enforce_search=enforce_search)
    res_s = atomic_write_json(settings_json, merged_s, dry_run=dry_run)
    if res_s.diff:
        diffs.append(res_s.diff)
        would_write += 1
    if res_s.written:
        written.append(settings_json)
    if res_s.backup_path:
        backups.append(res_s.backup_path)

    existing_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    new_md = _claude_md_with_import(existing_md)
    if new_md != existing_md:
        would_write += 1
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


def _strip_supamem_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """Strip both the injection (`claude-code`) and gate (`claude-code-gate`)
    PreToolUse hooks. Substring `supamem hook claude-code` matches both."""
    out = json.loads(json.dumps(settings))
    pre = out.get("hooks", {}).get("PreToolUse", [])
    cleaned: list[Any] = []
    for entry in pre:
        kept_hooks = [
            h
            for h in entry.get("hooks", []) or []
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


def uninstall(*, dry_run: bool = False) -> int:
    """Remove supamem from BOTH project and user scopes (defensive).

    Strips ``mcpServers.supamem`` from any of:
    * ``<cwd>/.mcp.json`` (project scope)
    * ``~/.claude.json`` (user scope)

    so a single uninstall fully cleans up regardless of which scope the user
    originally installed with.

    Returns the number of targets changed (real run) or that WOULD change
    (dry run), derived from the same diff accounting the real writes use.

    ``dry_run=True`` computes every strip against the current content but
    writes nothing (SM-7a) — no JSON rewrite, no .bak sibling, no managed-
    block removal, no sweep-heal rewrite. It never raises, including on the
    duplicated-block state healed in real runs (sweep runs in-memory only).
    """
    home = Path.home()
    cwd = Path.cwd()
    mcp_targets = [cwd / ".mcp.json", home / ".claude.json"]
    settings_json = home / ".claude" / "settings.json"
    claude_md = home / "CLAUDE.md"
    would_change = 0

    for target in mcp_targets:
        if target.exists():
            cur = _read_json(target)
            res = atomic_write_json(target, _strip_supamem_from_mcp(cur), dry_run=dry_run)
            if res.diff:
                would_change += 1

    if settings_json.exists():
        cur = _read_json(settings_json)
        res = atomic_write_json(settings_json, _strip_supamem_hook(cur), dry_run=dry_run)
        if res.diff:
            would_change += 1

    if claude_md.exists():
        if dry_run:
            # SM-7a: heal in-memory only — dry-run must neither raise on the
            # duplicated-block state nor write the healed file.
            body = claude_md.read_text(encoding="utf-8")
            body, _removed = sweep_managed_blocks(body)
        else:
            # SM-6: duplicated managed blocks used to crash uninstall here
            # with an unhandled ValueError — heal (with backup) first.
            body = _heal_managed_block_file(claude_md)
        before, _owned, after = extract_managed_block(body)
        if before != body:
            would_change += 1
            new_body = (before.rstrip() + "\n" + after.lstrip()).strip() + "\n"
            if not dry_run:
                claude_md.write_text(new_body, encoding="utf-8")
    return would_change


__all__ = ["install", "uninstall"]
