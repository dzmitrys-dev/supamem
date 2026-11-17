"""v0.4.0a6 — shared model daemon (ADR-0005): protocol core, socket transport, client error
mapping, lazy spawn/detach, idle exit, model pool sharing, hook fallback discipline."""
from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from supamem.config import ResolvedConfig
from supamem.daemon import DaemonBusy, DaemonError, DaemonUnavailable, server as dserver
from supamem.retrieval import model_pool
from supamem.retrieval.types import RetrievedChunk


def _chunk(text: str = "hello", score: float = 3.0) -> RetrievedChunk:
    return RetrievedChunk(
        id="1", text=text, score=score, source_path="/x/docs/a.md", rerank_score=score
    )


class _FakeBackend:
    instances = 0

    def __init__(self, cfg: Any, *, delay: float = 0.0, fail: bool = False) -> None:
        _FakeBackend.instances += 1
        self.cfg = cfg
        self.delay = delay
        self.fail = fail
        self.calls: list[tuple] = []

    def query(self, text: str, k: int = 5, *, where: Any = None) -> list[RetrievedChunk]:
        self.calls.append((text, k, where))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("qdrant down")
        return [_chunk(f"{self.cfg.collection}:{text}")]


@pytest.fixture
def daemon_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SUPAMEM_DAEMON", "on")
    return tmp_path / "cache"


def _req(op: str = "query", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"v": 1, "op": op}
    if op == "query":
        base.update(config=dataclasses.asdict(ResolvedConfig(collection="c1")), text="q", k=3)
    base.update(kw)
    return base


# ---- protocol core (DaemonState.dispatch) -----------------------------------------------


def test_config_roundtrip_ignores_unknown_keys() -> None:
    cfg = ResolvedConfig(collection="proj", embedder="multilingual_minilm", hook_target_globs=["a/*"])
    data = dataclasses.asdict(cfg)
    data["field_from_newer_client"] = 1
    back = dserver.config_from_dict(json.loads(json.dumps(data)))
    assert back == cfg


def test_dispatch_rejects_bad_requests() -> None:
    st = dserver.DaemonState(idle_seconds=0, backend_factory=_FakeBackend)
    assert st.dispatch([]) == {"ok": False, "error": "bad_request"}
    assert st.dispatch({"v": 99, "op": "ping"})["error"] == "protocol"
    assert st.dispatch({"v": 1, "op": "rm -rf"})["error"] == "bad_op"
    assert st.dispatch(_req(text=5))["error"] == "bad_request"
    assert st.dispatch(_req(k="many"))["error"] == "bad_request"


def test_query_returns_chunks_and_caches_backend_per_full_config() -> None:
    _FakeBackend.instances = 0
    st = dserver.DaemonState(idle_seconds=0, backend_factory=_FakeBackend)
    r1 = st.dispatch(_req())
    r2 = st.dispatch(_req())
    assert r1["ok"] and r1["chunks"][0]["text"] == "c1:q"
    assert _FakeBackend.instances == 1  # same full config → one backend
    other = dataclasses.asdict(ResolvedConfig(collection="c1", reranker_top_n=7))
    st.dispatch(_req(config=other))
    assert _FakeBackend.instances == 2  # same collection, different settings → own backend
    assert r2["ok"] and st.served == 3


def test_query_failure_is_reported_not_raised() -> None:
    st = dserver.DaemonState(idle_seconds=0, backend_factory=lambda c: _FakeBackend(c, fail=True))
    r = st.dispatch(_req())
    assert r["ok"] is False and r["error"] == "query_failed" and "qdrant down" in r["detail"]
    assert st.in_flight == 0


def test_waiter_cap_replies_busy_without_running() -> None:
    st = dserver.DaemonState(idle_seconds=0, backend_factory=_FakeBackend)
    for _ in range(dserver.MAX_WAITERS):
        st._waiters.acquire()
    assert st.dispatch(_req()) == {"ok": False, "error": "busy"}
    assert st.busy_rejected == 1


