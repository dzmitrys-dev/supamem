"""Wire-contract tests for single-arm CallToolResult returns (Phase 19, L1).

RED phase (TDD): written BEFORE the L1 handler change lands. Failures MUST
be assertion failures, never ImportError. Tests that are regression guards
(output-schema advertisement, secret redaction) may pass in RED by design —
the contract tests on the TextContent arm are the RED signal.

Cross-reference: 19-03-PLAN.md Task 1 <behavior>; RESEARCH §3.3 L1 + d2
(the MCP spec's own list_users example validates summary-in-TextContent +
canonical-structuredContent), Pitfalls 4 + 6 (canonical arm complete; no
full-JSON twin arm on ANY registered tool).

Patterns lifted from tests/test_mcp_caps.py: call_tool invocation through
``app._tool_manager`` (the SDK wrap/validate layer L1 changes), tool-registry
introspection for schema checks, conftest ``_cfg_with_caps`` +
``_mock_backend_with_long_chunks`` seam, and the capsys stdout-discipline
tail on every green path (stdio purity).

Determinism: ``supamem.mcp_server.time.perf_counter`` is pinned (constant)
so the direct ``dual_memory_search`` call and the wire call serialize
identical ``latency_ms`` — the byte-shape comparison would otherwise flake
on wall-clock jitter (plan 19-02 Test 2 precedent).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

# Module-level helpers (NOT fixtures — they take args).
from tests.conftest import _cfg_with_caps, _mock_backend_with_long_chunks

from supamem.memory_writer import WriteResult
from supamem.mcp_server import build_app, dual_memory_search

READ_TOOLS = ("dual_memory_search", "qdrant_find")
WRITE_TOOLS = ("dual_memory_write", "qdrant_store")
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS


def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin perf_counter → latency_ms serializes identically across calls."""
    monkeypatch.setattr("supamem.mcp_server.time.perf_counter", lambda: 1234.5)


async def _call(app: object, name: str, args: dict) -> CallToolResult:
    """Invoke a tool through the SDK tool-manager wrap/validate layer."""
    return await app._tool_manager.call_tool(  # type: ignore[attr-defined]
        name, args, Context(), convert_result=True
    )


