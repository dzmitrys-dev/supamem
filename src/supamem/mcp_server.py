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
import gc
import logging
import os
import subprocess
import sys
import threading
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


def _bump(kind: str, tokens: int, latency_ms: float) -> None:
    """One audit row per MCP search, with the counter's real signature ``bump(kind, source, tokens, latency_ms)``.

    The previous ``_bump(outcome=..., injected_tokens=..., elapsed_ms=...)`` never matched that signature; the
    TypeError was swallowed at debug level, so no MCP search was ever logged (field report 2026-11-06: 1 796 audit
    rows, all ``rerank``, zero ``search``). ``kind`` is ``search`` (hits or no match) or ``search_error``."""
    try:
        from supamem.stats import counter
    except ImportError:
        return
    try:
        counter.bump(kind, "mcp", int(tokens), float(latency_ms))
    except Exception as exc:  # noqa: BLE001 — counter must never block the tool
        log.warning("counter bump failed: %s", exc)


# ---- Backend cache (one TunedHybridBackend per config) -------------------
# v0.4.0a5: entries are _CachedBackend records (last_use/busy) so the idle sweeper
# can evict a backend whose models have been pinned unused — a queried server held
# ~2.4 GB of reranker weights for its whole lifetime (issue #5, mitigation 1 of 2;
# the shared daemon is the follow-up release).

_BACKEND_CACHE: dict[tuple, "_CachedBackend"] = {}
_CACHE_LOCK = threading.RLock()


class _CachedBackend:
    """Cache record: the backend plus sweeper bookkeeping (monotonic clock)."""

    __slots__ = ("backend", "last_use", "busy")

    def __init__(self, backend: TunedHybridBackend) -> None:
        self.backend = backend
        self.last_use = time.monotonic()
        self.busy = 0


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
    """Create-or-fetch the backend for this config and mark it busy.

    Busy/last_use update atomically under ``_CACHE_LOCK`` BEFORE the caller's
    query starts, so the sweeper can never evict an entry between stamp and
    query. Paired with :func:`_release_backend` in a ``finally``.
    """
    key = _backend_key(config)
    with _CACHE_LOCK:
        rec = _BACKEND_CACHE.get(key)
        if rec is None:
            rec = _CachedBackend(TunedHybridBackend(config=config))
            _BACKEND_CACHE[key] = rec
        rec.busy += 1
        rec.last_use = time.monotonic()
        return rec.backend


def _release_backend(config: ResolvedConfig, backend: TunedHybridBackend) -> None:
    """Mark the backend idle again. Identity-checked: a monkeypatched/test
    ``_get_backend`` that never acquired must not decrement a real record."""
    key = _backend_key(config)
    with _CACHE_LOCK:
        rec = _BACKEND_CACHE.get(key)
        if rec is not None and rec.backend is backend and rec.busy > 0:
            rec.busy -= 1


def _idle_ttl_seconds(config: ResolvedConfig) -> float:
    """Idle TTL in seconds; ``SUPAMEM_MCP_IDLE_TTL_SECONDS`` overrides (tests)."""
    env = os.environ.get("SUPAMEM_MCP_IDLE_TTL_SECONDS")
    if env:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    minutes = getattr(config, "mcp_model_idle_minutes", 30)
    try:
        return max(0.0, float(minutes) * 60.0)
    except (TypeError, ValueError):
        return 0.0


def _rss_kb() -> Optional[int]:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return int(out.stdout.strip()) if out.stdout.strip() else None
    except Exception:  # noqa: BLE001 — telemetry only
        return None


# v0.4.0a6 — a warm daemon answers in seconds; the budget covers a cold spawn + model load.
_DAEMON_TIMEOUT_S = 120.0


def sweep_idle_backends(config: ResolvedConfig) -> int:
    """Evict cached backends idle >= TTL and not busy. Returns count evicted.

    Teardown is dropping the whole cache entry (NOT clearing model fields on a
    live backend): an in-flight query holds its model refs in locals, the
    orphaned backend finishes correctly, construction is attribute-only, and
    ``_ensure()`` rebuilds everything on the next query. ``gc.collect()`` runs
    twice — torch finalizers/ref-cycles commonly need a second pass. Weight
    tensors and mmap'd onnx/safetensors DO return to the OS; dylib text pages
    and heap fragmentation do not (claim only what the log measures).
    """
    ttl = _idle_ttl_seconds(config)
    if ttl <= 0:
        return 0
    now = time.monotonic()
    evicted: list[tuple] = []
    with _CACHE_LOCK:
        for key in list(_BACKEND_CACHE):
            rec = _BACKEND_CACHE[key]
            if rec.busy == 0 and now - rec.last_use >= ttl:
                evicted.append(key)
                del _BACKEND_CACHE[key]
        pool_cleared = 0
        if evicted and not _BACKEND_CACHE:
            # v0.4.0a6: models live in the process-wide pool, not on the backend — drop
            # them too once no backend is cached, or eviction would free nothing.
            from supamem.retrieval import model_pool  # noqa: PLC0415

            pool_cleared = model_pool.clear()
    if pool_cleared:
        log.info("supamem mcp: model pool cleared (%d models)", pool_cleared)
    for key in evicted:
        before = _rss_kb()
        gc.collect()
        gc.collect()
        after = _rss_kb()
        log.info(
            "supamem mcp: idle backend unloaded (collection=%s, ttl=%ss) rss %s->%s kB",
            key[2] if len(key) > 2 else "?",
            ttl,
            before,
            after,
        )
    return len(evicted)


