"""Shared model daemon (v0.4.0a6) — one copy of the retrieval models per machine.

Before a6 every process that ran a retrieval (one stdio MCP server per Claude Code window,
plus a fresh ``supamem hook claude-code`` process per Edit/Write) loaded its own ~2.4 GB of
models; several windows editing at once pushed the owner's Mac to 27.4/28 GB swap and a
single hook call peaked at 8.66 GB footprint. The daemon holds the models once and serves
queries over a user-only Unix socket; it is spawned lazily by the first client and exits by
itself after an idle period (zero footprint when unused). See ADR-0005.

This module is import-light on purpose: the edit hook imports it on every edit, so it must
not pull in fastembed / torch / qdrant_client.
"""
from __future__ import annotations

import hashlib
import os
import socket
import tempfile
from pathlib import Path
from typing import Any

import platformdirs

PROTOCOL_V = 1
MAX_REQUEST_BYTES = 1 << 20  # 1 MiB
MAX_RESPONSE_BYTES = 16 << 20  # 16 MiB
# macOS sun_path is 104 bytes incl. the NUL terminator; Linux is 108.
_SUN_PATH_MAX = 103

ENV_SWITCH = "SUPAMEM_DAEMON"  # off|0|false disables, on|1|true forces
ENV_IDLE_SECONDS = "SUPAMEM_DAEMON_IDLE_SECONDS"


class DaemonUnavailable(Exception):  # noqa: N818 — reads as a state, matches siblings
    """No daemon could be reached or spawned — the caller may use its in-process path."""


class DaemonBusy(Exception):  # noqa: N818
    """The daemon is alive but saturated or too slow — the caller must NOT load models
    in-process (that would recreate the N x 2.4 GB spike); skip instead."""


class DaemonError(Exception):
    """The daemon ran the request and it failed (e.g. Qdrant down, collection missing)."""


def cache_root() -> Path:
    """Same root the reranker cache uses (``SUPAMEM_CACHE_DIR`` override for tests)."""
    override = os.environ.get("SUPAMEM_CACHE_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir("supamem"))


def run_dir() -> Path:
    """User-only runtime dir holding the socket, pid lock, spawn lock and daemon log."""
    d = cache_root() / "run"
    d.mkdir(parents=True, exist_ok=True)
    _secure_dir(d)
    return d


def _secure_dir(d: Path) -> None:
    try:
        st = d.stat()
        if st.st_uid != os.getuid():
            raise PermissionError(f"supamem daemon dir {d} is not owned by the current user")
        if st.st_mode & 0o077:
            d.chmod(0o700)
    except AttributeError:  # pragma: no cover — os.getuid missing (Windows)
        pass


def socket_path() -> Path:
    """``<cache_root>/run/daemon.sock``, or a short ``/tmp`` fallback when that path would
    exceed the AF_UNIX ``sun_path`` limit (pytest tmp dirs, deep SUPAMEM_CACHE_DIR)."""
    preferred = run_dir() / "daemon.sock"
    if len(os.fsencode(str(preferred))) <= _SUN_PATH_MAX:
        return preferred
    digest = hashlib.sha256(str(cache_root()).encode()).hexdigest()[:8]
    short_dir = Path(tempfile.gettempdir() if os.name != "posix" else "/tmp") / (
        f"supamem-{os.getuid()}"
    )
    short_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _secure_dir(short_dir)
    return short_dir / f"{digest}.sock"


def supported() -> bool:
    return hasattr(socket, "AF_UNIX") and os.name == "posix"


def daemon_enabled(cfg: Any) -> bool:
    """``SUPAMEM_DAEMON`` env wins; then ``[supamem.daemon] enabled`` (default on)."""
    if not supported():
        return False
    env = os.environ.get(ENV_SWITCH, "").strip().lower()
    if env in {"off", "0", "false", "no"}:
        return False
    if env in {"on", "1", "true", "yes"}:
        return True
    return bool(getattr(cfg, "daemon_enabled", True))


def idle_seconds_for(cfg: Any) -> float:
    """Daemon idle-exit TTL: env override (tests) → ``mcp_model_idle_minutes`` (0 = never)."""
    raw = os.environ.get(ENV_IDLE_SECONDS)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    minutes = getattr(cfg, "mcp_model_idle_minutes", 30)
    try:
        return max(0.0, float(minutes) * 60.0)
    except (TypeError, ValueError):
        return 1800.0


__all__ = [
    "DaemonBusy",
    "DaemonError",
    "DaemonUnavailable",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "PROTOCOL_V",
    "cache_root",
    "daemon_enabled",
    "idle_seconds_for",
    "run_dir",
    "socket_path",
    "supported",
]
