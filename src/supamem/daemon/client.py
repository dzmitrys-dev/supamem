"""Client side of the shared model daemon (v0.4.0a6, ADR-0005).

Import-light by contract (the edit hook imports this on every Edit): stdlib + filelock +
``supamem.daemon`` only. ``RetrievedChunk`` (pydantic) is imported lazily on a successful
reply. Error mapping is the safety core of the design:

* ``DaemonUnavailable`` — nothing reachable and spawning failed → caller MAY load in-process
  (the hook does so only under a host-wide lock);
* ``DaemonBusy`` — alive but saturated / slower than ``timeout`` → caller must SKIP, never
  load in-process (that recreates the N x 2.4 GB spike);
* ``DaemonError`` — the query itself failed inside the daemon.
"""
from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from supamem import __version__
from supamem.daemon import (
    MAX_RESPONSE_BYTES,
    PROTOCOL_V,
    DaemonBusy,
    DaemonError,
    DaemonUnavailable,
    idle_seconds_for,
    run_dir,
    socket_path,
)

CONNECT_TIMEOUT_S = 0.5
SPAWN_WAIT_S = 20.0  # cold python start under heavy swap is slow; bind happens before torch
SPAWN_LOCK_TIMEOUT_S = 25.0


def _connect(sock: Path, timeout: float = CONNECT_TIMEOUT_S) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock))
    except OSError:
        s.close()
        raise
    return s


def _roundtrip(s: socket.socket, req: dict[str, Any], timeout: float) -> dict[str, Any] | None:
    """Send one request; return the parsed reply, or ``None`` on EOF before a reply."""
    s.settimeout(timeout)
    s.sendall(json.dumps(req).encode() + b"\n")
    buf = bytearray()
    while True:
        chunk = s.recv(65536)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > MAX_RESPONSE_BYTES:
            raise DaemonError("reply too large")
        if buf.endswith(b"\n"):
            return json.loads(bytes(buf))


def ping(*, timeout: float = 2.0, sock: Path | None = None) -> dict[str, Any] | None:
    """The daemon's status dict, or ``None`` when no daemon answers."""
    try:
        s = _connect(sock or socket_path())
    except OSError:
        return None
    try:
        reply = _roundtrip(s, {"v": PROTOCOL_V, "op": "ping"}, timeout)
        return reply if reply and reply.get("ok") else None
    except (OSError, ValueError, DaemonError):
        return None
    finally:
        s.close()


def stop(*, timeout: float = 5.0) -> bool:
    """Ask a running daemon to retire; True if one acknowledged."""
    try:
        s = _connect(socket_path())
    except OSError:
        return False
    try:
        reply = _roundtrip(s, {"v": PROTOCOL_V, "op": "shutdown"}, timeout)
        return bool(reply and reply.get("ok"))
    except (OSError, ValueError, DaemonError):
        return False
    finally:
        s.close()


def _pid_lock_free(rdir: Path) -> bool:
    from filelock import FileLock, Timeout  # noqa: PLC0415

    lock = FileLock(str(rdir / "daemon.pid.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return False
    lock.release()
    return True


def _spawn_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SUPAMEM_NO_UPDATE_CHECK"] = "1"
    return env


def ensure_running(cfg: Any) -> None:
    """Spawn a daemon unless one answers; raise ``DaemonUnavailable`` if that fails."""
    from filelock import FileLock, Timeout  # noqa: PLC0415

    rdir = run_dir()
    sock = socket_path()
    try:
        with FileLock(str(rdir / "spawn.lock"), timeout=SPAWN_LOCK_TIMEOUT_S):
            if ping(sock=sock) is not None:
                return
            if _pid_lock_free(rdir):
                try:
                    sock.unlink()  # stale socket from a crashed daemon
                except FileNotFoundError:
                    pass
                idle = idle_seconds_for(cfg)
                log_path = rdir / "daemon.log"
                with open(log_path, "ab") as logf:
                    # NEVER inherit stdout: Claude Code waits for the hook's stdout to close,
                    # and an MCP server's stdout is the JSON-RPC channel.
                    subprocess.Popen(  # noqa: S603
                        [sys.executable, "-m", "supamem", "daemon", "serve",
                         "--idle-seconds", str(idle)],
                        stdin=subprocess.DEVNULL,
                        stdout=logf,
                        stderr=logf,
                        close_fds=True,
                        start_new_session=True,
                        env=_spawn_env(),
                    )
            deadline = time.monotonic() + SPAWN_WAIT_S
            while time.monotonic() < deadline:
                if ping(sock=sock, timeout=1.0) is not None:
                    return
                time.sleep(0.1)
    except Timeout as exc:
        raise DaemonUnavailable("spawn lock timeout") from exc
    except OSError as exc:
        raise DaemonUnavailable(f"spawn failed: {exc}") from exc
    raise DaemonUnavailable("daemon did not come up")


def _config_payload(cfg: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(cfg) and not isinstance(cfg, type):
        return dataclasses.asdict(cfg)
    raise DaemonError("config is not a ResolvedConfig dataclass")


def query(
    cfg: Any,
    text: str,
    k: int,
    where: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> list[Any]:
    """Run one retrieval in the shared daemon (spawning it on demand)."""
    req = {
        "v": PROTOCOL_V,
        "op": "query",
        "client_version": __version__,
        "config": _config_payload(cfg),
        "text": text,
        "k": int(k),
        "where": where,
        "deadline_s": float(timeout),
    }
    sock = socket_path()
    for attempt in (1, 2):
        try:
            s = _connect(sock)
        except OSError:
            ensure_running(cfg)  # raises DaemonUnavailable
            try:
                s = _connect(sock)
            except OSError as exc:
                raise DaemonUnavailable(f"connect after spawn failed: {exc}") from exc
        try:
            try:
                reply = _roundtrip(s, req, timeout)
            except socket.timeout as exc:
                raise DaemonBusy(f"no reply within {timeout:.0f}s") from exc
            except ValueError as exc:
                raise DaemonError("malformed reply") from exc
            except OSError as exc:
                reply = None  # reset mid-request — same as EOF
                if attempt == 2:
                    raise DaemonUnavailable(f"connection lost: {exc}") from exc
        finally:
            s.close()
        if reply is None or reply.get("error") == "closing":
            if attempt == 1:
                continue  # daemon retired between connect and reply — read-only, retry once
            raise DaemonUnavailable("daemon closed the connection")
        if reply.get("ok"):
            from supamem.retrieval.types import RetrievedChunk  # noqa: PLC0415

            return [RetrievedChunk.model_validate(c) for c in reply.get("chunks") or []]
        if reply.get("error") in {"busy", "expired"}:
            raise DaemonBusy(f"daemon {reply.get('error')}")
        raise DaemonError(str(reply.get("detail") or reply.get("error") or "unknown"))
    raise DaemonUnavailable("unreachable")  # pragma: no cover — loop always returns/raises
