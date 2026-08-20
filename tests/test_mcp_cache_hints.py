"""SEP-2549 cache-hint tests (Phase 19, L2) + deterministic-ordering guard.

RED phase (TDD): written BEFORE the config key + stamping land. The config
and gate tests fail with AttributeError / missing-SystemExit; the two SDK
-shape guard tests (write tools never carry hints; deterministic order)
pass by design in RED — they pin installed-SDK invariants the lever must
not break.

PROBED API (installed mcp 2.0.0 — recorded per 19-03-PLAN Task 3 PROBE
FIRST): cache hints are a PER-METHOD constructor map,
``MCPServer(cache_hints={method: CacheHint(ttl_ms=..., scope=...)})`` from
``mcp.server.caching``. ``CacheableMethod`` covers list/read/discovery
methods only — ``tools/call`` is NOT cacheable (``validate_cache_hints``
rejects the key; ``CallToolResult`` extends ``Result``, not
``CacheableResult``, and carries no ``ttl_ms``/``cache_scope`` fields; the
``@tool()`` decorator has no per-tool hint parameter). supamem serves no
prompts/resources, so the one usable stampable surface is ``tools/list``:
a TTL there lets 2026-era clients cache the tool registry (d5 ordering
hygiene makes that cache meaningful). Per-tool search-result stamping —
the shape RESEARCH §3.3 L2 assumed — does not exist in this SDK version;
the write-tool exclusion guarantee is therefore airtight by construction
(no tools/call result is ever stampable at all).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver import Context

from tests.conftest import _cfg_with_caps, _mock_backend_with_long_chunks

from supamem.mcp_server import build_app

EXPECTED_TOOL_ORDER = [
    "dual_memory_search",
    "dual_memory_write",
    "qdrant_find",
    "qdrant_store",
]


def _server_cache_hints(app: object) -> dict:
    """Read the validated per-method hint map off the installed MCPServer."""
    server = getattr(app, "_lowlevel_server")
    return dict(getattr(server, "cache_hints"))


# ── 1. Default OFF: no cache-hint surface anywhere ──────────────────────────


def test_default_ttl_zero_no_hints() -> None:
    from supamem.config import ResolvedConfig

    assert ResolvedConfig().mcp_cache_ttl_ms == 0, "cache_ttl_ms must default to 0 (off)"

    app = build_app(_cfg_with_caps())
    assert _server_cache_hints(app) == {}, (
        f"ttl=0 must leave the server's cache-hint map empty, got "
        f"{_server_cache_hints(app)!r}"
    )


# ── 2. Enabled: the stampable surface carries the configured TTL ───────────


def test_ttl_enabled_stamps_cacheable_surface() -> None:
    from mcp.server.caching import CacheHint

    cfg = _cfg_with_caps(mcp_cache_ttl_ms=300)
    app = build_app(cfg)
    hints = _server_cache_hints(app)
    assert hints == {"tools/list": CacheHint(ttl_ms=300, scope="private")}, (
        f"ttl=300 must stamp the one cacheable method supamem serves "
        f"(tools/list, private scope), got: {hints!r}"
    )


# ── 3. Write tools never carry cache hints (stale write-then-read guard) ───


def test_tools_call_results_never_carry_hints() -> None:
    """tools/call is not stampable at all: SDK rejects the key; results have
    no ttl fields. A cached write result could mask write-then-read
    visibility — the SDK shape makes the exclusion airtight for every tool."""
    from mcp.server.caching import CacheHint, validate_cache_hints

    with pytest.raises(ValueError, match="cacheable methods"):
        validate_cache_hints({"tools/call": CacheHint(ttl_ms=300)})


@pytest.mark.asyncio
async def test_no_tool_result_exposes_ttl_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supamem.memory_writer import WriteResult

    _mock_backend_with_long_chunks(monkeypatch, n_hits=1, text_len=80)

    def _fake_write_memory(**_kwargs: object) -> WriteResult:
        return WriteResult(
            summary="saved",
            path="/tmp/x.md",
            topic="x",
            slug="x",
            indexed=True,
            points_added=1,
            error=None,
        )

    from supamem import memory_writer

    monkeypatch.setattr(memory_writer, "write_memory", _fake_write_memory)

    cfg = _cfg_with_caps(mcp_cache_ttl_ms=300)
    app = build_app(cfg)
    calls = {
        "dual_memory_search": {"query": "hi", "top_k": 1},
        "qdrant_find": {"query": "hi", "top_k": 1},
        "dual_memory_write": {"topic": "x", "content": "body"},
        "qdrant_store": {"topic": "x", "content": "body"},
    }
    for name, args in calls.items():
        result = await app._tool_manager.call_tool(  # type: ignore[attr-defined]
            name, args, Context(), convert_result=True
        )
        assert not hasattr(result, "ttl_ms"), (
            f"{name}: tools/call result must not expose ttl_ms (no stale "
            f"write-then-read window)"
        )
        assert not hasattr(result, "cache_scope"), (
            f"{name}: tools/call result must not expose cache_scope"
        )


# ── 4. d5 guard: deterministic registration order across invocations ───────


def test_deterministic_tool_registration_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPAMEM_QDRANT_ALIASES", "1")
    app_a = build_app(_cfg_with_caps())
    app_b = build_app(_cfg_with_caps())
    order_a = list(app_a._tool_manager._tools)  # type: ignore[attr-defined]
    order_b = list(app_b._tool_manager._tools)  # type: ignore[attr-defined]
    assert order_a == order_b, (
        f"registration order must be deterministic (prompt-cache hygiene d5): "
        f"{order_a} != {order_b}"
    )
    assert order_a == EXPECTED_TOOL_ORDER, (
        f"fixed module registration order expected, got: {order_a}"
    )


# ── 5. Validation gate: negative ttl fails closed at boot ──────────────────


def test_load_config_rejects_negative_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from supamem.config import load_config

    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.mcp]\ncache_ttl_ms = -1\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        load_config(tmp_path)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "cache_ttl_ms" in captured.err, (
        f"error must name the offending key, got: {captured.err!r}"
    )
