"""Behavior tests for the MCP-response token instrument (Plan 19-02, MEASURE).

RED phase (TDD): written BEFORE the implementation lands. Failures MUST be
assertion failures, never ImportError — the module skeleton carries the same
public surface as the finished module (repo red-phase discipline inherited
from tests/test_mcp_caps.py's header).

Contract cross-refs: 19-02-PLAN.md Task 1 <behavior> tests 1-5; RESEARCH §2.2
response anatomy (both arms carry the full payload pre-L1) and §4b baseline
methodology. The estimator is the shared eval-line one
(supamem.eval.runner._estimate_tokens) — test 5 pins that single source.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

import supamem.eval.mcp_response_tokens as mrt

# ── 1. measure_result — constructed tool-result object (both SDK shapes) ──


def test_measure_result_on_constructed_tool_result() -> None:
    n = 400
    payload = {"k": "v" * 120}
    m = len(json.dumps(payload, default=str, sort_keys=False))

    res = CallToolResult(
        content=[TextContent(type="text", text="a" * n)],
        structured_content=payload,
    )
    row = mrt.measure_result(res)
    assert row["text_chars"] == n
    assert row["structured_chars"] == m
    assert row["est_tokens"] == max(1, n // 4) + max(1, m // 4)

    # SDK v1 converted shape: (unstructured blocks, structured dict) tuple.
    row_tuple = mrt.measure_result(([TextContent(type="text", text="a" * n)], payload))
    assert row_tuple["text_chars"] == n
    assert row_tuple["structured_chars"] == m
    assert row_tuple["est_tokens"] == max(1, n // 4) + max(1, m // 4)


# ── 2. run_pass — rows per query per read tool, deterministic ─────────────


def test_run_pass_rows_per_read_tool_and_deterministic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SUPAMEM_QDRANT_ALIASES", raising=False)
    # Pin wall-clock so latency_ms serializes identically across invocations.
    monkeypatch.setattr("supamem.mcp_server.time.perf_counter", lambda: 1234.5)

    rows_a = mrt.run_pass()
    rows_b = mrt.run_pass()

    assert len(rows_a) == len(mrt.FIXED_QUERY_SET) * len(mrt.READ_TOOLS)
    for row in rows_a:
        assert {"tool", "text_chars", "structured_chars", "est_tokens"} <= set(row)
        assert row["tool"] in mrt.READ_TOOLS
        assert row["text_chars"] > 0
        assert row["structured_chars"] > 0
        assert row["est_tokens"] > 0
    assert rows_a == rows_b, "rows must be deterministic across two invocations"

    captured = capsys.readouterr()
    assert captured.out == "", "instrument must write nothing to stdout"


# ── 3. aggregate — p50/p95 contract over the est_tokens column ────────────


def test_aggregate_p50_p95_contract() -> None:
    vals = [100, 200, 300, 400, 1000]
    rows = [
        {
            "text_chars": v * 4,
            "structured_chars": v * 2,
            "text_tokens": v,
            "structured_tokens": v // 2,
            "est_tokens": v,
        }
        for v in vals
    ]
    agg = mrt.aggregate(rows)
    # p50: statistics.median → middle value of the sorted odd-length column.
    assert agg["mcp_response_tokens_p50"] == 300
    # p95: nearest-rank → ceil(0.95 * 5) = 5th sorted value.
    assert agg["mcp_response_tokens_p95"] == 1000
    assert agg["text_arm_tokens_p50"] == 300
    assert agg["structured_arm_tokens_p50"] == 150


# ── 4. import purity — importing writes nothing to stdout ─────────────────


def test_module_import_writes_nothing_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    importlib.reload(mrt)
    captured = capsys.readouterr()
    assert captured.out == ""


# ── 5. single-source estimator — import only, no local formula fork ───────


def test_single_source_estimator_import_no_local_formula() -> None:
    src = Path(mrt.__file__).read_text(encoding="utf-8")
    assert "from supamem.eval.runner import _estimate_tokens" in src, (
        "must import the shared estimator from supamem.eval.runner"
    )
    assert not re.search(r"//\s*4", src), (
        "local token-formula re-implementation found (chars-div-4 arithmetic)"
    )
