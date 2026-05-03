"""Phase 10 Plan 10-01 — RED tests for the MTEB-style report envelope.

Locks the report shape from CONTEXT.md decisions D-REPORT-01..D-REPORT-02:

- Top-level keys: supamem_version, config_sha, collection, suite, dataset,
  judge, main_score, scores, by_axis, baseline. ``per_question`` only when
  ``verbose=True``.
- ``scores`` contains exactly the 9 metric names from D-REPORT-01.
- ``main_score`` mapping per suite (D-REPORT-02).
- ``baseline.delta`` carries signed floats; metrics absent from the loaded
  baseline are omitted (no KeyError).
- Writer output filename matches ``YYYY-MM-DDTHH-MM-SSZ.json``
  (filesystem-safe ISO with colons hyphenated).

All tests MUST FAIL today: ``supamem.eval.report`` does not exist.

Per D-07: this file imports NO SaaS LLM SDK.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


report_mod = pytest.importorskip("supamem.eval.report")


# D-REPORT-01: the 9 metric names locked in CONTEXT.md (G5).
EXPECTED_SCORE_KEYS = {
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

EXPECTED_TOPLEVEL_KEYS = {
    "supamem_version",
    "config_sha",
    "collection",
    "suite",
    "dataset",
    "judge",
    "main_score",
    "scores",
    "by_axis",
    "baseline",
}


def _stub_report_inputs():
    """Minimum kwargs for ``build_report``. Plan 10-04 may extend; we keep it loose."""
    return dict(
        suite="goldens",
        dataset={"name": "goldens", "n": 6},
        judge={"kind": "heuristic", "model": "n/a"},
        scores={k: 0.0 for k in EXPECTED_SCORE_KEYS},
        by_axis={},
        baseline_data={},
    )


def test_build_report_toplevel_keys_exact_set() -> None:
    """The non-verbose envelope contains exactly the 10 top-level keys."""
    rpt = report_mod.build_report(**_stub_report_inputs())
    assert set(rpt.keys()) == EXPECTED_TOPLEVEL_KEYS, set(rpt.keys()) ^ EXPECTED_TOPLEVEL_KEYS


def test_build_report_per_question_only_when_verbose() -> None:
    """``per_question`` MUST appear only with ``verbose=True``."""
    rpt_quiet = report_mod.build_report(**_stub_report_inputs(), verbose=False)
    assert "per_question" not in rpt_quiet
    rpt_loud = report_mod.build_report(**_stub_report_inputs(), verbose=True)
    assert "per_question" in rpt_loud


def test_scores_contain_exactly_the_nine_metrics() -> None:
    """D-REPORT-01: ``scores`` carries exactly the 9 metric names."""
    rpt = report_mod.build_report(**_stub_report_inputs())
    assert set(rpt["scores"].keys()) == EXPECTED_SCORE_KEYS


def test_main_score_for_longmemeval_is_tokens_per_correct_answer() -> None:
    """D-REPORT-02: ``main_score == scores['tokens_per_correct_answer']``
    for the ``longmemeval_s`` suite (the milestone gate metric)."""
    inputs = _stub_report_inputs()
    inputs["suite"] = "longmemeval_s"
    inputs["scores"] = {**inputs["scores"], "tokens_per_correct_answer": 1234.5}
    rpt = report_mod.build_report(**inputs)
    assert rpt["main_score"] == 1234.5


def test_main_score_for_goldens_is_recall_at_5() -> None:
    """D-REPORT-02: ``main_score == scores['recall_at_5']`` for ``goldens``."""
    inputs = _stub_report_inputs()
    inputs["suite"] = "goldens"
    inputs["scores"] = {**inputs["scores"], "recall_at_5": 0.62}
    rpt = report_mod.build_report(**inputs)
    assert rpt["main_score"] == 0.62


def test_baseline_delta_signed_floats_for_shared_metrics() -> None:
    """``baseline.delta`` carries signed floats only for metrics present in
    BOTH the current run and the loaded baseline JSON."""
    inputs = _stub_report_inputs()
    inputs["scores"] = {**inputs["scores"], "recall_at_5": 0.62}
    inputs["baseline_data"] = {"version": "v0.1.5", "scores": {"recall_at_5": 0.60}}
    rpt = report_mod.build_report(**inputs)
    assert "delta" in rpt["baseline"]
    delta = rpt["baseline"]["delta"]
    assert pytest.approx(delta["recall_at_5"], rel=1e-9) == 0.02


def test_baseline_delta_omits_metrics_absent_from_baseline() -> None:
    """Metrics absent from baseline JSON MUST NOT appear in ``baseline.delta``;
    no KeyError when the baseline pre-dates a metric introduction."""
    inputs = _stub_report_inputs()
    # Baseline only has recall_at_5; current run has all 9.
    inputs["baseline_data"] = {"version": "v0.1.5", "scores": {"recall_at_5": 0.55}}
    rpt = report_mod.build_report(**inputs)
    delta = rpt["baseline"]["delta"]
    # Only recall_at_5 must be in delta.
    assert set(delta.keys()) == {"recall_at_5"}


def test_writer_emits_filesystem_safe_iso_filename(tmp_path: Path) -> None:
    """D-REPORT-01: writer emits ``YYYY-MM-DDTHH-MM-SSZ.json`` (colons
    hyphenated for Windows compatibility)."""
    rpt = report_mod.build_report(**_stub_report_inputs())
    out = report_mod.write_report(rpt, out_dir=tmp_path)
    out_path = Path(out)
    assert out_path.parent == tmp_path
    assert out_path.suffix == ".json"
    # Stem matches YYYY-MM-DDTHH-MM-SSZ — note hyphens, not colons.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z", out_path.stem), out_path.stem
