"""Phase 14 Plan A Task A4 — baseline JSON schema migration tests.

Pin the dual-shape contract for ``_baseline_envelope`` (D-GATE-03):

- Legacy shape: top-level ``scores`` + ``by_axis`` only — must keep
  parsing for backwards-compat.
- Migrated shape: sibling ``unscoped`` + ``scoped`` keys, each carrying
  ``{scores, by_axis}``. Plan B's gate logic reads ``delta_scoped``.
- Legacy mirror: when the migrated shape is present, top-level
  ``scores`` and ``by_axis`` MUST equal ``unscoped.scores`` and
  ``unscoped.by_axis`` byte-for-byte (migration safety).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from supamem.eval.report import _baseline_envelope, build_report, load_baseline


# ---------------------------------------------------------------------------
# 1. legacy shape still parses


def test_load_baseline_legacy_shape_still_works(tmp_path: Path) -> None:
    """A JSON with only top-level scores parses; envelope returns legacy shape."""
    legacy: dict[str, Any] = {
        "version": "v0.0.0-legacy",
        "scores": {
            "tokens_per_correct_answer": 1000.0,
            "recall_at_5": 0.5,
        },
        "by_axis": {"single_session_user": {"tokens_per_correct_answer": 800.0}},
    }
    current = {"tokens_per_correct_answer": 950.0, "recall_at_5": 0.6}
    env = _baseline_envelope(current, legacy)
    assert env["version"] == "v0.0.0-legacy"
    assert "delta" in env
    assert env["delta"]["tokens_per_correct_answer"] == pytest.approx(-50.0)
    assert env["delta"]["recall_at_5"] == pytest.approx(0.1)


def test_load_baseline_migrated_shape_returns_per_pass() -> None:
    """A JSON with unscoped+scoped siblings parses; envelope exposes both."""
    migrated: dict[str, Any] = {
        "version": "v0.1.5",
        "unscoped": {
            "scores": {
                "tokens_per_correct_answer": 1374.59,
                "recall_at_5": 0.21,
            },
            "by_axis": {},
        },
        "scoped": {
            "scores": {
                "tokens_per_correct_answer": 900.0,
                "recall_at_5": 0.45,
            },
            "by_axis": {},
        },
        # legacy mirror for migration safety
        "scores": {
            "tokens_per_correct_answer": 1374.59,
            "recall_at_5": 0.21,
        },
        "by_axis": {},
    }
    current = {"tokens_per_correct_answer": 700.0, "recall_at_5": 0.5}
    env = _baseline_envelope(current, migrated)
    # Migrated envelope must carry per-pass deltas in addition to legacy delta.
    assert env["version"] == "v0.1.5"
    assert "delta_unscoped" in env
    assert "delta_scoped" in env
    assert env["delta_unscoped"]["tokens_per_correct_answer"] == pytest.approx(
        700.0 - 1374.59
    )
    assert env["delta_scoped"]["tokens_per_correct_answer"] == pytest.approx(
        700.0 - 900.0
    )
    # The legacy-shape delta is also present (mirror of unscoped).
    assert env["delta"]["tokens_per_correct_answer"] == pytest.approx(700.0 - 1374.59)


def test_legacy_mirror_preserved_for_migration_safety() -> None:
    """Top-level scores/by_axis equal unscoped.scores/unscoped.by_axis."""
    body = json.loads(
        Path("src/supamem/eval/baselines/v0.1.5.json").read_text(encoding="utf-8")
    )
    assert "unscoped" in body
    assert "scoped" in body
    assert body["scores"] == body["unscoped"]["scores"]
    assert body["by_axis"] == body["unscoped"]["by_axis"]
    # The legacy ~1374.59 number is preserved verbatim (full precision
    # from the original config-emulation capture).
    assert body["legacy_devdocs_unscoped_tpca"] == pytest.approx(1374.59, abs=0.05)
    # Capture method records the corpus change explicitly (D-GATE-05).
    assert body["capture_method"] == "config-emulation+scoped-recapture"
    # Capture notes call out the corpus change.
    notes = body.get("capture_notes", "")
    assert "LongMemEval" in notes or "haystack" in notes


def test_load_baseline_default_v015_uses_migrated_shape() -> None:
    """The shipped v0.1.5.json exposes both unscoped and scoped envelopes."""
    body = load_baseline("v0.1.5")
    assert "unscoped" in body
    assert "scoped" in body
    # Score-name set unchanged (REPORT_METRIC_NAMES still contract).
    expected_metric_names = {
        "recall_at_5",
        "context_precision",
        "context_recall",
        "answer_relevance",
        "tokens_per_correct_answer",
        "context_compression_ratio",
        "input_tokens_p50",
        "input_tokens_p95",
        "write_cost",
    }
    assert set(body["unscoped"]["scores"].keys()) == expected_metric_names
    assert set(body["scoped"]["scores"].keys()) == expected_metric_names


def test_build_report_envelope_with_migrated_baseline_carries_per_pass_deltas() -> None:
    """Integration: build_report folds migrated baseline shape into envelope."""
    baseline = load_baseline("v0.1.5")
    scores = {
        "recall_at_5": 0.30,
        "context_precision": None,
        "context_recall": None,
        "answer_relevance": None,
        "tokens_per_correct_answer": 1000.0,
        "context_compression_ratio": 400.0,
        "input_tokens_p50": 700.0,
        "input_tokens_p95": 900.0,
        "write_cost": 750.0,
    }
    env = build_report(
        suite="longmemeval_s",
        scores=scores,
        by_axis={},
        judge={"kind": "heuristic", "model": "n/a"},
        dataset={"name": "longmemeval_s", "revision": "x"},
        config_sha="abc",
        collection="supamem_eval_longmemeval_s",
        baseline_data=baseline,
    )
    assert env["baseline"]["version"] == "v0.1.5"
    assert "delta_unscoped" in env["baseline"]
    assert "delta_scoped" in env["baseline"]