def test_queries_are_serialized() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    class _Slow(_FakeBackend):
        def query(self, text, k=5, *, where=None):  # noqa: ANN001
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return [_chunk()]

    st = dserver.DaemonState(idle_seconds=0, backend_factory=_Slow)
    threads = [threading.Thread(target=st.dispatch, args=(_req(),)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1 and st.served == 4


def test_idle_exit_waits_for_in_flight_and_retires_on_newer_client() -> None:
    st = dserver.DaemonState(idle_seconds=10, backend_factory=_FakeBackend)
    now = time.monotonic()
    assert not st.should_exit(now)
    assert st.should_exit(now + 11)
    st.in_flight = 1
    assert not st.should_exit(now + 11)  # never while a query runs
    st.in_flight = 0
    st.dispatch(_req(client_version="999.0"))
    assert st.retire and st.should_exit()
    st2 = dserver.DaemonState(idle_seconds=0, backend_factory=_FakeBackend)
    st2.dispatch(_req(client_version="0.0.1"))
    assert not st2.retire and not st2.should_exit(time.monotonic() + 10**6)  # 0 = never idle


def test_closing_and_shutdown_ops() -> None:
    st = dserver.DaemonState(idle_seconds=0, backend_factory=_FakeBackend)
    assert st.dispatch({"v": 1, "op": "shutdown"})["ok"] and st.retire
    st.closing = True
    assert st.dispatch(_req())["error"] == "closing"
    assert st.dispatch({"v": 1, "op": "ping"})["ok"]  # ping always answers


# ---- model pool ------------------------------------------------------------------------


def test_pool_loads_each_model_once_across_threads() -> None:
    loads = 0

    def factory() -> object:
        nonlocal loads
        time.sleep(0.02)
        loads += 1
        return object()

    out: list[object] = []
    ts = [threading.Thread(target=lambda: out.append(model_pool.get("dense", "m", factory)))
          for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert loads == 1 and len({id(o) for o in out}) == 1
    assert model_pool.loaded() == {"dense": ["m"]}
    assert model_pool.clear() == 1 and model_pool.loaded() == {}


def test_backends_for_different_collections_share_models(monkeypatch: pytest.MonkeyPatch) -> None:
    import qdrant_client

    import supamem.retrieval.tuned_hybrid as th

    built: list[str] = []

    class _Emb:
        def __init__(self, model_id: str, **_kw: Any) -> None:
            built.append(model_id)

    monkeypatch.setattr(th, "TextEmbedding", _Emb)
    monkeypatch.setattr(th, "SparseTextEmbedding", _Emb)
    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **kw: object())
    a = th.TunedHybridBackend(config=ResolvedConfig(collection="proj-a"))
    b = th.TunedHybridBackend(config=ResolvedConfig(collection="proj-b"))
    _, dense_a, sparse_a = a._ensure()
    _, dense_b, sparse_b = b._ensure()
    assert dense_a is dense_b and sparse_a is sparse_b
    assert len(built) == 2  # one dense + one sparse for the whole process


def test_reranker_wrappers_share_one_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.rerankers import mxbai_v2

    loads: list[str] = []
    monkeypatch.setattr(mxbai_v2, "_load_model", lambda mid: loads.append(mid) or object())
    r1 = mxbai_v2.MxbaiV2Reranker(config=ResolvedConfig(collection="a"))
    r2 = mxbai_v2.MxbaiV2Reranker(config=ResolvedConfig(collection="b"))
    assert r1._ensure() is r2._ensure() and len(loads) == 1


def test_mcp_idle_sweep_clears_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import supamem.mcp_server as mcp

    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "1")
    mcp._BACKEND_CACHE.clear()
    model_pool.get("reranker", "m", object)
    rec = mcp._CachedBackend(object())
    rec.last_use = time.monotonic() - 10
    mcp._BACKEND_CACHE[("u", "", "c", False)] = rec
    assert mcp.sweep_idle_backends(ResolvedConfig()) == 1
    assert model_pool.loaded() == {}


# ---- socket transport + client ----------------------------------------------------------


@pytest.fixture
def live_daemon(daemon_env: Path):
    """A real socket daemon in a thread with a fake backend factory."""
    from supamem.daemon import socket_path

    holder: dict[str, Any] = {"factory": _FakeBackend}
    ready = threading.Event()
    rc: list[int] = []
    t = threading.Thread(
        target=lambda: rc.append(
            dserver.serve(0.6, backend_factory=lambda c: holder["factory"](c), ready=ready)
        ),
        daemon=True,
    )
    t.start()
    assert ready.wait(10)
    yield {"holder": holder, "thread": t, "sock": socket_path(), "rc": rc}
    from supamem.daemon import client

    client.stop()
    t.join(10)


def test_client_roundtrip_ping_and_idle_exit(live_daemon: dict[str, Any]) -> None:
    from supamem.daemon import client

    chunks = client.query(ResolvedConfig(collection="proj"), "auth flow", 4, {"room": "docs"})
    assert isinstance(chunks[0], RetrievedChunk) and chunks[0].text == "proj:auth flow"
    status = client.ping()
    assert status and status["pid"] == os.getpid() and status["served"] == 1
    assert oct(live_daemon["sock"].stat().st_mode & 0o777) == "0o600"
    live_daemon["thread"].join(10)  # idle 0.6 s → exits by itself
    assert not live_daemon["thread"].is_alive() and live_daemon["rc"] == [0]
    assert not live_daemon["sock"].exists()


def test_client_maps_query_failure_and_timeout(live_daemon: dict[str, Any]) -> None:
    from supamem.daemon import client

    live_daemon["holder"]["factory"] = lambda c: _FakeBackend(c, fail=True)
    with pytest.raises(DaemonError, match="qdrant down"):
        client.query(ResolvedConfig(collection="f1"), "q", 3)
    live_daemon["holder"]["factory"] = lambda c: _FakeBackend(c, delay=1.5)
    with pytest.raises(DaemonBusy):
        client.query(ResolvedConfig(collection="slow"), "q", 3, timeout=0.3)


def test_second_daemon_exits_zero_while_first_holds_pid_lock(live_daemon: dict[str, Any]) -> None:
    assert dserver.serve(0.5, backend_factory=_FakeBackend) == 0
    assert live_daemon["thread"].is_alive()


def test_unreachable_daemon_raises_unavailable(daemon_env: Path, monkeypatch) -> None:
    from supamem.daemon import client

    def _no_spawn(cfg: Any) -> None:
        raise DaemonUnavailable("spawn disabled in test")

    monkeypatch.setattr(client, "ensure_running", _no_spawn)
    with pytest.raises(DaemonUnavailable):
        client.query(ResolvedConfig(), "q", 3)


def test_stale_socket_is_replaced_by_spawn(daemon_env: Path, monkeypatch) -> None:
    from supamem.daemon import client, socket_path

    sock = socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock))
    s.close()  # dangling socket file: connect → refused
    assert sock.exists()
    started = threading.Event()

    def _fake_popen(*_a: Any, **_kw: Any) -> None:
        threading.Thread(
            target=dserver.serve, args=(0.5,), kwargs={"backend_factory": _FakeBackend},
            daemon=True,
        ).start()
        started.set()

    from types import SimpleNamespace

    # Patch only the client's view of subprocess (the server's ps probe needs the real one).
    monkeypatch.setattr(
        client, "subprocess", SimpleNamespace(Popen=_fake_popen, DEVNULL=subprocess.DEVNULL)
    )
    chunks = client.query(ResolvedConfig(collection="s"), "q", 2)
    assert started.is_set() and chunks[0].text == "s:q"
    client.stop()


