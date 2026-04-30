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


def _probe_health_flag(cfg: ResolvedConfig, points: int | None) -> str:
    """Single-character health indicator for the banner.

    * ``"⚠"`` when supamem looks misconfigured for THIS project:
      qdrant unreachable, OR the resolved collection is still the shipped
      default (``dev_memory_tuned_hybrid``) AND no project config was loaded
      (the legacy global-install failure mode), OR per-client install drift
      (a client's managed-block version differs from the running CLI).
    * ``"✓"`` otherwise.
    """
    if points is None:
        return "⚠"
    # ResolvedConfig() default collection — see config.py:42. If we're still
    # on it, no project TOML was loaded.
    default_collection = ResolvedConfig().collection
    if cfg.collection == default_collection:
        return "⚠"
    if _has_install_drift():
        return "⚠"
    return "✓"


def _has_install_drift() -> bool:
    """True if any installed client's managed-block version differs from the
    running CLI version (``supamem doctor`` drift signal).

    Cheap: reads small text files; never raises.
    """
    try:
        from supamem.doctor import version_drift_report

        return any(row.get("drift") for row in version_drift_report())
    except Exception as exc:  # noqa: BLE001 — banner must never fail
        log.debug("session_start: drift probe failed: %s", exc)
        return False


def _probe_update_hint() -> str | None:
    """Return ``"update v0.X.Y available"`` if a newer release is cached, else None.

    Cache-only — never touches the network. The background daemon-thread
    probe in ``update_check`` is what populates the cache asynchronously.
    """
    try:
        from supamem.update_check import doctor_report

        report = doctor_report(__version__)
        if report.get("update_available") and report.get("cached_latest_version"):
            return f"update v{report['cached_latest_version']} available"
    except Exception as exc:  # noqa: BLE001 — banner must never fail
        log.debug("session_start: update probe failed: %s", exc)
    return None


# ── Banner construction ─────────────────────────────────────────────────────


def build_banner(cfg: ResolvedConfig | None = None) -> str:
    """Compose the one-line banner. Pure function (cfg may be None for tests).

    Format:
        🧠 supamem ✓ v0.1.5 · supamem-supamem · 28 chunks · audit /path
                  ^── health flag (✓ healthy, ⚠ misconfigured/unreachable)

    When a newer release is cached locally (populated by the background
    update-check daemon), an extra segment is appended:
        … · update v0.2.0 available

    Length is capped at MAX_BANNER_CHARS (200); the audit path is the first
    segment dropped if we're at risk of overflow, then the chunk count.
    """
    cfg = cfg or ResolvedConfig()
    collection, points = _probe_collection(cfg)
    audit = _probe_audit_path()
    health = _probe_health_flag(cfg, points)
    update_hint = _probe_update_hint()

    head = f"🧠 supamem {health} v{__version__}"
    parts = [head]
    if collection:
        if points is None:
            parts.append(f"{collection} (qdrant unreachable)")
        else:
            parts.append(f"{collection} · {points} chunks")
    parts.append(f"audit {audit}")
    if update_hint:
        parts.append(update_hint)

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
    """Cross-host JSON payload — silent context injection + user-visible status.

    Three keys, three audiences:

    * ``hookSpecificOutput.additionalContext`` (Claude Code) — silent
      context injection into the model's window. Carries the full banner
      so the model knows version, collection, chunk count, audit path.
    * ``additional_context`` (Cursor / OpenCode forks) — snake-case
      duplicate; harmless on Claude Code (key ignored).
    * ``systemMessage`` (Claude Code) — the **user-visible** status line.
      Per Claude Code hooks docs, this field renders as the
      ``SessionStart:startup says: <line>`` row the user sees in the
      terminal. Suppressed when ``SUPAMEM_BANNER_QUIET=1``.
    * ``user_message`` (Cursor) — Cursor docs note this field is
      "accepted but not enforced" today; we include it for forward-
      compat once Cursor ships UI for it. No-op on Claude Code.

    The reason ``additionalContext`` and ``systemMessage`` carry the same
    payload is that both audiences benefit from the same one-liner: the
    user gets visible confirmation; the model gets context it can cite
    when answering "what version of supamem am I on?" without a tool call.
    """
    payload: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": banner,
        },
        "additional_context": banner,
    }
    if os.environ.get("SUPAMEM_BANNER_QUIET", "").strip() != "1":
        payload["systemMessage"] = banner
        # Cursor future-compat (currently a no-op in Cursor's UI per its docs).
        payload["user_message"] = banner
    return payload


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
