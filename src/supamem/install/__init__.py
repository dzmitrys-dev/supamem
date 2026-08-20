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


VALID_SCOPES = ("project", "user")


def _maybe_prepare_models(skip_models: bool) -> None:
    """Eager-fetch ML model prerequisites unless air-gapped opt-out (D-FETCH-01).

    Idempotent: when ``_manifest_matches`` reports a healthy cache the
    network roundtrip is skipped (D-FETCH-03). Failures are non-fatal —
    the client config still wires; user re-runs ``supamem repair`` later.
    """
    if skip_models:
        info("--skip-models: skipping ML model pre-fetch")
        return
    try:
        from supamem.config import load_config  # noqa: PLC0415
        from supamem.rerankers import prepare  # noqa: PLC0415

        cfg, _ = load_config()
        if getattr(cfg, "reranker_name", "off") != "off":
            prepare(cfg.reranker_model_id)
            ok("reranker model cached")
    except RuntimeError:
        # err_console already surfaced the actionable error; do not abort.
        from supamem.console import warn as _warn  # noqa: PLC0415

        _warn("reranker model fetch failed — run `supamem repair` later")
    except Exception as exc:  # noqa: BLE001 — config / loader pathologies must not block install
        log.debug("model pre-fetch skipped: %r", exc)


def _maybe_patch_agents(skip_patch_agents: bool) -> None:
    """Idempotently patch ``~/.claude/agents/`` + ``<project>/.claude/agents/`` whitelists.

    Per D-LOCK-01..03 + D-FAIL-03, mirrors ``_maybe_prepare_models`` swallow shape:
    failures are non-fatal — install always exits 0. Ordering invariant: this MUST
    be called AFTER ``_maybe_prepare_models`` (slow network first; fast filesystem
    second per RESEARCH.md "Wiring" rationale).
    """
    if skip_patch_agents:
        info("--skip-patch-agents: skipping subagent reachability patch")
        return
    try:
        from supamem.install.agent_patcher import patch_all  # noqa: PLC0415

        summary = patch_all(skip=False)
        n = len(summary.patched)
        if n > 0:
            ok(f"patched {n} subagent file(s) for supamem reachability")
    except Exception as exc:  # noqa: BLE001 — patcher pathologies must not block install
        log.debug("agent patch skipped: %r", exc)
        from supamem.console import warn as _warn  # noqa: PLC0415

        _warn(f"subagent patcher skipped: {exc!r} — run `supamem repair` later")


def install(
    client: Optional[str],
    *,
    dry_run: bool = False,
    scope: str = "project",
    enforce_search: bool = False,
    skip_models: bool = False,
    skip_patch_agents: bool = False,
) -> int:
    """Install supamem into the named client (or auto-detect).

    ``scope`` defaults to ``"project"`` so multi-project machines work without
    extra flags: each workspace gets its own ``.mcp.json`` / ``.cursor/mcp.json``
    with the correct ``SUPAMEM_PROJECT_ROOT``. Pass ``scope="user"`` to keep
    the legacy global behavior (one entry, last install wins).

    ``opencode`` ignores ``scope`` for now — its config layout is global-only.
    """
    if client is None:
        client = _autodetect()
        if client is None:
            err("could not auto-detect a single installed client; pass --client X")
            return 2
        info(f"auto-detected client: {client}")

    if client not in VALID_CLIENTS:
        err(f"unknown client: {client!r} (valid: {', '.join(VALID_CLIENTS)})")
        return 2

    if scope not in VALID_SCOPES:
        err(f"unknown scope: {scope!r} (valid: {', '.join(VALID_SCOPES)})")
        return 2

    if dry_run:
        # SM-7a (strict Q7 contract): a dry run changes NOTHING — the
        # share-dir sync, model pre-fetch, and agent patcher all write
        # outside the client config and are skipped entirely.
        info("dry-run: share-dir sync skipped (model pre-fetch and agent patching skipped)")
    else:
        written = ensure_share_dir()
        if written:
            ok(f"synced {len(written)} share artifact(s)")

        # Eager-fetch ML prerequisites BEFORE client dispatch (D-FETCH-01).
        # Order: models first (slow network), patcher second (fast
        # filesystem) — per RESEARCH.md "Wiring" rationale (Phase 08.1
        # D-LOCK-01..03 + D-FAIL-03).
        _maybe_prepare_models(skip_models)
        _maybe_patch_agents(skip_patch_agents)

    if client == "claude-code":
        from supamem.install import claude_code

        result = claude_code.install(dry_run=dry_run, scope=scope, enforce_search=enforce_search)
    elif client == "cursor":
        from supamem.install import cursor as cursor_install

        # Cursor's hooks API has no fail-closed pre-edit event today — gate
        # is Claude-Code-only. enforce_search is silently ignored for Cursor.
        result = cursor_install.install(dry_run=dry_run, scope=scope)
    elif client == "opencode":
        from supamem.install import opencode

        # opencode is global-only today — ignore scope and enforce_search
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


