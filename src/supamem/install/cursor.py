"""Cursor installer — patches ~/.cursor/mcp.json + ./.cursor/{rules,hooks.json}.

Targets:
1. ``~/.cursor/mcp.json`` — register MCP server entry under ``mcpServers.supamem``.
2. ``./.cursor/rules/dual-memory.mdc`` — COPIED from the share/cursor-rules
   tree (SC-3 documented exception: Cursor has no @import mechanism).
3. ``./.cursor/hooks.json`` — register a sessionStart entry calling
   ``supamem index --snapshot cursor`` so the .mdc snapshot refreshes
   on every Cursor session.

Atomic JSON writes with .bak.<ts>; idempotent on re-run.
"""
from __future__ import annotations

import json
import logging
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from supamem.config_io import atomic_write_json, deep_merge_json
from supamem.install._types import InstallResult

log = logging.getLogger("supamem.install.cursor")


def _mcp_supamem_entry(cwd: Path) -> dict[str, Any]:
    """Cursor MCP stanza — inject SUPAMEM_PROJECT_ROOT when bootstrapped in-repo."""
    env: dict[str, str] = {"DM_MCP_SOURCE": "mcp_cursor"}
    if (cwd / ".supamem" / "config.toml").is_file():
        env["SUPAMEM_PROJECT_ROOT"] = str(cwd.resolve())
    return {
        "command": "supamem",
        "args": ["mcp-server", "--transport", "stdio"],
        "env": env,
    }

SESSION_START_HOOK: dict[str, Any] = {
    "sessionStart": [
        {
            "command": ["supamem", "index", "--snapshot", "cursor"],
            "timeout": 60,
        }
    ]
}

ADVISORY_HOOK: dict[str, Any] = {
    "beforeSubmitPrompt": [
        {
            "command": ["supamem", "hook", "cursor-advisory"],
            "timeout": 5,
        }
    ]
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _packaged_mdc_source() -> Path:
    """Resolve the packaged dual-memory.mdc path."""
    files = resources.files("supamem.share")
    return Path(str(files)) / "cursor-rules" / "dual-memory.mdc"


def _hooks_already_present(existing: dict[str, Any]) -> bool:
    for entry in existing.get("sessionStart", []) or []:
        cmd = entry.get("command", [])
        if isinstance(cmd, list) and "supamem" in cmd and "--snapshot" in cmd:
            return True
        if isinstance(cmd, str) and "supamem index --snapshot cursor" in cmd:
            return True
    return False


def _advisory_already_present(existing: dict[str, Any]) -> bool:
    for entry in existing.get("beforeSubmitPrompt", []) or []:
        cmd = entry.get("command", [])
        if isinstance(cmd, list) and "supamem" in cmd and "cursor-advisory" in cmd:
            return True
        if isinstance(cmd, str) and "cursor-advisory" in cmd:
            return True
    return False


def _hooks_with_snapshot(existing: dict[str, Any]) -> dict[str, Any]:
    """Add sessionStart snapshot + beforeSubmitPrompt advisory if absent.

    Each event is gated independently so re-running install picks up only
    the missing entries — important for users on older installs upgrading
    in place.
    """
    merged = json.loads(json.dumps(existing))
    if not _hooks_already_present(merged):
        merged.setdefault("sessionStart", []).extend(SESSION_START_HOOK["sessionStart"])
    if not _advisory_already_present(merged):
        merged.setdefault("beforeSubmitPrompt", []).extend(
            ADVISORY_HOOK["beforeSubmitPrompt"]
        )
    return merged


def install(*, dry_run: bool = False, scope: str = "project") -> InstallResult:
    """Install supamem MCP entry + hooks for Cursor.

    ``scope`` controls where the MCP entry is written:

    * ``"project"`` (default) — ``<cwd>/.cursor/mcp.json``. Per-workspace,
      project-level wins on conflict per Cursor docs. Required for multi-
      project machines: each workspace carries its own ``SUPAMEM_PROJECT_ROOT``.
    * ``"user"`` — ``~/.cursor/mcp.json`` (legacy global). Single-project
      machines or users who want supamem available everywhere with one
      collection. Last install wins on multi-project machines.

    Hooks (``.cursor/hooks.json``) and the rules ``.mdc`` are always written
    project-scoped — they describe per-workspace behavior regardless of where
    the MCP entry lives.
    """
    home = Path.home()
    cwd = Path.cwd()
    if scope == "project":
        mcp_path = cwd / ".cursor" / "mcp.json"
    elif scope == "user":
        mcp_path = home / ".cursor" / "mcp.json"
    else:
        raise ValueError(f"cursor install: unknown scope {scope!r} (expected 'project' or 'user')")
    hooks_path = cwd / ".cursor" / "hooks.json"
    mdc_target = cwd / ".cursor" / "rules" / "dual-memory.mdc"

    written: list[Path] = []
    backups: list[Path] = []
    diffs: list[str] = []

    cur_mcp = _read_json(mcp_path)
    merged_mcp = deep_merge_json(
        cur_mcp,
        {"mcpServers": {"supamem": _mcp_supamem_entry(cwd)}},
    )
    res_mcp = atomic_write_json(mcp_path, merged_mcp, dry_run=dry_run)
    if res_mcp.diff:
        diffs.append(res_mcp.diff)
    if res_mcp.written:
        written.append(mcp_path)
    if res_mcp.backup_path:
        backups.append(res_mcp.backup_path)

    cur_hooks = _read_json(hooks_path)
    merged_hooks = _hooks_with_snapshot(cur_hooks)
    res_hooks = atomic_write_json(hooks_path, merged_hooks, dry_run=dry_run)
    if res_hooks.diff:
        diffs.append(res_hooks.diff)
    if res_hooks.written:
        written.append(hooks_path)
    if res_hooks.backup_path:
        backups.append(res_hooks.backup_path)

    src = _packaged_mdc_source()
    if src.exists():
        new_mdc = src.read_bytes()
        cur_mdc = mdc_target.read_bytes() if mdc_target.exists() else b""
        if cur_mdc != new_mdc:
            if not dry_run:
                mdc_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, mdc_target)
                written.append(mdc_target)
            diffs.append(f"--- {mdc_target}\n+++ {mdc_target}\n@@ supamem dual-memory.mdc copy @@\n")

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


