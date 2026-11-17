"""``supamem daemon serve`` — the shared model daemon (v0.4.0a6, ADR-0005).

Protocol: one newline-terminated JSON request per connection, one newline-terminated JSON
reply. Ops:

* ``{"v":1,"op":"ping"}`` → pid / version / uptime / rss / loaded models (+dtype) / counters
* ``{"v":1,"op":"query","client_version":..,"config":{ResolvedConfig fields},"text":..,
  "k":..,"where":..}`` → ``{"ok":true,"chunks":[RetrievedChunk json],"version":..}``
* ``{"v":1,"op":"shutdown"}`` → finish in-flight work, remove the socket, exit

Guarantees:
* exactly one daemon per cache root — a process-lifetime pid ``FileLock`` (fcntl; the kernel
  drops it even on ``kill -9``); a second ``serve`` exits 0;
* one query at a time over ONE pooled copy of each model; at most ``MAX_WAITERS`` queued
  requests, beyond that an immediate ``busy`` reply (clients skip, never load in-process);
* idle exit only when nothing is in flight; the socket is unlinked BEFORE the server stops
  accepting, so a racing client sees "unavailable" and respawns rather than hanging;
* a request from a NEWER supamem client (pipx upgrade) is served, then the daemon retires
  so the next client spawns the new code;
* request bodies are never logged (they carry the Qdrant API key).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

from supamem import __version__
from supamem.daemon import (
    MAX_REQUEST_BYTES,
    PROTOCOL_V,
    run_dir,
    socket_path,
)

log = logging.getLogger("supamem.daemon")

MAX_WAITERS = 8
_BACKEND_CACHE_MAX = 16

BackendFactory = Callable[[Any], Any]


def config_from_dict(data: dict[str, Any]) -> Any:
    """Rebuild a ``ResolvedConfig`` from the client's ``asdict`` payload (unknown keys —
    e.g. a newer client's fields — are ignored; missing keys take dataclass defaults)."""
    from supamem.config import ResolvedConfig  # noqa: PLC0415

    known = {f.name for f in fields(ResolvedConfig)}
    return ResolvedConfig(**{k: v for k, v in data.items() if k in known})


def _default_backend_factory(cfg: Any) -> Any:
    from supamem.retrieval.tuned_hybrid import TunedHybridBackend  # noqa: PLC0415

    return TunedHybridBackend(config=cfg)


