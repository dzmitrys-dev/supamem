"""MCP-response token instrument (Phase 19 MEASURE, Plan 19-02).

Measures the serialized wire shape of MCP tool results — the TextContent arm
(pretty-printed JSON the SDK derives from the returned model) plus the
structuredContent arm (``model_dump(mode="json", by_alias=True)``) — for a
fixed deterministic query set, called through the same SDK tool-manager layer
hosts use (``mcp_server.build_app`` + ``app._tool_manager.call_tool``).

Why this exists (RESEARCH §2.3): the existing tpca metric estimates tokens
over backend chunk texts only; the serialized tool result (schema fields,
previews, SDK double-wrap) is invisible to it. Every response-shape lever in
plan 19-03 would show zero effect without this instrument. Baseline BEFORE
levers is the project's measure-first discipline (PUB-05, Pitfall 5).

Estimator: the SAME 4-chars-per-token estimator as the eval line, imported
from ``supamem.eval.runner`` (single source of truth — never re-implemented
here; the locked ``_run_goldens_legacy`` region and ``retrieval/filters.py``
are never edited, import-only per the phase prohibitions). The runner import
is function-local so this module's own import surface stays stdlib +
supamem.console — importing the instrument never constructs backends or
clients.

Offline by construction: the default backend factory returns a deterministic
fake (``n_hits`` RetrievedChunk objects of ``chunk_chars``-char texts with
26-char source paths — mirrors the RESEARCH §2.2 measurement corpus). No
Qdrant connection, no network, no tiktoken.

Stdio purity: library code paths write nothing to stdout — measurement
results travel via return values. The ``__main__`` CLI prints human summaries
through :mod:`supamem.console` exports only (no bare ``print``). This module
is eval-side and is never imported by ``mcp_server.py`` (threat T-19-01).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from supamem.console import console

SCHEMA_VERSION = 1

READ_TOOLS: tuple[str, ...] = ("dual_memory_search", "qdrant_find")

# Fixed workload: ten deterministic queries spanning 20-250 chars (the caps
# max_query_chars default). Post-lever re-runs (plan 19-03) MUST reuse this
# identical set so before/after deltas compare the same queries.
FIXED_QUERY_SET: tuple[str, ...] = (
    "auth token refresh flow",
    "where is the reranker prefetch configured and how is it tuned",
    "how do the byte-identical carry locks guard the legacy goldens runner",
    "what config keys control the mcp response caps and where are they clamped",
    "summarize the decision trail behind switching the eval floors to a three-run live baseline",
    "which retrieval backends are registered through the plugin entry points and how does the resolver pick one",
    "describe every guard that keeps the stdio json-rpc contract pure in the mcp server",
    "explain how the adaptive depth heuristic estimates query complexity before flipping the default on",
    "trace the full path a dual_memory_search tool call takes from the mcp schema boundary to the payload",
    "how does the autotune closed loop observe bench metrics diagnose regressions against the coderag "
    "floors propose config deltas and apply them only when the no-regression gate passes on every axis "
    "column cell before writing config back",
)

# Metrics carried per run aggregate (and sanity-banded in plan 19-02 Task 2).
_REPORT_METRICS: tuple[str, ...] = (
    "mcp_response_tokens_p50",
    "mcp_response_tokens_p95",
    "text_arm_tokens_p50",
    "structured_arm_tokens_p50",
)


def measure_result(result: Any) -> dict[str, Any]:
    """Measure both arms of one SDK tool-manager call result.

    Duck-typed over the installed SDK's ``call_tool`` return so the instrument
    survives SDK shape changes: a result exposing ``.content`` plus
    ``.structured_content``/``.structuredContent`` (SDK v2 ``CallToolResult``,
    obtained via ``convert_result=True``) is read directly; a 2-tuple
    ``(unstructured blocks, structured dict)`` (SDK v1 converted return) uses
    the elements; anything else is treated as a bare unstructured payload.

    Token estimates route through the shared eval estimator
    (``supamem.eval.runner._estimate_tokens``) applied to each arm's serialized
    text and summed, so the numbers are directly comparable with the tpca
    line. ``structured_chars`` is measured over ``json.dumps(structured,
    default=str, sort_keys=False)`` — matching the wire shape the SDK's
    ``model_dump(by_alias=True)`` produces.
    """
    from supamem.eval.runner import _estimate_tokens

    if hasattr(result, "content"):
        blocks = getattr(result, "content", None) or []
        structured = getattr(result, "structured_content", None)
        if structured is None:
            structured = getattr(result, "structuredContent", None)
    elif isinstance(result, tuple) and len(result) == 2:
        blocks, structured = result
    else:
        blocks, structured = [result], None

    joined = "".join(getattr(b, "text", "") or "" for b in blocks)
    serialized = (
        json.dumps(structured, default=str, sort_keys=False) if structured is not None else ""
    )
    text_tokens = _estimate_tokens(joined)
    structured_tokens = _estimate_tokens(serialized)
    return {
        "text_chars": len(joined),
        "structured_chars": len(serialized),
        "text_tokens": text_tokens,
        "structured_tokens": structured_tokens,
        "est_tokens": text_tokens + structured_tokens,
    }


class _DeterministicFakeBackend:
    """Offline stand-in matching the RESEARCH §2.2 measurement corpus.

    Yields ``n_hits`` RetrievedChunk objects with ``chunk_chars``-char texts,
    monotonically decreasing scores, and 26-char source paths so baseline
    numbers reproduce the researched response anatomy (top_k=5 with
    1000-char chunks lands near 1.7k structured-arm est. tokens).
    """

    def __init__(self, n_hits: int = 5, chunk_chars: int = 1000) -> None:
        from supamem.retrieval.types import RetrievedChunk

        self._hits = [
            RetrievedChunk(
                id=str(i),
                text="x" * chunk_chars,
                score=0.9 - i * 0.01,
                source_path=f"a/b/c/d/chunk_{i:09d}.md",
            )
            for i in range(n_hits)
        ]

    def query(
        self, q: str, top_k: int, where: dict[str, Any] | None = None
    ) -> list[Any]:
        """Mirror TunedHybridBackend.query's (query, k, where=) contract."""
        return list(self._hits[: max(1, top_k)])