def uninstall(client: Optional[str], *, dry_run: bool = False) -> int:
    """Remove supamem from the named client (or auto-detect).

    ``dry_run=True`` performs none of the strip writes (SM-7a): every target
    the real uninstall would rewrite is left byte-identical.
    """
    if client is None:
        client = _autodetect()
        if client is None:
            err("could not auto-detect a single client; pass --client X")
            return 2

    if client == "claude-code":
        from supamem.install import claude_code

        return claude_code.uninstall(dry_run=dry_run)
    if client == "cursor":
        from supamem.install import cursor as cursor_install

        return cursor_install.uninstall(dry_run=dry_run)
    if client == "opencode":
        from supamem.install import opencode

        return opencode.uninstall(dry_run=dry_run)
    err(f"unknown client: {client!r}")
    return 2


def repair(
    client: Optional[str],
    *,
    dry_run: bool = False,
    enforce_search: bool = False,
    skip_models: bool = False,
    skip_patch_agents: bool = False,
) -> int:
    """Re-install at project scope and strip stale GLOBAL supamem entries.

    The migration verb for users on legacy global installs. Strategy:

    1. Strip ``mcpServers.supamem`` from the GLOBAL config files
       (``~/.claude.json``, ``~/.cursor/mcp.json``) — uninstall already does
       this defensively across both scopes, but we want only the user-scope
       removal here, NOT the project-scope removal that uninstall does. So
       we call the per-client uninstall (strips both) and then re-install
       project scope to put project files back.
    2. Re-run ``install(scope="project")`` from the current cwd so the
       per-workspace files exist with ``SUPAMEM_PROJECT_ROOT`` injected.

    On a healthy install this is a near no-op: stripping a missing entry is
    a no-op, and re-installing on top of an already-correct project scope
    file reports ``no_op=True``.

    Pass ``client=None`` to repair every detected install. Auto-detect uses
    the same heuristic as ``install()``.
    """
    if client is None:
        # Repair every client that has any signal of being installed
        # (project-scope or user-scope). We attempt all and report.
        targets: list[str] = []
        cwd = Path.cwd()
        if (cwd / ".mcp.json").exists() or (Path.home() / ".claude.json").exists():
            targets.append("claude-code")
        if (cwd / ".cursor" / "mcp.json").exists() or (
            Path.home() / ".cursor" / "mcp.json"
        ).exists():
            targets.append("cursor")
        if (Path.home() / ".config" / "opencode").exists():
            targets.append("opencode")
        if not targets:
            err("no installed clients detected — nothing to repair")
            return 2
    else:
        if client not in VALID_CLIENTS:
            err(f"unknown client: {client!r}")
            return 2
        targets = [client]

    rc_overall = 0
    for tgt in targets:
        info(f"repair: {tgt}")
        # Uninstall strips supamem from both project AND user scopes.
        # SM-7a: the dry_run flag reaches the uninstall half too — without
        # it, `repair --dry-run` performed a REAL uninstall then skipped the
        # reinstall, leaving the machine stripped.
        uninstall_rc = uninstall(tgt, dry_run=dry_run)
        if uninstall_rc != 0:
            rc_overall = uninstall_rc
            continue
        # Re-install at project scope so per-workspace files are recreated.
        # skip_models flows through to prepare() which is itself idempotent
        # via _manifest_matches (D-FETCH-03): healthy cache → no network call.
        install_rc = install(
            tgt,
            dry_run=dry_run,
            scope="project",
            enforce_search=enforce_search,
            skip_models=skip_models,
            skip_patch_agents=skip_patch_agents,
        )
        if install_rc != 0:
            rc_overall = install_rc
    return rc_overall


__all__ = ["install", "uninstall", "repair"]