def _strip_supamem_session_start(hooks: dict[str, Any]) -> dict[str, Any]:
    """Strip BOTH the snapshot sessionStart and advisory beforeSubmitPrompt
    entries that this installer added. Other entries are preserved."""
    out = json.loads(json.dumps(hooks))

    ss = out.get("sessionStart", []) or []
    cleaned_ss: list[Any] = []
    for entry in ss:
        cmd = entry.get("command", [])
        flat = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "supamem" in flat and "--snapshot" in flat:
            continue
        cleaned_ss.append(entry)
    if cleaned_ss:
        out["sessionStart"] = cleaned_ss
    elif "sessionStart" in out:
        del out["sessionStart"]

    bsp = out.get("beforeSubmitPrompt", []) or []
    cleaned_bsp: list[Any] = []
    for entry in bsp:
        cmd = entry.get("command", [])
        flat = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "supamem" in flat and "cursor-advisory" in flat:
            continue
        cleaned_bsp.append(entry)
    if cleaned_bsp:
        out["beforeSubmitPrompt"] = cleaned_bsp
    elif "beforeSubmitPrompt" in out:
        del out["beforeSubmitPrompt"]

    return out


def uninstall() -> int:
    """Remove supamem from BOTH project and user scopes (defensive).

    A user could have run ``supamem install --client cursor`` from multiple
    machines or with different scope flags over time. Strip from both so a
    single uninstall fully cleans up.
    """
    home = Path.home()
    cwd = Path.cwd()
    mcp_paths = [cwd / ".cursor" / "mcp.json", home / ".cursor" / "mcp.json"]
    hooks_path = cwd / ".cursor" / "hooks.json"
    mdc_target = cwd / ".cursor" / "rules" / "dual-memory.mdc"

    for mcp_path in mcp_paths:
        if mcp_path.exists():
            atomic_write_json(mcp_path, _strip_supamem_from_mcp(_read_json(mcp_path)))
    if hooks_path.exists():
        atomic_write_json(hooks_path, _strip_supamem_session_start(_read_json(hooks_path)))
    if mdc_target.exists():
        try:
            mdc_target.unlink()
        except OSError:
            pass
    return 0


__all__ = ["install", "uninstall"]
