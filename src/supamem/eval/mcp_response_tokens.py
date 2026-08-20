"""MCP-response token instrument (Phase 19 MEASURE, Plan 19-02) — RED skeleton.

Measures the serialized wire shape of MCP tool results — the TextContent arm
(pretty-printed JSON the SDK derives from the returned model) plus the
structuredContent arm — for a fixed deterministic query set, called through
the same SDK tool-manager layer hosts use (``mcp_server.build_app`` +
``app._tool_manager.call_tool``). Full rationale and methodology: Phase 19
RESEARCH §2.2/§2.3/§4b. The existing tpca metric cannot see the serialized
tool result, so every response-shape lever in plan 19-03 would show zero
effect without this instrument (measure-first discipline, PUB-05).

This skeleton exists so the RED-phase tests fail with assertion failures
rather than ImportError (repo red-phase discipline). GREEN implements:
``measure_result``, ``run_pass``, ``aggregate``, ``to_report``, and the
``__main__`` CLI.
"""
from __future__ import annotations

from typing import Any

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


def measure_result(result: Any) -> dict[str, Any]:
    """Measure both arms of one SDK tool-manager call result.

    GREEN contract: duck-typed over the installed SDK's call_tool return —
    a result exposing content + structuredContent/structured_content is read
    directly; a (unstructured blocks, structured dict) tuple uses the
    elements. Returns text_chars, structured_chars, per-arm token estimates,
    and est_tokens (sum of the arm estimates, computed via the shared
    estimator imported from supamem.eval.runner — never re-implemented here).
    """
    raise AssertionError("RED skeleton: measure_result not implemented (Plan 19-02 Task 1 GREEN)")


def run_pass(
    config: Any = None,
    top_k: int = 5,
    chunk_chars: int = 1000,
    backend_factory: Any = None,
) -> list[dict[str, Any]]:
    """Run FIXED_QUERY_SET through every registered read tool once.

    GREEN contract: builds the app via mcp_server.build_app, swaps the
    module-level _get_backend seam to an injectable backend factory
    (default: deterministic offline fake — 5 RetrievedChunk hits of
    chunk_chars-char texts, 26-char source paths, per RESEARCH §2.2), calls
    each read tool over FIXED_QUERY_SET through app._tool_manager.call_tool,
    and returns one row per query per read tool. Offline: no Qdrant, no
    network. Writes nothing to stdout.
    """
    raise AssertionError("RED skeleton: run_pass not implemented (Plan 19-02 Task 1 GREEN)")


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one pass's rows into report metrics.

    GREEN contract: p50 = statistics.median over the est_tokens column;
    p95 = nearest-rank (ceil(0.95 * n)-th sorted value) — percentile method
    LOCKED in the docstring. Also reports per-arm token p50s so the pre-lever
    double-arm shape can be sanity-banded (plan 19-02 Task 2).
    """
    raise AssertionError("RED skeleton: aggregate not implemented (Plan 19-02 Task 1 GREEN)")


def to_report(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble run aggregates into a JSON-able baseline report.

    GREEN contract: schema_version 1 (gate.py load_floors discipline),
    captured_at, mcp_version, supamem_version, the runs list, and a variance
    note (max-min relative to mean per metric).
    """
    raise AssertionError("RED skeleton: to_report not implemented (Plan 19-02 Task 1 GREEN)")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit("RED skeleton: CLI lands with the GREEN implementation (Plan 19-02)")
