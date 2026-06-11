"""Integration tests for CodeRAG rule-based autotune (Plan 18-I, Req-05)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from supamem.config import ResolvedConfig
from supamem.eval.coderag.gate import load_floors
from supamem.eval.coderag.report import empty_envelope


def _envelope_with(**cells: float) -> dict:
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
            "code_fact.supamem_only.recall_at_20": 0.05,
            "code_fact.supamem_only.mrr": 0.7,
            "code_fact.supamem_only.ndcg_at_10": 0.6,
            "code_fact.supamem_only.latency_ms_p95": 3000.0,
            "code_fact.combined.recall_at_5": 0.05,
            "code_fact.combined.recall_at_20": 0.05,
            "code_fact.combined.mrr": 0.996,
            "code_fact.combined.ndcg_at_10": 0.6,
            "code_fact.combined.latency_ms_p95": 3300.0,
            "decision_rationale.supamem_only.recall_at_1": 0.01,
            "decision_rationale.supamem_only.recall_at_5": 0.55,
            "decision_rationale.supamem_only.recall_at_20": 0.55,
            "decision_rationale.supamem_only.mrr": 0.2,
            "decision_rationale.supamem_only.ndcg_at_10": 0.3,
            "decision_rationale.supamem_only.latency_ms_p95": 4500.0,
        }
    )


def test_diagnose_proposes_prefetch_on_rationale_recall1_breach() -> None:
    from supamem.eval.autotune import ConfigDelta, diagnose

    floors = load_floors()
    env = _passing_envelope()
    env["scores"]["decision_rationale"]["supamem_only"]["recall_at_1"] = 0.0
    env["scores"]["decision_rationale"]["supamem_only"]["recall_at_5"] = 0.55

    proposals = diagnose(env, floors, cfg=ResolvedConfig())
    fields = {p.field for p in proposals}
    assert "reranker_prefetch_per_arm" in fields
    prefetch = next(p for p in proposals if p.field == "reranker_prefetch_per_arm")
    assert isinstance(prefetch, ConfigDelta)
    assert prefetch.value == 60
    assert "recall_at_1" in prefetch.reason


def test_diagnose_proposes_adaptive_depth_on_ndcg_breach() -> None:
    from supamem.eval.autotune import diagnose

    floors = load_floors()
    env = _passing_envelope()
    env["scores"]["code_fact"]["combined"]["ndcg_at_10"] = 0.4
    env["scores"]["code_fact"]["combined"]["latency_ms_p95"] = 3000.0

    proposals = diagnose(env, floors, cfg=ResolvedConfig())
    fields = {p.field for p in proposals}
    assert "adaptive_depth_enabled" in fields
    depth = next(p for p in proposals if p.field == "adaptive_depth_enabled")
    assert depth.value is True
    assert any(p.field == "adaptive_depth_delta" and p.value == 0.25 for p in proposals)


def test_diagnose_empty_when_all_floors_pass() -> None:
    from supamem.eval.autotune import diagnose

    floors = load_floors()
    proposals = diagnose(_passing_envelope(), floors, cfg=ResolvedConfig())
    assert proposals == []


def test_run_autotune_dry_run_writes_no_config(tmp_path: Path, monkeypatch) -> None:
    from supamem.eval.autotune import run_autotune

    monkeypatch.chdir(tmp_path)
    baseline = _passing_envelope()
    baseline["scores"]["decision_rationale"]["supamem_only"]["recall_at_1"] = 0.0

    with (
        patch("supamem.eval.autotune._observe_bench", return_value=baseline),
        patch("supamem.eval.autotune.persist_config") as mock_persist,
    ):
        rc = run_autotune(ResolvedConfig(), dry_run=True, apply=False)

    assert rc == 0
    mock_persist.assert_not_called()


def test_run_autotune_apply_refuses_when_gate_fails(tmp_path: Path, monkeypatch) -> None:
    from supamem.eval.autotune import run_autotune

    monkeypatch.chdir(tmp_path)
    baseline = _passing_envelope()
    baseline["scores"]["decision_rationale"]["supamem_only"]["recall_at_1"] = 0.0
    failing_trial = _passing_envelope()
    failing_trial["scores"]["code_fact"]["combined"]["recall_at_5"] = 0.0

    with (
        patch("supamem.eval.autotune._observe_bench", side_effect=[baseline, failing_trial]),
        patch("supamem.eval.autotune.persist_config") as mock_persist,
    ):
        rc = run_autotune(ResolvedConfig(), dry_run=False, apply=True)

    assert rc == 1
    mock_persist.assert_not_called()


def test_run_autotune_apply_persists_when_gate_passes(tmp_path: Path, monkeypatch) -> None:
    from supamem.eval.autotune import run_autotune

    monkeypatch.chdir(tmp_path)
    baseline = _passing_envelope()
    baseline["scores"]["decision_rationale"]["supamem_only"]["recall_at_1"] = 0.0
    passing_trial = _passing_envelope()

    with (
        patch("supamem.eval.autotune._observe_bench", side_effect=[baseline, passing_trial]),
        patch("supamem.eval.autotune.persist_config") as mock_persist,
    ):
        rc = run_autotune(ResolvedConfig(), dry_run=False, apply=True)

    assert rc == 0
    mock_persist.assert_called_once()
