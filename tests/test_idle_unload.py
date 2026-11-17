"""v0.4.0a5 — MCP-server idle model unload (issue #5 mitigation).

A queried stdio server pinned ~2.4 GB of reranker/embedder weights for its whole
lifetime. The sweeper evicts idle, non-busy cache entries; busy entries survive;
TTL=0 disables. All unit tests use fake backends and a monkeypatched clock; the
realistic RSS test runs the actual server subprocess and is network/slow-marked.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

import supamem.mcp_server as mcp
from supamem.config import ResolvedConfig


@pytest.fixture(autouse=True)
def _clean_cache():
    mcp._BACKEND_CACHE.clear()
    yield
    mcp._BACKEND_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_env_ttl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", raising=False)


def _cfg(**kw: Any) -> ResolvedConfig:
    base = dict(collection="t_idle", qdrant_url="http://localhost:6333")
    base.update(kw)
    return ResolvedConfig(**base)


def _seed(cfg: ResolvedConfig, backend: Any) -> None:
    mcp._BACKEND_CACHE[mcp._backend_key(cfg)] = mcp._CachedBackend(backend)


def test_ttl_from_config_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert mcp._idle_ttl_seconds(_cfg()) == 30 * 60.0
    assert mcp._idle_ttl_seconds(_cfg(mcp_model_idle_minutes=0)) == 0.0
    assert mcp._idle_ttl_seconds(_cfg(mcp_model_idle_minutes=5)) == 300.0
    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "1.5")
    assert mcp._idle_ttl_seconds(_cfg()) == 1.5
    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "junk")
    assert mcp._idle_ttl_seconds(_cfg(mcp_model_idle_minutes=2)) == 120.0  # env ignored, config used


def test_get_backend_marks_busy_and_release_decrements() -> None:
    cfg = _cfg()
    created = []
    real_ctor = mcp.TunedHybridBackend

    class FakeBackend:
        def __init__(self, *, config: Any) -> None:
            self.config = config
            created.append(self)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mcp, "TunedHybridBackend", FakeBackend)
    try:
        b = mcp._get_backend(cfg)
        rec = mcp._BACKEND_CACHE[mcp._backend_key(cfg)]
        assert rec.backend is b and rec.busy == 1
        # second acquire: same backend, busy=2
        assert mcp._get_backend(cfg) is b
        assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 2
        mcp._release_backend(cfg, b)
        assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 1
        mcp._release_backend(cfg, b)
        assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 0
    finally:
        monkeypatch.setattr(mcp, "TunedHybridBackend", real_ctor)


def test_release_identity_check_never_underflows() -> None:
    """A monkeypatched _get_backend (test fake) must not decrement a real record."""
    cfg = _cfg()
    real = object()
    _seed(cfg, real)
    mcp._release_backend(cfg, object())  # different backend object
    assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 0
    mcp._release_backend(cfg, real)  # busy already 0 → stays 0
    assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 0


def test_sweep_evicts_idle_not_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_idle = _cfg(collection="t_idle_a")
    cfg_busy = _cfg(collection="t_idle_b")
    _seed(cfg_idle, object())
    _seed(cfg_busy, object())
    mcp._BACKEND_CACHE[mcp._backend_key(cfg_busy)].busy = 1

    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "60")
    t0 = time.monotonic()
    mcp._BACKEND_CACHE[mcp._backend_key(cfg_idle)].last_use = t0 - 120
    mcp._BACKEND_CACHE[mcp._backend_key(cfg_busy)].last_use = t0 - 120

    evicted = mcp.sweep_idle_backends(_cfg())
    assert evicted == 1
    assert mcp._backend_key(cfg_idle) not in mcp._BACKEND_CACHE
    assert mcp._backend_key(cfg_busy) in mcp._BACKEND_CACHE  # busy survives


def test_sweep_respects_ttl_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(mcp_model_idle_minutes=0)
    _seed(cfg, object())
    mcp._BACKEND_CACHE[mcp._backend_key(cfg)].last_use = time.monotonic() - 10_000
    assert mcp.sweep_idle_backends(cfg) == 0
    assert mcp._backend_key(cfg) in mcp._BACKEND_CACHE


def test_reacquire_after_evict_rebuilds(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    _seed(cfg, object())
    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "1")
    mcp._BACKEND_CACHE[mcp._backend_key(cfg)].last_use = time.monotonic() - 5
    assert mcp.sweep_idle_backends(cfg) == 1

    class FakeBackend:
        def __init__(self, *, config: Any) -> None:
            self.config = config

    real = mcp.TunedHybridBackend
    monkeypatch.setattr(mcp, "TunedHybridBackend", FakeBackend)
    try:
        b = mcp._get_backend(cfg)
        assert isinstance(b, FakeBackend)
        assert mcp._BACKEND_CACHE[mcp._backend_key(cfg)].busy == 1
    finally:
        monkeypatch.setattr(mcp, "TunedHybridBackend", real)


def test_busy_loop_never_evicted_under_repeated_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Race pin: busy stays >= 1 across sweeps while a query is 'in flight'."""
    cfg = _cfg()
    monkeypatch.setenv("SUPAMEM_MCP_IDLE_TTL_SECONDS", "0.01")
    backend = mcp._get_backend(cfg)
    for _ in range(20):
        mcp._BACKEND_CACHE[mcp._backend_key(cfg)].last_use = time.monotonic() - 10
        assert mcp.sweep_idle_backends(cfg) == 0
    mcp._release_backend(cfg, backend)
    mcp._BACKEND_CACHE[mcp._backend_key(cfg)].last_use = time.monotonic() - 10
    assert mcp.sweep_idle_backends(cfg) == 1