def _assert_stdout_pure(capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    assert captured.out == "", (
        f"stdout pollution on tool-call path violates JSON-RPC purity: {captured.out!r}"
    )


def _fake_write_result() -> WriteResult:
    return WriteResult(
        summary="Saved memory 'wire-contract' (3 points)",
        path="/tmp/wire-contract.md",
        topic="wire-contract",
        slug="wire-contract",
        indexed=True,
        points_added=3,
        error=None,
    )


def _patch_write_memory(monkeypatch: pytest.MonkeyPatch) -> WriteResult:
    """Patch the write path (function-local import site: the real module attr)."""
    from supamem import memory_writer

    res = _fake_write_result()

    def _fake_write_memory(**_kwargs: object) -> WriteResult:
        return res

    monkeypatch.setattr(memory_writer, "write_memory", _fake_write_memory)
    return res


# ── 1. Read tools: TextContent = summary card, structuredContent = complete ─


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", READ_TOOLS)
async def test_read_tool_single_arm_contract(
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_clock(monkeypatch)
    _mock_backend_with_long_chunks(monkeypatch, n_hits=3, text_len=600)
    cfg = _cfg_with_caps(max_preview_chars=50)
    app = build_app(cfg)

    expected = await dual_memory_search(query="wire contract probe", top_k=3, config=cfg)
    wire = await _call(app, tool, {"query": "wire contract probe", "top_k": 3})

    assert isinstance(wire, CallToolResult)
    # TextContent arm: exactly ONE block carrying the compact summary card.
    assert len(wire.content) == 1, (
        f"{tool}: expected exactly 1 content block, got {len(wire.content)}"
    )
    assert isinstance(wire.content[0], TextContent)
    assert wire.content[0].text == expected.summary_md, (
        f"{tool}: TextContent must carry summary_md only (L1), got: "
        f"{wire.content[0].text[:120]!r}..."
    )
    # Canonical arm: complete SearchResult dump, byte-shape identical (Pitfall 4).
    assert wire.structured_content == expected.model_dump(mode="json", by_alias=True), (
        f"{tool}: structuredContent must equal the canonical SearchResult dump"
    )
    sc = wire.structured_content
    assert len(sc["chunks"]) == 3, "chunks list must be complete"
    for chunk in sc["chunks"]:
        assert chunk["text"] == "x" * 600, "full chunk text must ride the canonical arm intact"
        assert chunk["preview"], "preview must be present in detailed mode (CAPS-02)"
        assert len(chunk["preview"]) <= 50
    assert sc["total_tokens"] == expected.total_tokens
    assert "latency_ms" in sc and "clamped_to" in sc
    _assert_stdout_pure(capsys)


# ── 2. Write tools: TextContent = write summary, structuredContent = dict ──


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", WRITE_TOOLS)
async def test_write_tool_single_arm_contract(
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    res = _patch_write_memory(monkeypatch)
    app = build_app(_cfg_with_caps())

    wire = await _call(app, tool, {"topic": "wire-contract", "content": "body text"})

    assert isinstance(wire, CallToolResult)
    assert len(wire.content) == 1, (
        f"{tool}: expected exactly 1 content block, got {len(wire.content)}"
    )
    assert isinstance(wire.content[0], TextContent)
    assert wire.content[0].text == res.summary, (
        f"{tool}: TextContent must carry the write summary string only, got: "
        f"{wire.content[0].text[:120]!r}..."
    )
    assert set(wire.structured_content.keys()) == {
        "summary",
        "path",
        "topic",
        "slug",
        "indexed",
        "points_added",
        "error",
    }
    assert wire.structured_content["summary"] == res.summary
    assert wire.structured_content["points_added"] == 3
    _assert_stdout_pure(capsys)


# ── 3. Anti-double-wrap: the full-JSON twin arm is gone on ALL 4 tools ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_no_full_json_twin_arm(
    tool: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _pin_clock(monkeypatch)
    _mock_backend_with_long_chunks(monkeypatch, n_hits=3, text_len=400)
    _patch_write_memory(monkeypatch)
    app = build_app(_cfg_with_caps())

    if tool in READ_TOOLS:
        wire = await _call(app, tool, {"query": "twin arm probe", "top_k": 3})
    else:
        wire = await _call(app, tool, {"topic": "wire-contract", "content": "body"})

    assert len(wire.content) == 1, (
        f"{tool}: exactly one content block (Pitfall 6), got {len(wire.content)}"
    )
    text = wire.content[0].text or ""
    serialized = json.dumps(wire.structured_content, default=str, sort_keys=False)
    assert serialized not in text, f"{tool}: compact-JSON twin arm leaked into TextContent"
    assert not text.lstrip().startswith("{"), (
        f"{tool}: TextContent looks like a JSON dump (SDK double-wrap, Pitfall 6): "
        f"{text[:80]!r}..."
    )
    if tool in READ_TOOLS:
        assert "x" * 400 not in text, (
            f"{tool}: full chunk payload must not ride the TextContent arm (L1)"
        )
    _assert_stdout_pure(capsys)


# ── 4. Read tools still advertise the SearchResult-derived output schema ────


def test_read_tools_advertise_output_schema() -> None:
    app = build_app(_cfg_with_caps())
    for name in READ_TOOLS:
        tool = app._tool_manager._tools[name]  # type: ignore[attr-defined]
        schema = getattr(tool, "output_schema", None) or getattr(tool, "outputSchema", None)
        assert schema, f"{name} must advertise an output schema (Annotated return form)"
        props = schema.get("properties", {})
        # Derived from SearchResult: its distinctive fields must be present.
        assert {"summary_md", "chunks", "total_tokens"} <= set(props), (
            f"{name}: output schema not derived from SearchResult: {sorted(props)}"
        )


# ── 5. Security: backend errors surface redacted, secret never in any arm ──


@pytest.mark.asyncio
async def test_error_path_redacts_secret_never_leaks_to_arms(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "http://supa-secret-qdrant-internal:6333"
    monkeypatch.setenv("QDRANT_URL", secret)

    def _raising_query(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"connect to {secret} refused")

    fake = MagicMock()
    fake.query.side_effect = _raising_query
    monkeypatch.setattr("supamem.mcp_server._get_backend", lambda cfg: fake)

    app = build_app(_cfg_with_caps())
    with pytest.raises(ToolError) as exc_info:
        await _call(app, "dual_memory_search", {"query": "hi", "top_k": 1})
    msg = str(exc_info.value)
    assert secret not in msg, f"secret leaked through ToolError: {msg!r}"
    assert "<QDRANT_URL_REDACTED>" in msg, f"redaction placeholder missing: {msg!r}"

    # Green path afterwards: the secret must never appear in any TextContent arm.
    _mock_backend_with_long_chunks(monkeypatch, n_hits=1, text_len=100)
    green = await _call(app, "dual_memory_search", {"query": "hi", "top_k": 1})
    for block in green.content:
        assert secret not in (block.text or ""), "secret leaked into a TextContent arm"
    _assert_stdout_pure(capsys)
