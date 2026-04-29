"""FastMCP server exposing ``dual_memory_search`` over stdio + Streamable HTTP.

Verified FastMCP API (mcp >= 1.13):
    FastMCP(name, *, host="127.0.0.1", port=8000, ...)
    FastMCP.run(transport: Literal["stdio","sse","streamable-http"]="stdio",
                mount_path: str | None = None) -> None

Stdout discipline: this module MUST NOT emit anything on stdout at import or
runtime — stdio is the MCP framing channel. All logs go to stderr (or to
``MCP_LOG_FILE`` if set).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Optional

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

from pydantic import BaseModel, Field  # noqa: E402

from supamem.config import ResolvedConfig  # noqa: E402
from supamem.retrieval.tuned_hybrid import TunedHybridBackend  # noqa: E402
from supamem.retrieval.types import RetrievedChunk  # noqa: E402

# ---- Constants ------------------------------------------------------------

MAX_QUERY_LEN = 4096
MAX_FILE_PATH_LEN = 1024
SECRET_ENV_VARS = ("QDRANT_URL", "QDRANT_API_KEY")


# ---- Pydantic schemas (auto-derived JSON Schema for the MCP tool) --------


class Chunk(BaseModel):
    score: float = Field(..., description="RRF-fused score from tuned_hybrid retrieval")
    text: str = Field(..., description="Document excerpt from the chunk")
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


def _build_summary_md(chunk_count: int, total_tokens: int, latency_ms: int) -> str:
    """One-line Markdown summary surfaced in the tool-call card."""
    if chunk_count == 0:
        return f"🧠 **supamem** · no matches · {latency_ms} ms"
    return (
        f"🧠 **supamem** · {chunk_count} chunks · {total_tokens} tokens · "
        f"{latency_ms} ms · _−78% vs naive RAG_"
    )


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

_BACKEND_CACHE: dict[int, TunedHybridBackend] = {}


def _get_backend(config: ResolvedConfig) -> TunedHybridBackend:
    key = id(config)
    if key not in _BACKEND_CACHE:
        _BACKEND_CACHE[key] = TunedHybridBackend(config=config)
    return _BACKEND_CACHE[key]


# ---- Tool implementation -------------------------------------------------


async def dual_memory_search(
    query: str = "",
    top_k: int = 5,
    *,
    config: ResolvedConfig | None = None,
) -> SearchResult:
    """Hybrid Qdrant retrieval over the configured collection (D-25 lock)."""
    cfg = config or ResolvedConfig()
    q = (query or "").strip()
    if len(q) > MAX_QUERY_LEN:
        raise ValueError(f"query too long (>{MAX_QUERY_LEN} chars)")
    if not q:
        raise ValueError("query required")

    backend = _get_backend(cfg)

    t0 = time.perf_counter()
    try:
        hits: list[RetrievedChunk] = await asyncio.to_thread(backend.query, q, top_k)
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.exception("retrieval failed: %s", exc)
        _bump(outcome="error", elapsed_ms=elapsed_ms)
        raise RuntimeError(_sanitize_error(exc)) from None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    chunks: list[Chunk] = []
    for h in hits or []:
        text = (h.text or "").strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                score=float(h.score or 0.0),
                text=text,
                source=h.source_path or h.file_path or "?",
                file_path=h.file_path,
            )
        )
    total_tokens = sum(max(1, len(c.text) // 4) for c in chunks)
    _bump(
        outcome="injected" if chunks else "no_match",
        injected_tokens=total_tokens,
        elapsed_ms=elapsed_ms,
    )
    return SearchResult(
        summary_md=_build_summary_md(len(chunks), total_tokens, int(elapsed_ms)),
        chunks=chunks,
        total_tokens=total_tokens,
        latency_ms=int(elapsed_ms),
    )


# ---- App builder + transports --------------------------------------------


_TOOL_TITLE = "🧠 supamem · Memory Search"
_TOOL_DESCRIPTION = (
    "Hybrid (BM25 + dense) Qdrant retrieval over the project's dual-memory corpus. "
    "Returns the top-k most relevant chunks of project notes, ADRs, decisions, insights, "
    "and rules — automatically curated to fit a small token budget while preserving recall."
)


def _register_dual_memory_tool(app: Any, config: ResolvedConfig) -> None:
    """Register dual_memory_search + dual_memory_write (and aliases) on a FastMCP app.

    - title (spec ≥ 2025-03-26) — Cursor / Claude.ai web render this.
    - description — concise; renders in tool-picker UIs.
    - Aliases ``qdrant_find`` / ``qdrant_store`` are registered by default for
      backward compat with the upstream ``mcp-server-qdrant`` tool names.
      Disable with ``SUPAMEM_QDRANT_ALIASES=0``.
    """
    from mcp.server.fastmcp.tools import Tool as _FastMCPTool  # noqa: F401  (typecheck only)

    aliases_enabled = os.environ.get("SUPAMEM_QDRANT_ALIASES", "1").strip() not in ("0", "false", "")

    # ── Read: dual_memory_search (canonical) ────────────────────────────────
    @app.tool(
        name="dual_memory_search",
        title=_TOOL_TITLE,
        description=_TOOL_DESCRIPTION,
    )
    async def dual_memory_search_tool(  # noqa: ARG001  (FastMCP wraps this)
        query: str = Field(
            "",
            description="Natural-language question. Hybrid (BM25 + dense) search over project memory.",
        ),
        top_k: int = Field(5, description="Max chunks to return (1-10 recommended)."),
    ) -> SearchResult:
        return await dual_memory_search(query=query, top_k=top_k, config=config)

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
    ) -> dict:
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
        return {
            "summary": res.summary,
            "path": res.path,
            "topic": res.topic,
            "slug": res.slug,
            "indexed": res.indexed,
            "points_added": res.points_added,
            "error": res.error,
        }

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
            query: str = Field("", description="Search query (alias of dual_memory_search)."),
            top_k: int = Field(5, description="Max chunks to return."),
        ) -> SearchResult:
            return await dual_memory_search(query=query, top_k=top_k, config=config)

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
        ) -> dict:
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
            return {
                "summary": res.summary,
                "path": res.path,
                "topic": res.topic,
                "slug": res.slug,
                "indexed": res.indexed,
                "points_added": res.points_added,
                "error": res.error,
            }


def build_app(config: ResolvedConfig) -> Any:
    """Construct a FastMCP app with the dual_memory_search tool registered."""
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("supamem", host="127.0.0.1", port=8765)
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
    from mcp.server.fastmcp import FastMCP

    log.info("starting supamem MCP server (streamable-http) on %s:%d", host, port)
    app = FastMCP("supamem", host=host, port=port)
    _register_dual_memory_tool(app, config)
    app.run(transport="streamable-http")


__all__ = [
    "Chunk",
    "SearchResult",
    "build_app",
    "dual_memory_search",
    "run_http",
    "run_stdio",
]
