"""Phase 14 Plan B Task B2 — RED tests for the report.py sibling-key envelope.

Pinned contract:
- ``build_report`` accepts the nested per-record shape from Task B1 and
  emits ``scores: {unscoped, scoped}`` and ``by_axis: {unscoped, scoped}``
  sibling sub-dicts under the existing top-level keys.
- ``_compute_main_score`` for ``longmemeval_s`` reads
  ``envelope['scores']['scoped']['tokens_per_correct_answer']``
  (D-GATE-01 scoped-only gate).
- ``_compute_main_score`` for ``goldens`` retains the legacy flat reader
  on ``envelope['scores']['recall_at_5']``.
- The 10 top-level keys contract from ``test_eval_report.py`` is
  preserved — only the structure UNDER ``scores`` and ``by_axis`` changes.
- ``_baseline_envelope`` consumes the migrated v0.1.5 baseline shape and
  produces ``delta_unscoped`` + ``delta_scoped`` siblings; legacy single
  ``delta`` mirror is retained for unmigrated callers.
- ``REPORT_METRIC_NAMES`` is unchanged — both per-pass sub-dicts carry
  exactly those 9 names.
"""
from __future__ import annotations

from typing import Any

import pytest

from supamem.eval.report import (
    REPORT_METRIC_NAMES,
    _baseline_envelope,
    _compute_main_score,
    build_report,
    load_baseline,
)


# --------------------------------------------------------------------------- #
# Helpers


def _flat_scores(**overrides: Any) -> dict[str, Any]:
    """Return a 9-metric flat scores dict (legacy shape)."""
    out: dict[str, Any] = {
        "recall_at_5": 0.0,
        "context_precision": None,
        "context_recall": None,
        "answer_relevance": None,
        "tokens_per_correct_answer": 0.0,
        "context_compression_ratio": 0.0,
        "input_tokens_p50": 0.0,
        "input_tokens_p95": 0.0,
        "write_cost": 0.0,
    }
    out.update(overrides)
    return out


def _per_pass_scores(
    *, unscoped_overrides: dict[str, Any] | None = None,
    scoped_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "unscoped": _flat_scores(**(unscoped_overrides or {})),
        "scoped": _flat_scores(**(scoped_overrides or {})),
    }


def _per_pass_by_axis() -> dict[str, Any]:
    return {
        "unscoped": {"single_session_user": _flat_scores(recall_at_5=0.5)},
        "scoped": {"single_session_user": _flat_scores(recall_at_5=0.7)},
    }


def _build_inputs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        suite="longmemeval_s",
        scores=_per_pass_scores(),
        by_axis=_per_pass_by_axis(),
        judge={"kind": "heuristic", "model": "n/a"},
        dataset={"name": "longmemeval_s", "n": 1},
        config_sha="abc",
        collection="supamem_eval_longmemeval_s",
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Tests


def test_build_report_emits_sibling_keys() -> None:
    """build_report fed nested scores+by_axis emits the sibling-key envelope."""
    env = build_report(**_build_inputs())
    assert "scores" in env
    assert "by_axis" in env
    assert isinstance(env["scores"], dict)
    assert "unscoped" in env["scores"]
    assert "scoped" in env["scores"]
    assert isinstance(env["by_axis"], dict)
    assert "unscoped" in env["by_axis"]
    assert "scoped" in env["by_axis"]


def test_build_report_each_pass_carries_all_9_metric_names() -> None:
    env = build_report(**_build_inputs())
    assert set(env["scores"]["unscoped"].keys()) == set(REPORT_METRIC_NAMES)
    assert set(env["scores"]["scoped"].keys()) == set(REPORT_METRIC_NAMES)


def test_build_report_top_level_keys_unchanged() -> None:
    """The 10-key non-verbose envelope contract is preserved."""
    env = build_report(**_build_inputs())
    expected = {
        "supamem_version", "config_sha", "collection", "suite", "dataset",
        "judge", "main_score", "scores", "by_axis", "baseline",
    }
    assert set(env.keys()) == expected, set(env.keys()) ^ expected


