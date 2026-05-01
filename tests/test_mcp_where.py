"""CLASS-03 — where parameter on MCP retrieval (D-02, D-04, D-17)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.mcp_server import build_app, dual_memory_search


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {
        "qdrant_url": "http://localhost:6333",
        "collection": "test_mcp_where",
    }
    base.update(overrides)
    return ResolvedConfig(**base)


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch _get_backend to return a MagicMock capturing query() kwargs."""
    import supamem.mcp_server as mod

    fb = MagicMock()
    fb.query.return_value = []
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fb)
    return fb


@pytest.mark.asyncio
async def test_canonical_threads_where_to_backend(fake_backend: MagicMock) -> None:
    await dual_memory_search(
        query="x", top_k=5, where={"room": "backend"}, config=_cfg()
    )
    # backend.query is called via asyncio.to_thread(backend.query, q, k, where=...)
    kwargs = fake_backend.query.call_args.kwargs
    assert kwargs.get("where") == {"room": "backend"}


@pytest.mark.asyncio
async def test_canonical_default_where_is_none(fake_backend: MagicMock) -> None:
    await dual_memory_search(query="x", top_k=5, config=_cfg())
    kwargs = fake_backend.query.call_args.kwargs
    assert kwargs.get("where") is None


@pytest.mark.asyncio
async def test_multi_key_where_passes_through(fake_backend: MagicMock) -> None:
    w = {"room": "backend", "chunker": "markdown_header"}
    await dual_memory_search(query="x", top_k=5, where=w, config=_cfg())
    assert fake_backend.query.call_args.kwargs.get("where") == w


@pytest.mark.asyncio
async def test_list_value_where_passes_through(fake_backend: MagicMock) -> None:
    w = {"room": ["backend", "tests"]}
    await dual_memory_search(query="x", top_k=5, where=w, config=_cfg())
    assert fake_backend.query.call_args.kwargs.get("where") == w


def _tool_input_schema(app: Any, name: str) -> dict:
    """Pull the JSON Schema for a registered tool by name."""
    tool = app._tool_manager._tools[name]  # type: ignore[attr-defined]
    schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
    assert schema is not None, f"tool {name!r} has no schema"
    return schema


def test_alias_schema_parity_on_where_field() -> None:
    """qdrant_find and dual_memory_search must share where Field text byte-for-byte (D-17)."""
    app = build_app(_cfg())
    canon_schema = _tool_input_schema(app, "dual_memory_search")
    alias_schema = _tool_input_schema(app, "qdrant_find")

    canonical = canon_schema.get("properties", {}).get("where", {})
    alias = alias_schema.get("properties", {}).get("where", {})

    assert canonical, "canonical dual_memory_search.where missing from JSON schema"
    assert alias, "qdrant_find.where missing from JSON schema"

    # Byte-identity on description (the anti-drift invariant per D-17)
    assert canonical["description"] == alias["description"], (
        "qdrant_find.where.description drifted from dual_memory_search — "
        "both must reference the SAME WHERE_DESC constant in mcp_server.py"
    )
    # Type/shape parity — same JSON Schema rendering
    assert canonical.get("type") == alias.get("type")
    assert canonical.get("anyOf") == alias.get("anyOf")  # Optional[...] renders here


@pytest.mark.asyncio
async def test_alias_threads_where_to_backend(
    fake_backend: MagicMock,
) -> None:
    """qdrant_find alias delegates to dual_memory_search with where intact."""
    app = build_app(_cfg())
    await app._tool_manager.call_tool(  # type: ignore[attr-defined]
        "qdrant_find", {"query": "x", "top_k": 5, "where": {"room": "backend"}}
    )
    kwargs = fake_backend.query.call_args.kwargs
    assert kwargs.get("where") == {"room": "backend"}
