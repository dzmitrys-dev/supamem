"""MCPServer exposing ``dual_memory_search`` over stdio + Streamable HTTP.

Verified MCP SDK v2 API (mcp >= 2, < 3):
    MCPServer(name, ...)                     — identity only; no transport kwargs
    MCPServer.run(transport: Literal["stdio","sse","streamable-http"]="stdio",
                  **kwargs) -> None          — host/port ride here for HTTP transports
                                             (e.g. run(transport="streamable-http",
                                             host=..., port=...))

Stdout discipline: this module MUST NOT emit anything on stdout at import or
runtime — stdio is the MCP framing channel. All logs go to stderr (or to
``MCP_LOG_FILE`` if set). Defense in depth: SDK v2 additionally diverts
fd 1 to stderr while serving.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Annotated, Any, Optional, Union

# ---- Logging routed to stderr BEFORE any heavy import ---------------------

_log_handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stderr)]
_log_file = os.environ.get("MCP_LOG_FILE")
if _log_file:
    try:
        _log_handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))
    except OSError:
        pass

logging.basicConfig(
    level=os.environ.get("MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
    force=True,
)
log = logging.getLogger("supamem.mcp_server")

from mcp.types import CallToolResult, TextContent  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from supamem.config import ResolvedConfig  # noqa: E402
from supamem.retrieval.tuned_hybrid import TunedHybridBackend  # noqa: E402
from supamem.retrieval.types import RetrievedChunk  # noqa: E402

# ---- Constants ------------------------------------------------------------

MAX_FILE_PATH_LEN = 1024
SECRET_ENV_VARS = ("QDRANT_URL", "QDRANT_API_KEY")


# ---- Pydantic schemas (auto-derived JSON Schema for the MCP tool) --------


class Chunk(BaseModel):
    score: float = Field(..., description="RRF-fused score from tuned_hybrid retrieval")
    text: str = Field(
        ...,
        description="Full canonical chunk payload (intact, never truncated)",
    )
    preview: str = Field(
        "",
        description="Display-only excerpt, capped at mcp.caps.max_preview_chars",
    )
    source: str = Field(..., description="Source file path or document identifier")
    file_path: Optional[str] = Field(None, description="Original file path if available")


class SearchResult(BaseModel):
    summary_md: str = Field(
        "",
        description=(
            "User-visible Markdown summary rendered by hosts that show the first "
            "TextContent block (Claude Code, Claude Desktop). Hosts that read "
            "structuredContent only (Cursor) skip this — the chunks payload is canonical."
        ),
    )
    chunks: list[Chunk] = Field(..., description="Retrieved chunks ranked by score")
    total_tokens: int = Field(..., description="Approx token count across all chunk text")
    latency_ms: int = Field(..., description="Wall-clock retrieval latency")
    clamped_to: Optional[int] = Field(
        None,
        description="Set when server clamped requested top_k (CAPS-03)",
    )


def _build_summary_md(
    chunk_count: int,
    total_tokens: int,
    latency_ms: int,
    *,
    requested_top_k: Optional[int] = None,
    clamped_to: Optional[int] = None,
) -> str:
    """Multi-line Markdown summary card for the tool-call render (D-14).

    Zero-match render is intentionally unchanged (D-16). When ``clamped_to`` is
    set alongside ``requested_top_k``, an extra ``⚠️`` line surfaces the clamp
    so users can raise ``mcp.caps.max_top_k`` if they need more headroom.
    """
    if chunk_count == 0:
        return f"🧠 **supamem** · no matches · {latency_ms} ms"
    lines = [
        "🧠 **supamem** · _Memory Search_",
        "",
        f"• **{chunk_count} chunks** · {total_tokens} tokens · {latency_ms} ms",
        "• _−78% vs naive RAG_",
    ]
    if clamped_to is not None and requested_top_k is not None:
        lines.append(
            f"⚠️ Clamped `top_k`: {requested_top_k} → {clamped_to} "
            f"(raise `mcp.caps.max_top_k`)"
        )
    return "\n".join(lines)


# ---- Error sanitization (T-80.6-05-01) -----------------------------------


def _sanitize_error(exc: BaseException, env_vars: tuple[str, ...] = SECRET_ENV_VARS) -> str:
    """Replace literal env-var values in the message with redacted placeholders.

    Catches the most common leak (Qdrant URL embedded in a connection error)
    without depending on exception class names.
    """
    msg = str(exc)
    for name in env_vars:
        val = os.environ.get(name, "").strip()
        if val and val in msg:
            msg = msg.replace(val, f"<{name}_REDACTED>")
    if msg == str(exc) and not msg:
        return f"dual_memory_search failed ({type(exc).__name__})"
    return msg


# ---- Counter integration (no-op until plan 80.6-06 lands) ----------------


def _bump(*_args: Any, **_kwargs: Any) -> None:
    try:
        from supamem.stats.counter import bump as real_bump  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        real_bump(*_args, **_kwargs)
    except Exception as exc:  # noqa: BLE001 — counter must never block the tool
        log.debug("counter bump failed: %s", exc)


# ---- Backend cache (one TunedHybridBackend per config) -------------------

_BACKEND_CACHE: dict[tuple, TunedHybridBackend] = {}


def _backend_key(cfg: ResolvedConfig) -> tuple:
    """Structural cache key — id(config) is unsafe across GC.

    A freed ResolvedConfig's address can be reused by an allocator-fresh
    config with different qdrant_url / api_key / collection, silently
    handing the new caller the prior backend (wrong collection, wrong
    forbidden-collection guard). Key on the fields that actually shape
    backend identity.
    """
    return (
        cfg.qdrant_url,
        cfg.qdrant_api_key,
        cfg.collection,
        cfg.allow_legacy_collection,
    )


def _get_backend(config: ResolvedConfig) -> TunedHybridBackend:
    key = _backend_key(config)
    if key not in _BACKEND_CACHE:
        _BACKEND_CACHE[key] = TunedHybridBackend(config=config)
    return _BACKEND_CACHE[key]


# ---- Tool implementation -------------------------------------------------


async def dual_memory_search(
    query: str = "",
    top_k: int = 5,
    *,
    where: Optional[dict[str, Union[str, list[str]]]] = None,
    config: ResolvedConfig | None = None,
) -> SearchResult:
    """Hybrid Qdrant retrieval over the configured collection (D-25 lock)."""
    cfg = config or ResolvedConfig()
    q = (query or "").strip()
    if not q:
        raise ValueError("query required")
    # Query length is enforced at the Pydantic schema boundary
    # (Field(max_length=cfg.mcp_caps_max_query_chars)) — see
    # _register_dual_memory_tool. No len() check here (D-07).

    cap = cfg.mcp_caps_max_top_k
    effective_top_k = min(max(1, top_k), cap)
    clamped_to: Optional[int] = effective_top_k if top_k > cap else None

    backend = _get_backend(cfg)

    t0 = time.perf_counter()
    try:
        hits: list[RetrievedChunk] = await asyncio.to_thread(
            backend.query, q, effective_top_k, where=where
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.exception("retrieval failed: %s", exc)
        _bump(outcome="error", elapsed_ms=elapsed_ms)
        raise RuntimeError(_sanitize_error(exc)) from None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Defensive: backend may return more than effective_top_k (e.g. mock
    # backends or future Qdrant versions). Enforce exact count post-call.
    hits = (hits or [])[:effective_top_k]
    pcap = cfg.mcp_caps_max_preview_chars
    concise = cfg.mcp_response_format == "concise"
    chunks: list[Chunk] = []
    for h in hits:
        text = (h.text or "").strip()
        if not text:
            continue
        if concise:
            # L3 (Phase 19): opt-in concise mode — empty the display preview;
            # text is untouched (v0.2.0 scope lock: payloads stay intact).
            preview = ""
        elif len(text) > pcap:
            # Reserve one codepoint of budget for the ellipsis so the total
            # preview length remains ≤ pcap (test asserts <=).
            preview = text[: max(0, pcap - 1)] + "…" if pcap > 0 else ""
        else:
            preview = text[:pcap]
        # L4 (Phase 19): drop file_path when it merely duplicates source
        # (~2.6% wire dedup, both modes — the path survives in `source`).
        src = h.source_path or h.file_path or "?"
        file_path = (
            None if (h.file_path is not None and h.file_path == src) else h.file_path
        )
        chunks.append(
            Chunk(
                score=float(h.score or 0.0),
                text=text,
                preview=preview,
                source=src,
                file_path=file_path,
            )
        )
    total_tokens = sum(max(1, len(c.text) // 4) for c in chunks)
    _bump(
        outcome="injected" if chunks else "no_match",
        injected_tokens=total_tokens,
        elapsed_ms=elapsed_ms,
    )
    return SearchResult(
        summary_md=_build_summary_md(
            len(chunks),
            total_tokens,
            int(elapsed_ms),
            requested_top_k=top_k,
            clamped_to=clamped_to,
        ),
        chunks=chunks,
        total_tokens=total_tokens,
        latency_ms=int(elapsed_ms),
        clamped_to=clamped_to,
    )


# ---- App builder + transports --------------------------------------------


_TOOL_TITLE = "🧠 supamem · Memory Search"
_TOOL_DESCRIPTION = (
    "Hybrid (BM25 + dense) Qdrant retrieval over the project's dual-memory corpus. "
    "Returns the top-k most relevant chunks of project notes, ADRs, decisions, insights, "
    "and rules — automatically curated to fit a small token budget while preserving recall."
)


def _register_dual_memory_tool(app: Any, config: ResolvedConfig) -> None:
    """Register dual_memory_search + dual_memory_write (and aliases) on an MCPServer app.

    - title (spec ≥ 2025-03-26) — Cursor / Claude.ai web render this.
    - description — concise; renders in tool-picker UIs.
    - Aliases ``qdrant_find`` / ``qdrant_store`` are registered by default for
      backward compat with the upstream ``mcp-server-qdrant`` tool names.
      Disable with ``SUPAMEM_QDRANT_ALIASES=0``.
    """
    from mcp.server.mcpserver.tools import Tool as _FastMCPTool  # noqa: F401  (typecheck only)

    aliases_enabled = os.environ.get("SUPAMEM_QDRANT_ALIASES", "1").strip() not in ("0", "false", "")

    # Capture cap values ONCE so canonical + alias schemas cannot drift
    # (D-17 anti-drift; Pitfall 4).
    max_q = config.mcp_caps_max_query_chars
    max_k = config.mcp_caps_max_top_k

    # D-17 anti-drift — the where Field description is defined ONCE here and
    # referenced by BOTH dual_memory_search_tool AND qdrant_find_alias. Inlining
    # the description string into either handler is the exact bug D-17 prevents.
    where_desc = (
        "Optional payload filter. AND across keys, OR within list values. "
        "v1 documents 'room' as a key (one of: backend, frontend, tests, "
        "docs, scripts, config, migrations, types). Example: "
        '{"room": "backend"} or {"room": ["backend", "tests"]}. '
        "Unknown keys are passed through to Qdrant."
    )

    # ── Read: dual_memory_search (canonical) ────────────────────────────────
    @app.tool(
        name="dual_memory_search",
        title=_TOOL_TITLE,
        description=_TOOL_DESCRIPTION,
    )
    async def dual_memory_search_tool(  # noqa: ARG001  (FastMCP wraps this)
        query: str = Field(
            ...,
            description=(
                f"Natural-language question. Hybrid (BM25 + dense) search over "
                f"project memory. Max {max_q} chars (server enforced)."
            ),
            min_length=1,
            max_length=max_q,
        ),
        top_k: int = Field(
            5,
            description=(
                f"Max chunks to return. Server clamps to {max_k}; "
                f"clamped_to is set in response when this fires."
            ),
            ge=1,
        ),
        where: Optional[dict[str, Union[str, list[str]]]] = Field(
            None, description=where_desc
        ),
    ) -> Annotated[CallToolResult, SearchResult]:
        # L1 (Phase 19): pre-built CallToolResult — the SDK validates it against
        # the SearchResult-derived output schema (the Annotated form keeps schema
        # discovery for Cursor-shaped consumers) and passes it through, killing
        # the full-JSON double-wrap of the TextContent arm.
        result = await dual_memory_search(
            query=query, top_k=top_k, where=where, config=config
        )
        return CallToolResult(
            content=[TextContent(type="text", text=result.summary_md)],
            structured_content=result.model_dump(mode="json", by_alias=True),
        )

    # ── Write: dual_memory_write (canonical) ────────────────────────────────
    @app.tool(
        name="dual_memory_write",
        title="🧠 supamem · Memory Save",
        description=(
            "Persist an insight, research finding, or note into the project's "
            "dual-memory corpus. Writes a Markdown file under "
            "<project>/.claude/insights/_agent/<slug>.md AND immediately indexes "
            "it into Qdrant so the very next dual_memory_search sees it. "
            "Idempotent on topic — re-saving the same topic overwrites in place."
        ),
    )
    async def dual_memory_write_tool(  # noqa: ARG001
        topic: str = Field(
            ...,
            description="Short topic (used as deterministic slug; max 120 chars).",
        ),
        content: str = Field(
            ...,
            description="Markdown body of the memory (max 64K chars).",
        ),
        description: Optional[str] = Field(
            None,
            description="Optional one-line description for the YAML frontmatter (max 300 chars).",
        ),
        tags: Optional[list[str]] = Field(
            None,
            description="Optional tags (max 10, each max 32 chars).",
        ),
    ) -> CallToolResult:
        from supamem.memory_writer import write_memory

        try:
            res = await asyncio.to_thread(
                write_memory,
                topic=topic,
                content=content,
                description=description,
                tags=tags,
                config=config,
            )
        except ValueError as exc:
            raise RuntimeError(f"dual_memory_write: {exc}") from None
        # L1 (Phase 19): single-arm return — write summary as the only
        # TextContent, the result dict as the canonical structured arm.
        return CallToolResult(
            content=[TextContent(type="text", text=res.summary)],
            structured_content={
                "summary": res.summary,
                "path": res.path,
                "topic": res.topic,
                "slug": res.slug,
                "indexed": res.indexed,
                "points_added": res.points_added,
                "error": res.error,
            },
        )

    # ── Backward-compat aliases for upstream `mcp-server-qdrant` users ──────
    if aliases_enabled:

        @app.tool(
            name="qdrant_find",
            title="🧠 supamem · qdrant-find (alias)",
            description=(
                "Backward-compat alias for dual_memory_search. Identical behavior; "
                "kept so prose referencing the upstream `qdrant-find` tool name "
                "still routes correctly. Disable with SUPAMEM_QDRANT_ALIASES=0."
            ),
        )
        async def qdrant_find_alias(  # noqa: ARG001
            query: str = Field(
                ...,
                description=(
                    f"Search query (alias of dual_memory_search). "
                    f"Max {max_q} chars (server enforced)."
                ),
                min_length=1,
                max_length=max_q,
            ),
            top_k: int = Field(
                5,
                description=(
                    f"Max chunks to return (alias of dual_memory_search). "
                    f"Server clamps to {max_k}."
                ),
                ge=1,
            ),
            where: Optional[dict[str, Union[str, list[str]]]] = Field(
                None, description=where_desc
            ),
        ) -> Annotated[CallToolResult, SearchResult]:
            # L1 (Phase 19): same single-arm shape as the canonical read tool —
            # the Annotated form preserves the SearchResult output schema.
            result = await dual_memory_search(
                query=query, top_k=top_k, where=where, config=config
            )
            return CallToolResult(
                content=[TextContent(type="text", text=result.summary_md)],
                structured_content=result.model_dump(mode="json", by_alias=True),
            )

        @app.tool(
            name="qdrant_store",
            title="🧠 supamem · qdrant-store (alias)",
            description=(
                "Backward-compat alias for dual_memory_write. Identical behavior; "
                "kept so prose referencing the upstream `qdrant-store` tool name "
                "still routes correctly. Disable with SUPAMEM_QDRANT_ALIASES=0."
            ),
        )
        async def qdrant_store_alias(  # noqa: ARG001
            topic: str = Field(..., description="Topic (alias of dual_memory_write)."),
            content: str = Field(..., description="Markdown body."),
            description: Optional[str] = Field(None, description="Optional description."),
            tags: Optional[list[str]] = Field(None, description="Optional tags."),
        ) -> CallToolResult:
            from supamem.memory_writer import write_memory

            try:
                res = await asyncio.to_thread(
                    write_memory,
                    topic=topic,
                    content=content,
                    description=description,
                    tags=tags,
                    config=config,
                )
            except ValueError as exc:
                raise RuntimeError(f"qdrant_store: {exc}") from None
            # L1 (Phase 19): single-arm return, matching the canonical write tool.
            return CallToolResult(
                content=[TextContent(type="text", text=res.summary)],
                structured_content={
                    "summary": res.summary,
                    "path": res.path,
                    "topic": res.topic,
                    "slug": res.slug,
                    "indexed": res.indexed,
                    "points_added": res.points_added,
                    "error": res.error,
                },
            )


def build_app(config: ResolvedConfig) -> Any:
    """Construct an MCPServer app with the dual_memory_search tool registered."""
    from mcp.server.mcpserver import MCPServer

    app = MCPServer("supamem")
    _register_dual_memory_tool(app, config)
    return app


def run_stdio(config: ResolvedConfig) -> None:
    log.info("starting supamem MCP server (stdio)")
    app = build_app(config)
    app.run(transport="stdio")


def run_http(
    config: ResolvedConfig,
    *,
    port: int = 8765,
    host: str = "127.0.0.1",
) -> None:
    """Streamable HTTP per Nov 2025 MCP spec (D-45)."""
    from mcp.server.mcpserver import MCPServer

    log.info("starting supamem MCP server (streamable-http) on %s:%d", host, port)
    app = MCPServer("supamem")
    _register_dual_memory_tool(app, config)
    app.run(transport="streamable-http", host=host, port=port)


__all__ = [
    "Chunk",
    "SearchResult",
    "build_app",
    "dual_memory_search",
    "run_http",
    "run_stdio",
]