async def _collect_rows(app: Any, top_k: int) -> list[dict[str, Any]]:
    """Call every registered read tool over FIXED_QUERY_SET, measure each result."""
    from mcp.server.mcpserver import Context

    rows: list[dict[str, Any]] = []
    tools = [t for t in READ_TOOLS if t in app._tool_manager._tools]  # type: ignore[attr-defined]
    for tool in tools:
        for query in FIXED_QUERY_SET:
            result = await app._tool_manager.call_tool(  # type: ignore[attr-defined]
                tool, {"query": query, "top_k": top_k}, Context(), convert_result=True
            )
            row = measure_result(result)
            row["tool"] = tool
            row["query"] = query
            rows.append(row)
    return rows


def run_pass(
    config: Any = None,
    top_k: int = 5,
    chunk_chars: int = 1000,
    backend_factory: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the fixed query set through the SDK tool-manager layer once.

    Builds a real app via ``supamem.mcp_server.build_app`` and calls each
    registered read tool over ``FIXED_QUERY_SET`` through
    ``app._tool_manager.call_tool(..., convert_result=True)`` — the same
    wrap/validate layer hosts exercise (SDK v2 call shape; the package pins
    ``mcp>=2,<3``). The backend seam is injectable: the default factory
    returns the deterministic offline fake, so callers never need to
    monkeypatch. The module-level ``_get_backend`` is swapped only for the
    duration of the pass and always restored (try/finally).
    """
    import supamem.mcp_server as mcp_mod
    from supamem.config import ResolvedConfig

    cfg = config if config is not None else ResolvedConfig()

    def _default_factory(_cfg: Any) -> _DeterministicFakeBackend:
        return _DeterministicFakeBackend(n_hits=top_k, chunk_chars=chunk_chars)

    factory = backend_factory if backend_factory is not None else _default_factory
    original = mcp_mod._get_backend
    mcp_mod._get_backend = factory  # type: ignore[assignment]
    try:
        app = mcp_mod.build_app(cfg)
        return asyncio.run(_collect_rows(app, top_k))
    finally:
        mcp_mod._get_backend = original


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one pass's rows into the report metrics.

    Percentile contract (LOCKED — changing this invalidates baselines):
    p50 uses ``statistics.median`` (true median; even-sized columns average
    the two middle values); p95 uses nearest-rank — the sorted column's
    ``ceil(0.95 * n)``-th value. Per-arm token p50s are computed the same way
    over the arm token columns so the pre-lever double-arm shape can be
    sanity-banded (plan 19-02 Task 2: text arm within [0.8x, 1.6x] of the
    structured arm).
    """
    if not rows:
        raise ValueError("aggregate: empty rows")
    est = sorted(r["est_tokens"] for r in rows)
    text_tok = sorted(r["text_tokens"] for r in rows)
    struct_tok = sorted(r["structured_tokens"] for r in rows)

    def _p95(col: list[int]) -> int:
        return col[math.ceil(0.95 * len(col)) - 1]

    return {
        "n_rows": len(rows),
        "mcp_response_tokens_p50": statistics.median(est),
        "mcp_response_tokens_p95": _p95(est),
        "text_arm_tokens_p50": statistics.median(text_tok),
        "structured_arm_tokens_p50": statistics.median(struct_tok),
    }


def to_report(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble run aggregates into a JSON-able baseline report.

    Schema discipline mirrors ``eval/coderag/gate.py::load_floors``
    (schema_version gate). Variance is recorded per metric as (max - min)
    relative to the runs' mean so plan 19-03's before/after deltas can be
    read against run-to-run noise.
    """
    if not runs:
        raise ValueError("to_report: empty runs")
    variance: dict[str, dict[str, float]] = {}
    for metric in _REPORT_METRICS:
        vals = [float(r[metric]) for r in runs]
        mean = sum(vals) / len(vals)
        spread = (max(vals) - min(vals)) / mean if mean else 0.0
        variance[metric] = {"max_min_over_mean": round(spread, 6)}
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mcp_version": importlib.metadata.version("mcp"),
        "supamem_version": importlib.metadata.version("supamem"),
        "runs": runs,
        "variance": variance,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m supamem.eval.mcp_response_tokens",
        description="Measure MCP tool-result token shape (Phase 19 MEASURE).",
    )
    parser.add_argument("--runs", type=int, default=3, help="independent passes (default: 3)")
    parser.add_argument(
        "--out", type=Path, default=None, help="write the JSON report to this path"
    )
    parser.add_argument(
        "--concise",
        action="store_true",
        help="reserved for plan 19-03 post-lever re-measurement; accepted, not yet used",
    )
    args = parser.parse_args(argv)

    runs: list[dict[str, Any]] = []
    for _ in range(args.runs):
        runs.append(aggregate(run_pass()))
    report = to_report(runs)
    report["workload"] = {
        "queries": len(FIXED_QUERY_SET),
        "read_tools": list(READ_TOOLS),
        "top_k": 5,
        "chunk_chars": 1000,
        "concise": bool(args.concise),
    }
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        console.print(f"[supamem.ok]✓[/supamem.ok] report written to {args.out}")
    first = runs[0]
    console.print(
        f"[supamem.info]→[/supamem.info] mcp_response_tokens p50/p95: "
        f"{first['mcp_response_tokens_p50']} / {first['mcp_response_tokens_p95']}"
    )
    console.print(
        f"[supamem.info]→[/supamem.info] text arm p50 {first['text_arm_tokens_p50']} · "
        f"structured arm p50 {first['structured_arm_tokens_p50']} (runs={args.runs})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