def _start_idle_sweeper(config: ResolvedConfig) -> None:
    """Daemon thread sweeping the cache every <=60 s (skipped when disabled)."""
    ttl = _idle_ttl_seconds(config)
    if ttl <= 0:
        return
    interval = min(60.0, max(1.0, ttl / 2.0))

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                sweep_idle_backends(config)
            except Exception:  # noqa: BLE001 — the sweeper must never die
                log.debug("idle sweep failed", exc_info=True)

    t = threading.Thread(target=_loop, name="supamem-idle-sweeper", daemon=True)
    t.start()


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

    t0 = time.perf_counter()
    hits: list[RetrievedChunk] | None = None
    from supamem.daemon import daemon_enabled  # noqa: PLC0415

    if daemon_enabled(cfg):
        # v0.4.0a6 (ADR-0005): one shared daemon holds the models for every window; this
        # server stays ~130 MB. Only an UNREACHABLE daemon falls back to loading in-process.
        from supamem.daemon import DaemonUnavailable  # noqa: PLC0415
        from supamem.daemon import client as daemon_client  # noqa: PLC0415

        try:
            hits = await asyncio.to_thread(
                daemon_client.query, cfg, q, effective_top_k, where,
                timeout=_DAEMON_TIMEOUT_S,
            )
        except DaemonUnavailable as exc:
            log.warning("supamem daemon unavailable (%s) — in-process retrieval", exc)
        except Exception as exc:  # noqa: BLE001 — busy / query failure: surface, don't load
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("daemon retrieval failed: %s: %s", type(exc).__name__, exc)
            _bump("search_error", 0, elapsed_ms)
            raise RuntimeError(_sanitize_error(exc)) from None
    if hits is None:
        backend = _get_backend(cfg)
        try:
            hits = await asyncio.to_thread(backend.query, q, effective_top_k, where=where)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            log.exception("retrieval failed: %s", exc)
            _bump("search_error", 0, elapsed_ms)
            raise RuntimeError(_sanitize_error(exc)) from None
        finally:
            _release_backend(cfg, backend)

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
    _bump("search", total_tokens, elapsed_ms)
    # v0.4.0a5 — MCP channel journal row (kind="mcp"). No session_id exists in the
    # MCP tool-call context, so use-rate for this channel stays transcript-mined
    # (docs/usefulness-metrics.md); the row still powers volume / score / language
    # mix analytics that the audit log's one-line counters cannot answer.
    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        from supamem.stats.injections import append_row, chunk_lang  # noqa: PLC0415

        append_row(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "mcp",
                "session": "",
                "target": "",
                "query": q[:250],
                "chunks": [
                    {
                        "source": c.source or c.file_path or "?",
                        "score": round(float(c.score or 0.0), 4),
                        "lang": chunk_lang(c.text or ""),
                    }
                    for c in chunks
                ],
                "empty": not chunks,
                "deduped": 0,
                "dropped": {"stale": 0, "floor": 0, "foreign": 0},
                "ms": round(elapsed_ms, 1),
                "shadow": None,
            }
        )
    except Exception:  # noqa: BLE001 — journaling must never break the tool
        log.debug("mcp journal row failed", exc_info=True)
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


def _cache_hints_for(cfg: ResolvedConfig) -> dict[str, Any] | None:
    """SEP-2549 per-method cache-hint map for the server constructor (L2).

    Probed against installed mcp 2.x: cache hints are a PER-METHOD
    constructor map (``MCPServer(cache_hints={method: CacheHint(...)})``);
    ``CacheableMethod`` covers list/read/discovery methods only —
    ``tools/call`` is NOT cacheable, and the ``@tool()`` decorator has no
    per-tool hint parameter. supamem serves no prompts/resources, so
    ``tools/list`` is the one usable stampable surface: a TTL there lets
    2026-era clients cache the tool registry (deterministic registration
    order keeps that cache valid — d5). Write-tool exclusion is airtight by
    construction: no tools/call result (read or write) is ever stamped, so
    a cached result can never mask write-then-read visibility.

    Default ``mcp_cache_ttl_ms = 0`` → ``None`` (hints off). Scope stays
    ``private`` (the CacheHint default): cached listings must not leak
    across authorization contexts.
    """
    if cfg.mcp_cache_ttl_ms <= 0:
        return None
    from mcp.server.caching import CacheHint  # noqa: PLC0415 (import-weight convention)

    return {"tools/list": CacheHint(ttl_ms=cfg.mcp_cache_ttl_ms)}


def build_app(config: ResolvedConfig) -> Any:
    """Construct an MCPServer app with the dual_memory_search tool registered."""
    from mcp.server.mcpserver import MCPServer

    app = MCPServer("supamem", cache_hints=_cache_hints_for(config))
    _register_dual_memory_tool(app, config)
    return app


def run_stdio(config: ResolvedConfig) -> None:
    log.info("starting supamem MCP server (stdio)")
    _start_idle_sweeper(config)
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
    _start_idle_sweeper(config)
    app = MCPServer("supamem", cache_hints=_cache_hints_for(config))
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
