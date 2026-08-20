"""Tests for ``supamem.mcp_server`` (Plan 80.6-05 Task 1).

Covers tool surface, stdout discipline (MCP framing on stdio transport
forbids any non-MCP byte on stdout), error sanitization (Qdrant URL /
API key never leak in user-facing errors), and the verified FastMCP.run
signature for both stdio and Streamable HTTP transports.
"""
from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {
        "qdrant_url": "http://localhost:6333",
        "collection": "test_mcp_collection",
    }
    base.update(overrides)
    return ResolvedConfig(**base)


def test_build_app_registers_dual_memory_search() -> None:
    from supamem.mcp_server import build_app

    app = build_app(_cfg())
    tool_names = set(app._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert "dual_memory_search" in tool_names


def test_search_tool_returns_searchresult_with_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock TunedHybridBackend; calling the search fn returns a SearchResult."""
    import supamem.mcp_server as mod
    from supamem.retrieval.types import RetrievedChunk

    fake_backend = MagicMock()
    fake_backend.query.return_value = [
        RetrievedChunk(id="1", text="alpha body", score=0.9, source_path="a.md"),
        RetrievedChunk(id="2", text="beta body", score=0.8, source_path="b.md"),
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)

    import asyncio

    out = asyncio.run(mod.dual_memory_search(query="hi", top_k=2, config=_cfg()))
    assert len(out.chunks) == 2
    assert out.chunks[0].text == "alpha body"
    assert out.total_tokens > 0
    assert out.latency_ms >= 0
    # Brand polish: summary_md is populated and contains the supamem brand line.
    assert "supamem" in out.summary_md
    assert "chunks" in out.summary_md or "matches" in out.summary_md


def test_search_tool_summary_md_no_match_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the backend returns zero hits, summary_md says 'no matches'."""
    import asyncio

    import supamem.mcp_server as mod

    fake_backend = MagicMock()
    fake_backend.query.return_value = []
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)

    out = asyncio.run(mod.dual_memory_search(query="missing", top_k=5, config=_cfg()))
    assert "no matches" in out.summary_md
    assert "supamem" in out.summary_md


def test_tool_registered_with_title_and_description(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward-compat brand polish: the registered tool has a human-readable title."""
    from supamem.mcp_server import build_app

    app = build_app(_cfg())
    tool = app._tool_manager._tools["dual_memory_search"]  # type: ignore[attr-defined]
    # The Tool object exposes title (spec ≥ 2025-03-26) and description.
    title = getattr(tool, "title", None) or ""
    description = getattr(tool, "description", None) or ""
    assert "supamem" in title.lower() or "memory" in title.lower()
    assert len(description) > 20


def test_mcp_dual_memory_search_missing_collection_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CollectionMissingError surfaces actionable remediation via RuntimeError."""
    import asyncio
    import io

    import supamem.mcp_server as mod
    from supamem.qdrant_collection import CollectionMissingError

    coll = "ghost_collection"
    fake_backend = MagicMock()
    fake_backend.query.side_effect = CollectionMissingError(coll)
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)

    cap = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = cap
    try:
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(mod.dual_memory_search(query="x", top_k=1, config=_cfg(collection=coll)))
    finally:
        sys.stdout = real_stdout

    msg = str(exc_info.value)
    assert coll in msg
    assert __import__("re").search(r"(?i)supamem (index|init)", msg)
    assert cap.getvalue() == ""


def test_search_tool_sanitizes_qdrant_url_in_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a backend exception leaks QDRANT_URL into its message, sanitize it."""
    import supamem.mcp_server as mod

    secret_url = "https://secret-cluster.example:6333"
    monkeypatch.setenv("QDRANT_URL", secret_url)

    fake_backend = MagicMock()
    fake_backend.query.side_effect = RuntimeError(f"connection failed to {secret_url}")
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake_backend)

    import asyncio

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(mod.dual_memory_search(query="x", top_k=1, config=_cfg()))
    assert secret_url not in str(exc_info.value)
    assert "QDRANT_URL_REDACTED" in str(exc_info.value) or "failed" in str(exc_info.value).lower()


def test_module_import_does_not_touch_stdout() -> None:
    """Stdio transport: importing must not write a single byte to stdout."""
    import importlib

    cap = io.StringIO()
    real = sys.stdout
    sys.stdout = cap
    try:
        if "supamem.mcp_server" in sys.modules:
            importlib.reload(sys.modules["supamem.mcp_server"])
        else:
            import supamem.mcp_server  # noqa: F401
    finally:
        sys.stdout = real
    assert cap.getvalue() == "", f"unexpected stdout: {cap.getvalue()!r}"


def test_log_handlers_route_to_stderr_when_no_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default logging routes to stderr, never stdout."""
    import importlib
    import logging

    monkeypatch.delenv("MCP_LOG_FILE", raising=False)
    if "supamem.mcp_server" in sys.modules:
        importlib.reload(sys.modules["supamem.mcp_server"])
    else:
        import supamem.mcp_server  # noqa: F401

    handlers = logging.getLogger().handlers
    streams = [getattr(h, "stream", None) for h in handlers if isinstance(h, logging.StreamHandler)]
    assert any(s is sys.stderr for s in streams), "expected stderr StreamHandler"
    assert not any(s is sys.stdout for s in streams), "stdout handler is forbidden"


def test_run_stdio_calls_fastmcp_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_stdio must invoke MCPServer.run with transport='stdio'."""
    import supamem.mcp_server as mod
    from mcp.server.mcpserver import MCPServer

    captured: dict[str, Any] = {}

    def fake_run(self: MCPServer, *args: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["transport"] = (
            kwargs.get("transport") if kwargs else (args[0] if args else None)
        )

    monkeypatch.setattr(MCPServer, "run", fake_run)
    mod.run_stdio(_cfg())
    assert captured.get("transport") == "stdio"


def test_run_http_uses_streamable_http_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_http must invoke MCPServer.run with transport='streamable-http' (D-45)."""
    import supamem.mcp_server as mod
    from mcp.server.mcpserver import MCPServer

    captured: dict[str, Any] = {}

    def fake_run(self: MCPServer, *args: Any, **kwargs: Any) -> None:  # noqa: ARG001
        captured["transport"] = (
            kwargs.get("transport") if kwargs else (args[0] if args else None)
        )
        captured["host"] = kwargs.get("host")
        captured["port"] = kwargs.get("port")

    monkeypatch.setattr(MCPServer, "run", fake_run)
    mod.run_http(_cfg(), port=8765, host="127.0.0.1")
    assert captured.get("transport") == "streamable-http"
    assert captured.get("host") == "127.0.0.1"
    assert captured.get("port") == 8765
