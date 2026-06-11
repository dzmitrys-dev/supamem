"""Unit tests for CodeRAG no-regression floor gate (Plan 18-H, Req-05)."""

from __future__ import annotations

import pytest

from supamem.eval.coderag.gate import load_floors, passes_no_regression_floor
from supamem.eval.coderag.report import empty_envelope


def _envelope_with(**cells: float) -> dict:
    """Build envelope; keys are ``axis.column.metric``."""
    env = empty_envelope()
    for path, value in cells.items():
        axis, column, metric = path.split(".", 2)
        env["scores"][axis][column][metric] = value
    return env


def _passing_envelope() -> dict:
    """Synthetic envelope that clears all ADR §7 floors."""
    return _envelope_with(
        **{
            "code_fact.supamem_only.recall_at_5": 0.05,
            "code_fact.supamem_only.mrr": 0.7,
            "code_fact.supamem_only.ndcg_at_10": 0.6,
            "code_fact.supamem_only.latency_ms_p95": 3000.0,
            "code_fact.fastapi_only.recall_at_5": 0.01,
            "code_fact.fastapi_only.mrr": 0.55,
            "code_fact.fastapi_only.ndcg_at_10": 0.5,
            "code_fact.fastapi_only.latency_ms_p95": 3500.0,
            "code_fact.combined.recall_at_5": 0.05,
            "code_fact.combined.mrr": 0.99,
            "code_fact.combined.ndcg_at_10": 0.6,
            "code_fact.combined.latency_ms_p95": 3300.0,
            "decision_rationale.supamem_only.recall_at_5": 0.55,
            "decision_rationale.supamem_only.mrr": 0.2,
            "decision_rationale.supamem_only.ndcg_at_10": 0.3,
            "decision_rationale.supamem_only.latency_ms_p95": 4500.0,
        }
    )


def test_load_floors_returns_dict() -> None:
    data = load_floors()
    assert data["schema_version"] == 1
    assert isinstance(data["epsilon"], float)
    assert isinstance(data["floors"], dict)
    assert "code_fact.combined.recall_at_5" in data["floors"]


def test_passes_when_all_metrics_above_floor() -> None:
    floors = load_floors()
    ok, violations = passes_no_regression_floor(_passing_envelope(), floors)
    assert ok is True
    assert violations == []


def test_fails_when_recall_below_floor() -> None:
    floors = load_floors()
    env = _passing_envelope()
    env["scores"]["code_fact"]["combined"]["recall_at_5"] = 0.01
    ok, violations = passes_no_regression_floor(env, floors)
    assert ok is False
    assert violations
    assert any("code_fact.combined.recall_at_5" in v for v in violations)


def test_epsilon_allows_small_regression() -> None:
    baseline = _envelope_with(**{"code_fact.combined.recall_at_5": 0.5})
    candidate = _envelope_with(**{"code_fact.combined.recall_at_5": 0.495})
    floors = {"schema_version": 1, "epsilon": 0.01, "floors": {"code_fact.combined.recall_at_5": 0.0}}
    ok, violations = passes_no_regression_floor(
        candidate, floors, epsilon=0.01, baseline=baseline
    )
    assert ok is True
    assert violations == []


def test_epsilon_blocks_large_regression() -> None:
    baseline = _envelope_with(**{"code_fact.combined.recall_at_5": 0.5})
    candidate = _envelope_with(**{"code_fact.combined.recall_at_5": 0.48})
    floors = {"schema_version": 1, "epsilon": 0.01, "floors": {"code_fact.combined.recall_at_5": 0.0}}
    ok, violations = passes_no_regression_floor(
        candidate, floors, epsilon=0.01, baseline=baseline
    )
    assert ok is False
    assert violations
    assert any("code_fact.combined.recall_at_5" in v for v in violations)


def test_latency_ceiling_violation() -> None:
    floors = load_floors()
    env = _passing_envelope()
    env["scores"]["code_fact"]["combined"]["latency_ms_p95"] = 5000.0
    ok, violations = passes_no_regression_floor(env, floors)
    assert ok is False
    assert any("latency_ms_p95" in v for v in violations)
