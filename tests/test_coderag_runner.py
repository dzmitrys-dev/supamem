"""Phase 15 Plan A Task A2 — suite_loader + cli.py wiring tests.

Locks:
- ``CodeRAGSuite.run`` returns the empty three-column-axis envelope.
- ``_build_backend`` overrides ``cfg.collection`` to ``supamem_eval_coderag`` ONLY
  when ``suite="coderag"``; goldens / longmemeval_s paths unchanged (Phase 14
  carry-locks).
- Caller's ``cfg`` is never mutated.
- ``cli.py`` does NOT contain a hardcoded ``if suite == "coderag":`` literal in
  its eval handler — dispatch goes through ``suite_loader`` entry-point lookup.
- ``suite_loader.list_suites()`` enumerates ``coderag``.
"""
from __future__ import annotations

import ast
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import pytest

from supamem.config import ResolvedConfig

import supamem.eval.runner as runner_mod
from supamem.eval import suite_loader


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "user_project"}
    base.update(overrides)
    return ResolvedConfig(**base)


# ---------------------------------------------------------------------------
# Suite class via entry-point


def test_run_coderag_callable_via_entry_point() -> None:
    (ep,) = (e for e in entry_points(group="supamem.eval") if e.name == "coderag")
    suite_cls = ep.load()
    envelope = suite_cls.run([], object())
    assert envelope["report_schema_version"] == "coderag.v1"


def test_run_coderag_returns_three_column_envelope() -> None:
    from supamem.eval.coderag import CodeRAGSuite

    envelope = CodeRAGSuite.run([], object())
    assert set(envelope.keys()) == {"report_schema_version", "scores", "peers"}
    assert set(envelope["scores"].keys()) == {"code_fact", "decision_rationale"}
    for axis_block in envelope["scores"].values():
        assert set(axis_block.keys()) == {"supamem_only", "fastapi_only", "combined"}


# ---------------------------------------------------------------------------
# _build_backend collection-override seam


def test_build_backend_overrides_collection_for_coderag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ResolvedConfig] = []

    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            captured.append(config)

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    runner_mod._build_backend(cfg, suite="coderag")

    assert len(captured) == 1
    assert captured[0].collection == "supamem_eval_coderag", (
        f"expected supamem_eval_coderag, got {captured[0].collection!r}"
    )


def test_build_backend_does_not_override_for_goldens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ResolvedConfig] = []

    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            captured.append(config)

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    runner_mod._build_backend(cfg, suite="goldens")
    runner_mod._build_backend(cfg)

    for got in captured:
        assert got.collection == "user_project"


def test_build_backend_does_not_mutate_caller_cfg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            pass

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    original = cfg.collection
    runner_mod._build_backend(cfg, suite="coderag")
    assert cfg.collection == original


# ---------------------------------------------------------------------------
# CLI dispatch — must NOT contain a hardcoded coderag literal


_CLI_PY = (
    Path(__file__).resolve().parent.parent / "src" / "supamem" / "cli.py"
)


def _cmd_evalbench_node() -> ast.FunctionDef:
    tree = ast.parse(_CLI_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_evalbench":
            return node
    raise AssertionError("cmd_evalbench function not found in cli.py")


def test_cli_eval_dispatch_coderag_via_entry_point() -> None:
    """cmd_evalbench body MUST NOT contain a literal `if suite == "coderag"`
    branch — dispatch must go through suite_loader."""
    fn = _cmd_evalbench_node()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            # Compare like: suite == "coderag"
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "suite"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value == "coderag"
            ):
                raise AssertionError(
                    "cli.cmd_evalbench contains hardcoded `suite == 'coderag'` "
                    "branch — dispatch must go through suite_loader entry-point lookup"
                )


def test_suite_loader_lists_coderag() -> None:
    suites = set(suite_loader.list_suites())
    assert "coderag" in suites, f"coderag missing from list_suites(): {suites!r}"


# ---------------------------------------------------------------------------
# Phase 15 Plan C Task C2 — _run_coderag body tests
# ---------------------------------------------------------------------------


class _FakeHit:
    """Minimal RetrievedChunk-shaped hit for runner tests."""

    def __init__(self, doc_id: str, score: float = 1.0) -> None:
        self.score = score
        self.payload = {"doc_id": doc_id}