def _rss_kb() -> int | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        return int(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _newer(client_version: str | None) -> bool:
    if not client_version:
        return False
    try:
        from packaging.version import Version  # noqa: PLC0415

        return Version(client_version) > Version(__version__)
    except Exception:  # noqa: BLE001 — unparsable version → never retire on it
        return False


class DaemonState:
    """All mutable daemon state; ``dispatch`` is the transport-independent core (tests
    drive it directly)."""

    def __init__(self, *, idle_seconds: float, backend_factory: BackendFactory | None = None):
        self.idle_seconds = idle_seconds
        self.backend_factory = backend_factory or _default_backend_factory
        self.started = time.monotonic()
        self.last_activity = time.monotonic()
        self.in_flight = 0
        self.served = 0
        self.busy_rejected = 0
        self.expired = 0
        self.closing = False
        self.retire = False
        self._lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._waiters = threading.BoundedSemaphore(MAX_WAITERS)
        self._backends: OrderedDict[str, Any] = OrderedDict()

    # ---- lifecycle -------------------------------------------------------------------
    def should_exit(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.in_flight:
                return False
            if self.retire:
                return True
            return self.idle_seconds > 0 and (now - self.last_activity) >= self.idle_seconds

    # ---- dispatch --------------------------------------------------------------------
    def dispatch(self, req: Any) -> dict[str, Any]:
        if not isinstance(req, dict):
            return {"ok": False, "error": "bad_request"}
        if req.get("v") != PROTOCOL_V:
            return {"ok": False, "error": "protocol", "protocol": PROTOCOL_V}
        op = req.get("op")
        if op == "ping":
            return self._ping()
        if self.closing:
            return {"ok": False, "error": "closing"}
        if op == "shutdown":
            with self._lock:
                self.retire = True
            return {"ok": True, "version": __version__}
        if op == "query":
            return self._query(req)
        return {"ok": False, "error": "bad_op"}

    def _ping(self) -> dict[str, Any]:
        from supamem.retrieval import model_pool  # noqa: PLC0415

        with self._lock:
            snap = {
                "in_flight": self.in_flight,
                "served": self.served,
                "busy_rejected": self.busy_rejected,
                "expired": self.expired,
                "idle_for_s": round(time.monotonic() - self.last_activity, 1),
            }
        return {
            "ok": True,
            "pid": os.getpid(),
            "version": __version__,
            "protocol": PROTOCOL_V,
            "uptime_s": round(time.monotonic() - self.started, 1),
            "idle_seconds": self.idle_seconds,
            "rss_kb": _rss_kb(),
            "models": model_pool.loaded(),
            "dtypes": model_pool.dtypes(),
            "closing": self.closing,
            **snap,
        }

    def _backend_for(self, cfg_dict: dict[str, Any]) -> Any:
        # Cache per FULL config: TunedHybridBackend.query reads ~10 config fields, so two
        # projects sharing a collection but differing in rerank/decay settings must not
        # share a backend. Heavy models are shared anyway via the process-wide model pool.
        key = hashlib.sha256(
            json.dumps(cfg_dict, sort_keys=True, default=str).encode()
        ).hexdigest()
        backend = self._backends.get(key)
        if backend is None:
            backend = self.backend_factory(config_from_dict(cfg_dict))
            self._backends[key] = backend
            while len(self._backends) > _BACKEND_CACHE_MAX:
                self._backends.popitem(last=False)
        else:
            self._backends.move_to_end(key)
        return backend

    def _query(self, req: dict[str, Any]) -> dict[str, Any]:
        cfg_dict = req.get("config")
        text = req.get("text")
        if not isinstance(cfg_dict, dict) or not isinstance(text, str):
            return {"ok": False, "error": "bad_request"}
        try:
            k = int(req.get("k", 5))
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad_request"}
        where = req.get("where")
        try:
            deadline_s = float(req.get("deadline_s") or 0.0)
        except (TypeError, ValueError):
            deadline_s = 0.0
        if not self._waiters.acquire(blocking=False):
            with self._lock:
                self.busy_rejected += 1
            return {"ok": False, "error": "busy"}
        t0 = time.perf_counter()
        with self._lock:
            self.in_flight += 1
            self.last_activity = time.monotonic()
        try:
            with self._query_lock:
                # The client gives up after `deadline_s`; a request that waited in the queue
                # past it is dead work — running it would only delay the live requests
                # behind it (2026-11-17: abandoned cold-start requests clogged the queue).
                waited = time.perf_counter() - t0
                if deadline_s and waited >= deadline_s:
                    with self._lock:
                        self.expired += 1
                    return {"ok": False, "error": "expired"}
                backend = self._backend_for(cfg_dict)
                chunks = backend.query(text, k=k, where=where)
            payload = [c.model_dump(mode="json") for c in chunks]
            if _newer(req.get("client_version")):
                with self._lock:
                    self.retire = True
                log.info("newer client %s seen — retiring after drain", req.get("client_version"))
            return {"ok": True, "chunks": payload, "version": __version__}
        except Exception as exc:  # noqa: BLE001 — surfaced to the client, logged here
            log.warning("query failed (collection=%s): %s: %s",
                        cfg_dict.get("collection"), type(exc).__name__, exc)
            return {"ok": False, "error": "query_failed",
                    "detail": f"{type(exc).__name__}: {exc}"[:500]}
        finally:
            with self._lock:
                self.in_flight -= 1
                self.served += 1
                self.last_activity = time.monotonic()
            self._waiters.release()
            log.info("query collection=%s k=%s %.0f ms", cfg_dict.get("collection"), k,
                     (time.perf_counter() - t0) * 1000.0)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # noqa: D401
        state: DaemonState = self.server.state  # type: ignore[attr-defined]
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_REQUEST_BYTES:
            resp: dict[str, Any] = {"ok": False, "error": "too_large"}
        else:
            try:
                resp = state.dispatch(json.loads(raw))
            except (ValueError, UnicodeDecodeError):
                resp = {"ok": False, "error": "bad_json"}
        try:
            self.wfile.write(json.dumps(resp).encode() + b"\n")
            self.wfile.flush()
        except OSError:
            pass  # client went away (its own timeout) — nothing to do


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _watch(server: _Server, state: DaemonState, sock: Path, done: threading.Event) -> None:
    interval = 0.2 if 0 < state.idle_seconds < 5 else 2.0
    while not done.is_set():
        if state.should_exit():
            state.closing = True
            try:
                sock.unlink()  # racing clients now see "unavailable" and respawn
            except FileNotFoundError:
                pass
            server.shutdown()
            done.set()
            return
        done.wait(interval)


def serve(
    idle_seconds: float,
    *,
    sock: Path | None = None,
    backend_factory: BackendFactory | None = None,
    ready: threading.Event | None = None,
) -> int:
    """Run the daemon until idle / retire / shutdown. Returns the process exit code."""
    from filelock import FileLock, Timeout  # noqa: PLC0415

    # Models must never be fetched from the network inside a query (an edit hook waits on
    # it); `supamem install`/`repair` pre-fetch them. Set before any HF import.
    os.environ["HF_HUB_OFFLINE"] = "1"
    rdir = run_dir()
    pid_lock = FileLock(str(rdir / "daemon.pid.lock"))
    try:
        pid_lock.acquire(timeout=0)
    except Timeout:
        log.info("another supamem daemon already holds %s — exiting", rdir / "daemon.pid.lock")
        return 0
    sock = sock or socket_path()
    try:
        try:
            sock.unlink()  # stale: we hold the pid lock, so no live daemon owns it
        except FileNotFoundError:
            pass
        old_umask = os.umask(0o077)
        try:
            server = _Server(str(sock), _Handler)
        finally:
            os.umask(old_umask)
        os.chmod(sock, 0o600)
        state = DaemonState(idle_seconds=idle_seconds, backend_factory=backend_factory)
        server.state = state  # type: ignore[attr-defined]
        done = threading.Event()
        threading.Thread(target=server.serve_forever, name="supamem-daemon-accept",
                         daemon=True).start()
        log.info("supamem daemon %s listening on %s (pid %d, idle %.0fs)",
                 __version__, sock, os.getpid(), idle_seconds)
        if ready is not None:
            ready.set()
        if backend_factory is None:
            # Import the query code now (not the models) so a pipx upgrade mid-life can never
            # make a later lazy import load half-new code.
            try:
                import supamem.rerankers.mxbai_v2  # noqa: F401, PLC0415
                import supamem.retrieval.tuned_hybrid  # noqa: F401, PLC0415
            except Exception as exc:  # noqa: BLE001
                log.warning("warm import failed: %s: %s", type(exc).__name__, exc)
        _watch(server, state, sock, done)
        server.server_close()
        log.info("supamem daemon exiting (served=%d, busy_rejected=%d)",
                 state.served, state.busy_rejected)
        return 0
    finally:
        pid_lock.release()


def main_serve(idle_seconds: float) -> int:
    import faulthandler  # noqa: PLC0415
    import signal  # noqa: PLC0415

    # `kill -USR1 <pid>` dumps every thread's Python stack into the daemon log — the only
    # way to see a stuck query without root (py-spy needs it on macOS).
    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return serve(idle_seconds)
