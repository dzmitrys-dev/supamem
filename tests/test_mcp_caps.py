"""Boundary tests for CAPS-01..03 (Phase 05 — MCP response caps).

Wave 0 (RED phase): all 10 tests are written BEFORE implementation lands.
They MUST fail with assertion / AttributeError messages, never ImportError.

Cross-reference: 05-VALIDATION.md "Per-Task Verification Map" rows for
``tests/test_mcp_caps.py`` map 1:1 onto the test functions below.

JSON-RPC purity: no bare ``print()``; tests that check stdout discipline
use ``capsys``.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

# Module-level helpers (NOT fixtures — they take args).
from tests.conftest import _cfg_with_caps, _mock_backend_with_long_chunks

# These imports MUST succeed at collection time. ``MAX_QUERY_LEN`` still
# exists in red phase (Wave 2 deletes it); test 4 uses ``hasattr`` so the
# import line itself never raises.
from supamem.mcp_server import (
    Chunk,  # noqa: F401  — re-exported for downstream test parity
    SearchResult,  # noqa: F401
    _build_summary_md,
    build_app,
    dual_memory_search,
)


# ── 1. CAPS-01 — oversized query rejected via Pydantic validation ──────────


@pytest.mark.asyncio
async def test_query_over_max_chars_rejects_via_validation_error() -> None:
    cfg = _cfg_with_caps(max_query_chars=10)
    app = build_app(cfg)
    with pytest.raises(ToolError) as exc_info:
        await app._tool_manager.call_tool(
            "dual_memory_search", {"query": "x" * 100, "top_k": 5}
        )
    msg = str(exc_info.value).lower()
    assert "string_too_long" in msg or "at most 10" in msg, (
        f"Expected Pydantic max_length validation error, got: {msg!r}"
    )


# ── 2. CAPS-01 — schema discoverability ────────────────────────────────────


def test_query_max_length_appears_in_tool_schema() -> None:
    cfg = _cfg_with_caps(max_query_chars=77)
    app = build_app(cfg)
    tool = app._tool_manager._tools["dual_memory_search"]  # type: ignore[attr-defined]
    schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None)
    assert schema is not None, "tool schema not exposed via .parameters or .inputSchema"
    query_props = schema.get("properties", {}).get("query", {})
    assert query_props.get("maxLength") == cfg.mcp_caps_max_query_chars, (
        f"expected maxLength={cfg.mcp_caps_max_query_chars} in JSON schema, "
        f"got: {query_props}"
    )


# ── 3. CAPS-01 + D-17 — alias schema + response-shape parity ───────────────


def test_alias_schema_matches_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPAMEM_QDRANT_ALIASES", "1")
    cfg = _cfg_with_caps(max_query_chars=42)
    app = build_app(cfg)
    tools = app._tool_manager._tools  # type: ignore[attr-defined]
    canonical = tools["dual_memory_search"]
    alias = tools["qdrant_find"]

    canon_schema = (
        getattr(canonical, "parameters", None)
        or getattr(canonical, "inputSchema", None)
    )
    alias_schema = (
        getattr(alias, "parameters", None)
        or getattr(alias, "inputSchema", None)
    )
    assert canon_schema is not None and alias_schema is not None

    canon_q = canon_schema.get("properties", {}).get("query", {})
    alias_q = alias_schema.get("properties", {}).get("query", {})

    # Input parity: BOTH must publish a numeric maxLength (not None) AND match.
    # Asserting non-None catches red phase where neither tool plumbs the cap yet.
    assert canon_q.get("maxLength") == cfg.mcp_caps_max_query_chars, (
        f"canonical query.maxLength must equal cfg.mcp_caps_max_query_chars="
        f"{cfg.mcp_caps_max_query_chars}; got {canon_q.get('maxLength')!r}"
    )
    assert alias_q.get("maxLength") == cfg.mcp_caps_max_query_chars, (
        f"alias query.maxLength must equal cfg.mcp_caps_max_query_chars="
        f"{cfg.mcp_caps_max_query_chars}; got {alias_q.get('maxLength')!r} "
        f"(D-17 alias parity)"
    )
    # No extra/missing keys on the query field across the two tools.
    assert set(canon_q.keys()) == set(alias_q.keys()), (
        f"alias query field keys diverged: canonical={set(canon_q.keys())}, "
        f"alias={set(alias_q.keys())}"
    )

    # Response-shape parity: both tools return SearchResult.
    canon_fn = getattr(canonical, "fn", None) or getattr(canonical, "func", None)
    alias_fn = getattr(alias, "fn", None) or getattr(alias, "func", None)
    assert canon_fn is not None and alias_fn is not None
    canon_ret = canon_fn.__annotations__.get("return")
    alias_ret = alias_fn.__annotations__.get("return")
    assert canon_ret is alias_ret, (
        f"alias response-shape drift: canonical returns {canon_ret}, "
        f"alias returns {alias_ret} (D-17 / T-05-02)"
    )


# ── 4. CAPS-01 — MAX_QUERY_LEN constant removed (Wave 2) ───────────────────


def test_max_query_len_constant_removed() -> None:
    from supamem import mcp_server as mod

    assert not hasattr(mod, "MAX_QUERY_LEN"), (
        "MAX_QUERY_LEN must be deleted in Wave 2 — query length is now "
        "config-driven via cfg.mcp_caps_max_query_chars (D-07)."
    )


# ── 5. CAPS-02 — preview capped, full text intact ──────────────────────────


@pytest.mark.asyncio
async def test_preview_capped_text_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    import supamem.mcp_server as mod
    from supamem.retrieval.types import RetrievedChunk
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.query.return_value = [
        RetrievedChunk(id="1", text="abc" * 100, score=0.9, source_path="s.md"),
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake)

    cfg = _cfg_with_caps(max_preview_chars=50)
    result = await dual_memory_search(query="hi", top_k=1, config=cfg)
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert len(chunk.preview) <= 50, (
        f"preview length {len(chunk.preview)} exceeds cap 50"
    )
    assert chunk.text == "abc" * 100, "full text must remain intact (D-01/D-02)"


# ── 6. CAPS-02 — Unicode codepoint counting (Pitfall 2) ────────────────────


@pytest.mark.asyncio
async def test_preview_unicode_codepoint_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import supamem.mcp_server as mod
    from supamem.retrieval.types import RetrievedChunk
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.query.return_value = [
        RetrievedChunk(id="1", text="漢" * 100, score=0.9, source_path="s.md"),
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake)

    cfg = _cfg_with_caps(max_preview_chars=20)
    result = await dual_memory_search(query="hi", top_k=1, config=cfg)
    preview = result.chunks[0].preview
    # Codepoint count, not byte count: len() on a str returns codepoints.
    assert len(preview) <= 20, (
        f"preview codepoint count {len(preview)} exceeds cap 20 "
        f"(possible byte-vs-codepoint slicing bug, Pitfall 2)"
    )


# ── 7. CAPS-03 — top_k silent clamp signals via clamped_to ─────────────────


@pytest.mark.asyncio
async def test_top_k_silent_clamp_signals_clamped_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_backend_with_long_chunks(monkeypatch, n_hits=10, text_len=20)
    cfg = _cfg_with_caps(max_top_k=5)
    result = await dual_memory_search(query="hi", top_k=50, config=cfg)
    assert len(result.chunks) == 5, (
        f"expected exactly 5 chunks after clamp, got {len(result.chunks)}"
    )
    assert result.clamped_to == 5, (
        f"expected clamped_to=5 signal, got {result.clamped_to!r} (CAPS-03)"
    )


# ── 8. CAPS-03 — under-cap path leaves clamped_to unset ────────────────────


@pytest.mark.asyncio
async def test_top_k_under_cap_no_clamp_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_backend_with_long_chunks(monkeypatch, n_hits=10, text_len=20)
    cfg = _cfg_with_caps(max_top_k=5)
    result = await dual_memory_search(query="hi", top_k=3, config=cfg)
    assert result.clamped_to is None, (
        f"expected clamped_to=None when top_k<=cap, got {result.clamped_to!r} (D-04)"
    )


# ── 9. CAPS-03 — summary_md renders ⚠️ when clamped, not otherwise ──────────


def test_summary_md_renders_clamp_warning() -> None:
    # With clamping → warning line present.
    rendered = _build_summary_md(
        chunk_count=5,
        total_tokens=100,
        latency_ms=12,
        requested_top_k=50,
        clamped_to=5,
    )
    assert "⚠️" in rendered, "expected ⚠️ glyph on clamp event (D-14)"
    assert "50" in rendered, "expected requested top_k=50 to be surfaced"
    assert "5" in rendered, "expected cap value 5 to be surfaced"
    assert "max_top_k" in rendered, (
        "expected hint mentioning 'max_top_k' so users know which knob to raise"
    )

    # Counter-case: no clamp → no warning glyph (D-16).
    clean = _build_summary_md(
        chunk_count=5,
        total_tokens=100,
        latency_ms=12,
        requested_top_k=5,
        clamped_to=None,
    )
    assert "⚠️" not in clean, (
        "summary_md must NOT show clamp warning when clamped_to is None"
    )


# ── 10. CAPS-01 — stdout discipline on cap-rejection path ──────────────────


def test_cap_rejection_no_stdout_pollution(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _cfg_with_caps(max_query_chars=10)
    app = build_app(cfg)
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            app._tool_manager.call_tool(
                "dual_memory_search", {"query": "x" * 100, "top_k": 5}
            )
        )
    # The rejection MUST come from the Pydantic max_length validator, not
    # from a downstream backend crash — otherwise this test passes for the
    # wrong reason in red phase.
    msg = str(exc_info.value).lower()
    assert "string_too_long" in msg or "at most 10" in msg, (
        f"expected Pydantic cap-rejection error, got: {msg!r}"
    )
    captured = capsys.readouterr()
    assert captured.out == "", (
        f"stdout pollution on cap-rejection path violates JSON-RPC purity: "
        f"{captured.out!r}"
    )
