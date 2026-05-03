"""Phase 11 D-NOID-01 — No-identity-tier regression test.

Locks the FILT-02 anti-feature commitment: supamem MUST NOT register any
always-on identity / wake-up / prelude tier, and MUST NOT permit retrieval
without an explicit user-supplied query.

Three assertions (D-NOID-01.a..c):

1. Empty-query rejection — ``dual_memory_search(query="")`` raises ValueError.
2. No prelude tool names — registered tool names contain nothing matching
   the regex ``(?i)(wake[_-]?up|identity|prelude|inject)``.
3. No solicit-less retrieval — every retrieval tool's JSON Schema requires
   ``query`` (in ``required``) AND enforces ``minLength: 1``.

If a future change ships an auto-prelude tool, this file is the load-bearing
canary that fails CI before the change can land.

Doc commitment cross-ref: D-NOID-02 in
``.planning/phases/11-filtered-retrieval-backend/11-CONTEXT.md`` and the
README sentence "supamem does NOT auto-inject identity / wake-up / prelude
context into agent calls — retrieval is always solicited via an explicit
query."
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import pytest

from supamem.config import ResolvedConfig
from supamem.mcp_server import build_app, dual_memory_search

# ──────────────────────────────────────────────────────────────────────────
# Deterministic env (AGENTS.md "Test Discipline" — applies to subprocess
# CLI smokes; harmless in-process and documents intent for parity).
# ──────────────────────────────────────────────────────────────────────────
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")
os.environ.setdefault("COLUMNS", "200")
os.environ.pop("FORCE_COLOR", None)

# Regex from D-NOID-01.b — case-insensitive; covers wake-up, wake_up, wakeup,
# identity, prelude, inject (and any compound that contains these).
FORBIDDEN_NAME_RE = re.compile(r"(?i)(wake[_-]?up|identity|prelude|inject)")

# Set of MCP tool names that are RETRIEVAL tools (subject to assertion 3).
# Write-side tools (dual_memory_write / qdrant_store) are out of scope —
# their input schema legitimately requires non-empty topic/content but is
# not a retrieval surface.
RETRIEVAL_TOOL_NAMES = {"dual_memory_search", "qdrant_find"}


def _cfg() -> ResolvedConfig:
    """Minimal ResolvedConfig — same shape as tests/test_mcp_server.py:_cfg."""
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection="test_no_identity_tier",
    )


@pytest.fixture
def tool_registry() -> dict[str, Any]:
    """Live FastMCP tool registry — NOT a hardcoded list.

    Returns the actual ``app._tool_manager._tools`` dict from a freshly
    built app. The fixture rebuilds per-test so cap/alias env tweaks in
    one test never bleed into another.

    Convention precedent: tests/test_mcp_server.py:33,83 already crosses
    the ``_tool_manager._tools`` line — same access pattern reused here
    rather than introducing a new public helper.
    """
    app = build_app(_cfg())
    return app._tool_manager._tools  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────
# Assertion 1 (D-NOID-01.a) — empty-query rejection
# ──────────────────────────────────────────────────────────────────────────


def test_dual_memory_search_rejects_empty_query() -> None:
    """``dual_memory_search(query="")`` MUST raise — no auto-prelude path.

    Failure mode this catches: a future refactor that silently substitutes
    a default query, an injected identity prelude, or a retrieval call
    with no user-supplied query.
    """
    with pytest.raises(ValueError, match="query required"):
        asyncio.run(dual_memory_search(query="", config=_cfg()))


def test_dual_memory_search_rejects_whitespace_query() -> None:
    """Whitespace-only query is also empty — schema ``minLength=1`` does
    NOT catch this (`" "` has length 1), so the runtime ``.strip()`` check
    in mcp_server.py:191 is the load-bearing defense. Lock it explicitly.
    """
    with pytest.raises(ValueError, match="query required"):
        asyncio.run(dual_memory_search(query="   ", config=_cfg()))


# ──────────────────────────────────────────────────────────────────────────
# Assertion 2 (D-NOID-01.b) — no prelude / wake-up / identity / inject tools
# ──────────────────────────────────────────────────────────────────────────


def test_no_forbidden_tool_names(tool_registry: dict[str, Any]) -> None:
    """Tool registry MUST NOT contain any name matching the FILT-02 lock regex.

    Failure mode this catches: a future PR registers e.g.
    ``identity_prelude``, ``wake_up_context``, or ``inject_session_memory``
    — any of which would violate the no-identity-tier commitment.
    """
    offenders = [
        name for name in tool_registry.keys() if FORBIDDEN_NAME_RE.search(name)
    ]
    assert offenders == [], (
        "Phase 11 D-NOID-01 / FILT-02 anti-feature lock violated: registered "
        f"tool name(s) {offenders!r} match the forbidden-prelude regex "
        f"{FORBIDDEN_NAME_RE.pattern!r}. supamem does NOT ship always-on "
        "identity / wake-up / prelude tiers (see "
        ".planning/phases/11-filtered-retrieval-backend/11-CONTEXT.md "
        "D-NOID-01..03 and the README no-auto-inject commitment)."
    )


# ──────────────────────────────────────────────────────────────────────────
# Assertion 3 (D-NOID-01.c) — every retrieval tool requires non-empty query
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool_name", sorted(RETRIEVAL_TOOL_NAMES))
def test_retrieval_tool_schema_requires_nonempty_query(
    tool_name: str, tool_registry: dict[str, Any]
) -> None:
    """Every retrieval tool's JSON Schema MUST have ``query`` in ``required``
    AND enforce ``minLength >= 1``.

    Failure mode this catches: a default value (``Field("")``) sneaks back
    in, making the schema advertise ``query`` as optional to LLM hosts —
    which would let a host "explore" by calling the tool with no query and
    receive whatever the runtime fallback yields. The runtime ``ValueError``
    is defense-in-depth; the schema is the LLM-facing contract.
    """
    assert tool_name in tool_registry, (
        f"retrieval tool {tool_name!r} is not registered — RETRIEVAL_TOOL_NAMES "
        "in tests/test_no_identity_tier.py is out of sync with mcp_server.py"
    )
    schema = tool_registry[tool_name].parameters
    assert isinstance(schema, dict), (
        f"{tool_name}.parameters must be a dict (FastMCP Tool.parameters "
        f"is auto-derived from Pydantic), got {type(schema).__name__}"
    )

    # 3a) ``query`` is in the top-level ``required`` array.
    required = schema.get("required") or []
    assert "query" in required, (
        f"{tool_name} JSON Schema does not list 'query' as required "
        f"(required={required!r}). Phase 11 D-NOID-01.c locks every "
        "retrieval tool to an explicit user-supplied query — making it "
        "optional re-opens the auto-prelude attack surface."
    )

    # 3b) ``query`` property has ``minLength >= 1``.
    props = schema.get("properties") or {}
    query_schema = props.get("query") or {}
    min_length = query_schema.get("minLength")
    assert isinstance(min_length, int) and min_length >= 1, (
        f"{tool_name}.parameters.properties.query MUST set minLength>=1 "
        f"(got {min_length!r}). Without it, hosts can pass query='' which "
        "today only the runtime ``.strip()`` check rejects — the JSON "
        "Schema layer (the LLM-facing contract) MUST advertise the "
        "constraint. See D-NOID-01.c."
    )
