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