class _RecordingBackend:
    """Records every backend.query call. Returns hits per a configurable map."""

    def __init__(self, hits_by_where: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.hits_by_where = hits_by_where or {}

    def query(self, text, k=20, *, where=None):  # noqa: ANN001, ANN003, ARG002
        self.calls.append({"text": text, "k": k, "where": where})
        # where=None as dict key is awkward; collapse to a stable token
        key = "combined" if where is None else (
            "supamem" if where.get("repo") == ["supamem"] else
            "fastapi" if where.get("repo") == ["fastapi"] else "other"
        )
        return self.hits_by_where.get(key, [])


def _record(qid: str, axis: str, repo: str, gold: list[str]) -> dict:
    return {"id": qid, "axis": axis, "repo": repo, "text": f"q-{qid}", "gold": gold}


def test_run_coderag_three_passes_per_query_for_code_fact() -> None:
    from supamem.eval.coderag.runner import _run_coderag

    backend = _RecordingBackend()
    records = [_record("q1", "code_fact", "supamem", ["d1"])]
    _run_coderag(records, backend)

    # Three retrieval passes per code_fact query (warmup excluded — only 1 record).
    # The single record drives 1 warmup call (where=None) + 3 measured
    # (supamem_only / fastapi_only / combined). Total = 4 calls.
    wheres = [c["where"] for c in backend.calls]
    # Every where used at least once
    assert {"repo": ["supamem"]} in wheres
    assert {"repo": ["fastapi"]} in wheres
    assert None in wheres


def test_run_coderag_decision_rationale_skips_fastapi_only_pass() -> None:
    from supamem.eval.coderag.runner import _run_coderag

    backend = _RecordingBackend()
    records = [_record("q1", "decision_rationale", "supamem", ["d1"])]
    envelope = _run_coderag(records, backend)

    # decision_rationale: 1 warmup (where=None) + 2 measured (supamem_only +
    # combined). The fastapi_only pass MUST NOT be issued.
    fastapi_calls = [
        c for c in backend.calls
        if c["where"] is not None and c["where"].get("repo") == ["fastapi"]
    ]
    assert fastapi_calls == [], (
        f"decision_rationale must skip fastapi_only; got: {fastapi_calls}"
    )
    # Envelope reflects INV-A1
    dr = envelope["scores"]["decision_rationale"]
    assert dr["fastapi_only"] is None
    if dr["supamem_only"] is not None:
        assert dr["combined"] == dr["supamem_only"]


def test_run_coderag_envelope_invariants_locked() -> None:
    from supamem.eval.coderag.runner import _run_coderag

    backend = _RecordingBackend(hits_by_where={
        "supamem": [_FakeHit("d1", 9.0)],
        "combined": [_FakeHit("d1", 9.0)],
    })
    records = [_record("q1", "decision_rationale", "supamem", ["d1"])]
    envelope = _run_coderag(records, backend)

    assert envelope["report_schema_version"] == "coderag.v1"
    dr = envelope["scores"]["decision_rationale"]
    assert dr["fastapi_only"] is None  # INV-A1
    assert dr["combined"] == dr["supamem_only"]  # INV-A1


def test_run_coderag_warmup_pass_untimed() -> None:
    """First WARMUP_QUERIES backend.query calls do NOT contribute to recorded latencies."""
    from supamem.eval.coderag.runner import WARMUP_QUERIES, _run_coderag

    backend = _RecordingBackend(hits_by_where={
        "supamem": [_FakeHit("g1", 9.0)],
        "fastapi": [_FakeHit("g1", 9.0)],
        "combined": [_FakeHit("g1", 9.0)],
    })
    records = [
        _record(f"q{i}", "code_fact", "supamem", ["g1"])
        for i in range(WARMUP_QUERIES + 5)  # 15 records
    ]
    _run_coderag(records, backend)

    # WARMUP_QUERIES warmup calls (where=None) issued first (one per warmup record),
    # THEN per-record measured passes (3 wheres each for code_fact).
    # Total = WARMUP_QUERIES + 3 * len(records)
    assert len(backend.calls) == WARMUP_QUERIES + 3 * len(records)
    # First WARMUP_QUERIES calls are warmups (where=None).
    for c in backend.calls[:WARMUP_QUERIES]:
        assert c["where"] is None


def test_run_coderag_latency_p95_geq_p50() -> None:
    """INV-06: per axis × column, latency_ms_p95 >= latency_ms_p50."""
    from supamem.eval.coderag.runner import _run_coderag

    backend = _RecordingBackend(hits_by_where={
        "supamem": [_FakeHit("g1", 9.0)],
        "fastapi": [_FakeHit("g1", 9.0)],
        "combined": [_FakeHit("g1", 9.0)],
    })
    records = [_record(f"q{i}", "code_fact", "supamem", ["g1"]) for i in range(5)]
    envelope = _run_coderag(records, backend)

    cf = envelope["scores"]["code_fact"]
    for col in ("supamem_only", "fastapi_only", "combined"):
        cell = cf[col]
        assert cell is not None, f"code_fact.{col} unexpectedly None"
        p50, p95 = cell["latency_ms_p50"], cell["latency_ms_p95"]
        if p50 is not None and p95 is not None:
            assert p95 >= p50, f"{col}: p95={p95} < p50={p50}"


def test_runner_dispatches_coderag_via_function_name() -> None:
    """src/supamem/eval/runner.py must reference _run_coderag by FUNCTION NAME
    in its dispatch path (A-D-PLAN-01 carry)."""
    runner_path = (
        Path(__file__).resolve().parent.parent / "src" / "supamem" / "eval" / "runner.py"
    )
    tree = ast.parse(runner_path.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        # Match `_run_coderag(...)` call OR import of `_run_coderag`
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run_coderag":
            found = True
            break
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "_run_coderag":
                    found = True
                    break
        if found:
            break
    assert found, "runner.py must reference _run_coderag by name (A-D-PLAN-01)"
