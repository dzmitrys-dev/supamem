"""SessionStart banner — emits a one-line status string when an AI client
opens a new session in a supamem-enabled project.

Visibility design (v0.1.4):
- Plain text, ≤200 chars (chat hosts don't render Markdown in additionalContext)
- Emoji prefix for instant recognition; no badges, no `<details>`, no fluff
- Cross-client portability via dual JSON keys (camelCase + snake_case) so
  Claude Code, Cursor, and OpenCode all pick it up
- ALL probes are best-effort — banner emits a degraded line on any failure
  rather than blocking session start

Output shape (per Claude Code hook contract):
    {
      "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "🧠 supamem v0.1.4 · ..."
      },
      "additional_context": "🧠 supamem v0.1.4 · ..."
    }

The duplicate snake_case key is for Cursor/OpenCode forks that adopted the
older shape; harmless on Claude Code (ignored).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from supamem import __version__
from supamem.config import ResolvedConfig

log = logging.getLogger("supamem.hooks.session_start")

# ── Limits ─────────────────────────────────────────────────────────────────
MAX_BANNER_CHARS = 200


# ── Probes (each best-effort; never raise) ─────────────────────────────────


def _probe_collection(cfg: ResolvedConfig) -> tuple[str | None, int | None]:
    """Return (collection_name, point_count). On any failure return (cfg.collection or None, None)."""
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            url=cfg.qdrant_url,
            api_key=cfg.qdrant_api_key or None,
            check_compatibility=False,
            timeout=2,
        )
        info = client.get_collection(cfg.collection)
        return cfg.collection, int(info.points_count or 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("session_start: collection probe failed: %s", exc)
        return cfg.collection or None, None


def _probe_audit_path() -> Path:
    """The canonical audit JSONL path (``platformdirs.user_cache_dir/audit.jsonl``)."""
    try:
        import platformdirs

        return Path(platformdirs.user_cache_dir("supamem")) / "audit.jsonl"
    except Exception:
        return Path.home() / ".cache" / "supamem" / "audit.jsonl"


# ── Banner construction ─────────────────────────────────────────────────────


def build_banner(cfg: ResolvedConfig | None = None) -> str:
    """Compose the one-line banner. Pure function (cfg may be None for tests)."""
    cfg = cfg or ResolvedConfig()
    collection, points = _probe_collection(cfg)
    audit = _probe_audit_path()

    parts = [f"🧠 supamem v{__version__}"]
    if collection:
        if points is None:
            parts.append(f"{collection} (qdrant unreachable)")
        else:
            parts.append(f"{collection} · {points} chunks")
    parts.append(f"audit {audit}")

    banner = " · ".join(parts)
    if len(banner) > MAX_BANNER_CHARS:
        banner = banner[: MAX_BANNER_CHARS - 1] + "…"
    return banner


# ── Cross-client emission ───────────────────────────────────────────────────


def _detect_client() -> str:
    """Sniff the calling client from env vars when --client is omitted."""
    if os.environ.get("CLAUDECODE", "").strip():
        return "claude-code"
    if os.environ.get("OPENCODE", "").strip():
        return "opencode"
    if os.environ.get("CURSOR_AGENT", "").strip() or os.environ.get("CURSOR", "").strip():
        return "cursor"
    return "claude-code"  # safest default — Claude Code accepts the dual schema


def _emit_payload(banner: str) -> dict[str, Any]:
    """Dual-format JSON payload that works across Claude Code / Cursor / OpenCode."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": banner,
        },
        # Snake-case duplicate for Cursor / OpenCode forks that adopted the
        # older shape. Harmless on Claude Code (key ignored).
        "additional_context": banner,
    }


def run(client: str | None = None, *, config: ResolvedConfig | None = None) -> int:
    """Run the session-start hook. Returns exit code; never raises."""
    try:
        cfg = config
        if cfg is None:
            from supamem.config import load_config

            cfg, _ = load_config()
        client_name = client or _detect_client()
        banner = build_banner(cfg)
        payload = _emit_payload(banner)
        # Discard any stdin payload — we don't need it for the banner
        try:
            if not sys.stdin.isatty():
                sys.stdin.read()
        except Exception:
            pass
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
        log.info("session_start: emitted banner for client=%s (%d chars)", client_name, len(banner))
        return 0
    except Exception as exc:  # noqa: BLE001 — fail-soft per hook discipline
        log.exception("session_start failed: %s", exc)
        return 0  # never break session start


__all__ = ["build_banner", "run", "MAX_BANNER_CHARS"]