def test_build_report_legacy_flat_shape_when_no_scoped() -> None:
    """Old flat shape still emits flat envelope (backwards-compat)."""
    inputs = _build_inputs(
        scores=_flat_scores(recall_at_5=0.5),
        by_axis={"single_session_user": _flat_scores()},
    )
    env = build_report(**inputs)
    # Flat input → flat output: scores carries the 9 metric names directly,
    # not 'unscoped'/'scoped' sub-dicts.
    assert "unscoped" not in env["scores"]
    assert "scoped" not in env["scores"]
    assert set(env["scores"].keys()) == set(REPORT_METRIC_NAMES)


def test_compute_main_score_longmemeval_s_reads_scoped_tpca() -> None:
    """D-GATE-01: longmemeval_s gate reads scores.scoped.tpca."""
    scores = _per_pass_scores(
        unscoped_overrides={"tokens_per_correct_answer": 1500.0},
        scoped_overrides={"tokens_per_correct_answer": 700.0},
    )
    main = _compute_main_score("longmemeval_s", scores)
    assert main == 700.0, (
        f"longmemeval_s gate must read scoped.tpca; got {main}"
    )


def test_compute_main_score_goldens_unchanged() -> None:
    """Goldens reader stays on the legacy flat shape (D-REPORT-02)."""
    scores = _flat_scores(recall_at_5=0.62)
    main = _compute_main_score("goldens", scores)
    assert main == 0.62


def test_baseline_envelope_consumes_migrated_baseline() -> None:
    """Migrated baseline (unscoped+scoped) yields delta_unscoped + delta_scoped."""
    baseline = {
        "version": "v0.1.5",
        "unscoped": {
            "scores": _flat_scores(tokens_per_correct_answer=1374.59),
            "by_axis": {},
        },
        "scoped": {
            "scores": _flat_scores(tokens_per_correct_answer=900.0),
            "by_axis": {},
        },
        # Legacy mirror.
        "scores": _flat_scores(tokens_per_correct_answer=1374.59),
        "by_axis": {},
    }
    scores = _per_pass_scores(
        unscoped_overrides={"tokens_per_correct_answer": 1200.0},
        scoped_overrides={"tokens_per_correct_answer": 600.0},
    )
    env = _baseline_envelope(scores, baseline)
    assert "delta_unscoped" in env
    assert "delta_scoped" in env
    assert env["delta_unscoped"]["tokens_per_correct_answer"] == pytest.approx(
        1200.0 - 1374.59
    )
    assert env["delta_scoped"]["tokens_per_correct_answer"] == pytest.approx(
        600.0 - 900.0
    )
    # The legacy delta is the unscoped-mirror delta.
    assert env["delta"]["tokens_per_correct_answer"] == pytest.approx(
        1200.0 - 1374.59
    )


def test_baseline_envelope_legacy_baseline_still_works() -> None:
    """Legacy single-shape baseline yields flat delta (no per-pass keys)."""
    baseline = {
        "version": "v0.0.0-legacy",
        "scores": _flat_scores(tokens_per_correct_answer=1000.0),
        "by_axis": {},
    }
    scores = _flat_scores(tokens_per_correct_answer=900.0)
    env = _baseline_envelope(scores, baseline)
    assert "delta" in env
    assert env["delta"]["tokens_per_correct_answer"] == pytest.approx(-100.0)
    assert "delta_unscoped" not in env
    assert "delta_scoped" not in env


def test_baseline_delta_scoped_field_present_in_envelope() -> None:
    """End-to-end: build_report folds migrated v0.1.5 baseline into the
    envelope and surfaces baseline.delta_scoped against the scoped pass."""
    baseline = load_baseline("v0.1.5")
    inputs = _build_inputs(baseline_data=baseline)
    env = build_report(**inputs)
    assert "delta_scoped" in env["baseline"]
    assert "delta_unscoped" in env["baseline"]
    assert "tokens_per_correct_answer" in env["baseline"]["delta_scoped"]


def test_main_score_on_envelope_for_longmemeval_s() -> None:
    """Integration: build_report's main_score reads scoped.tpca for longmemeval_s."""
    scores = _per_pass_scores(
        unscoped_overrides={"tokens_per_correct_answer": 1500.0},
        scoped_overrides={"tokens_per_correct_answer": 600.0},
    )
    env = build_report(**_build_inputs(scores=scores))
    assert env["main_score"] == 600.0