def test_real_spawn_is_detached_from_parent_stdout(daemon_env: Path) -> None:
    """Spawned by a process whose stdout is a pipe: the pipe must reach EOF while the daemon
    lives (Claude Code waits for the hook's stdout to close; MCP stdout is JSON-RPC)."""
    env = {**os.environ, "SUPAMEM_DAEMON_IDLE_SECONDS": "3", "SUPAMEM_NO_UPDATE_CHECK": "1"}
    code = (
        "from supamem.config import ResolvedConfig\n"
        "from supamem.daemon import client\n"
        "client.ensure_running(ResolvedConfig())\n"
        "print('spawned', flush=True)\n"
    )
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "spawned" in proc.stdout and time.monotonic() - t0 < 60  # pipe closed → run returned
    from supamem.daemon import client

    status = client.ping()
    assert status and status["pid"] != os.getpid()
    deadline = time.monotonic() + 20
    while client.ping() is not None and time.monotonic() < deadline:
        time.sleep(0.3)
    assert client.ping() is None  # idle 3 s → exited, zero footprint


# ---- hook discipline --------------------------------------------------------------------


def test_hook_import_stays_light() -> None:
    code = (
        "import sys, supamem.hooks.claude_code, supamem.daemon.client\n"
        "heavy=[m for m in ('fastembed','torch','qdrant_client','onnxruntime','transformers')"
        " if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_hook_backend_uses_daemon_and_never_loads_locally_when_busy(
    daemon_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import supamem.hooks.claude_code as hook
    from supamem.daemon import client

    monkeypatch.setattr(client, "query", lambda cfg, text, k, where, timeout: [_chunk(text)])
    hb = hook._HookBackend(ResolvedConfig())
    assert hb.query("auth", k=3)[0].text == "auth"

    def _busy(*_a: Any, **_kw: Any) -> None:
        raise DaemonBusy("full")

    monkeypatch.setattr(client, "query", _busy)
    monkeypatch.setattr(hook._HookBackend, "_query_local", lambda *a: pytest.fail("loaded locally"))
    with pytest.raises(DaemonBusy):
        hook._HookBackend(ResolvedConfig()).query("auth", k=3)


def test_hook_falls_back_in_process_only_when_unreachable_and_lock_free(
    daemon_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from filelock import FileLock

    import supamem.hooks.claude_code as hook
    import supamem.retrieval.tuned_hybrid as th
    from supamem.daemon import client, run_dir

    def _unreachable(*_a: Any, **_kw: Any) -> None:
        raise DaemonUnavailable("down")

    monkeypatch.setattr(client, "query", _unreachable)
    monkeypatch.setattr(th, "TunedHybridBackend", lambda config: _FakeBackend(config))
    assert hook._HookBackend(ResolvedConfig(collection="fb")).query("x", k=2)[0].text == "fb:x"

    held = FileLock(str(run_dir() / "hook-inproc.lock"))
    held.acquire()
    try:
        monkeypatch.setattr(hook, "_INPROC_LOCK_TIMEOUT_S", 0.2)
        with pytest.raises(hook._InprocBusy):
            hook._HookBackend(ResolvedConfig(collection="fb")).query("x", k=2)
    finally:
        held.release()


def test_hook_run_emits_once_when_daemon_busy(
    tmp_path: Path, daemon_env: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import supamem.hooks.claude_code as hook
    from supamem.daemon import client

    def _busy(*_a: Any, **_kw: Any) -> None:
        raise DaemonBusy("full")

    monkeypatch.setattr(client, "query", _busy)
    monkeypatch.setattr(hook, "_marker_dir", lambda: tmp_path)
    hook._BACKEND_CACHE.clear()
    target = tmp_path / "src" / "auth" / "session.py"
    rc = hook.run(target, ResolvedConfig(collection="busy"), {"cwd": str(tmp_path)})
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and len(out) == 1
    assert json.loads(out[0])["hookSpecificOutput"]["additionalContext"] == ""


def test_request_that_outwaited_its_deadline_is_not_run() -> None:
    gate = threading.Event()

    class _Blocking(_FakeBackend):
        def query(self, text, k=5, *, where=None):  # noqa: ANN001
            if text == "first":
                gate.wait(5)
            return [_chunk(text)]

    st = dserver.DaemonState(idle_seconds=0, backend_factory=_Blocking)
    first = threading.Thread(target=st.dispatch, args=(_req(text="first"),))
    first.start()
    time.sleep(0.05)
    out: list[dict] = []
    second = threading.Thread(
        target=lambda: out.append(st.dispatch(_req(text="second", deadline_s=0.1)))
    )
    second.start()
    time.sleep(0.3)  # second now waited past its deadline behind `first`
    gate.set()
    first.join()
    second.join()
    assert out == [{"ok": False, "error": "expired"}] and st.expired == 1
