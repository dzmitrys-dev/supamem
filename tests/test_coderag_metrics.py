"""Phase 15 Plan C Task C1 — pytrec_eval wrapper tests.

Locks:
- ``score(qrels, run)`` returns the 6 METRIC_SET keys averaged across queries.
- Empty ``run`` yields all-zero metrics (no divide-by-zero).
- INV-02 monotonicity ``recall_1 <= recall_5 <= recall_10 <= recall_20``.
- INV-05 bounds: ``0.0 <= recip_rank <= 1.0`` and ``0.0 <= ndcg_cut_10 <= 1.0``.
- ``pytrec_eval.RelevanceEvaluator`` is the canonical scorer (Don't Hand-Roll).
"""
from __future__ import annotations

import pytest

from supamem.eval.coderag import metrics


METRIC_KEYS = {
    "recall_1",
    "recall_5",
    "recall_10",
    "recall_20",
    "recip_rank",
    "ndcg_cut_10",
}


# Small handcrafted qrels + run -------------------------------------------------


def _qrels_three() -> dict[str, dict[str, int]]:
    return {
        "q1": {"d11": 1, "d12": 1},
        "q2": {"d21": 1, "d22": 1, "d23": 1},
        "q3": {"d31": 1},
    }


def _run_perfect() -> dict[str, dict[str, float]]:
    # First gold at rank 1 for every query.
    return {
        "q1": {"d11": 9.0, "dx": 1.0},
        "q2": {"d21": 9.0, "dx": 1.0},
        "q3": {"d31": 9.0, "dx": 1.0},
    }


def _run_partial() -> dict[str, dict[str, float]]:
    return {
        "q1": {"d11": 5.0, "dx": 4.0, "d12": 3.0},  # 2 gold in top-5
        "q2": {"dx": 9.0, "d21": 5.0, "dy": 4.0},   # 1 gold rank 2
        "q3": {"dx": 9.0, "dy": 5.0},               # miss
    }


# Tests -------------------------------------------------------------------------


def test_score_returns_six_metrics() -> None:
    out = metrics.score(_qrels_three(), _run_partial())
    assert isinstance(out, dict)
    assert set(out.keys()) == METRIC_KEYS


def test_score_perfect_run_yields_recip_rank_eq_1() -> None:
    # First gold at rank 1 for every query → MRR == 1.0.
    # recall_1 is bounded by min(1, 1/|gold|) per query and may be < 1 when
    # |gold| > 1 (only one doc can sit at rank 1) — so we assert MRR here
    # and bound recall_1 by its definitional maximum.
    out = metrics.score(_qrels_three(), _run_perfect())
    assert out["recip_rank"] == pytest.approx(1.0)
    # All three queries land their first gold at rank 1; the average of
    # 1/|gold_i| across the three queries (|gold| = 2, 3, 1) = (1/2 + 1/3 + 1) / 3.
    expected_recall_1 = (0.5 + 1.0 / 3.0 + 1.0) / 3.0
    assert out["recall_1"] == pytest.approx(expected_recall_1)


def test_score_recall_monotonicity() -> None:
    # INV-02
    out = metrics.score(_qrels_three(), _run_partial())
    assert out["recall_1"] <= out["recall_5"]
    assert out["recall_5"] <= out["recall_10"]
    assert out["recall_10"] <= out["recall_20"]


def test_score_mrr_ndcg_bounded() -> None:
    # INV-05
    out = metrics.score(_qrels_three(), _run_partial())
    assert 0.0 <= out["recip_rank"] <= 1.0
    assert 0.0 <= out["ndcg_cut_10"] <= 1.0


def test_score_empty_run_yields_zero() -> None:
    out = metrics.score(_qrels_three(), {})
    assert set(out.keys()) == METRIC_KEYS
    for v in out.values():
        assert v == 0.0


def test_score_uses_pytrec_eval_RelevanceEvaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeEvaluator:
        def __init__(self, qrels, metric_set):
            captured["qrels"] = qrels
            captured["metric_set"] = set(metric_set)

        def evaluate(self, run):  # noqa: ARG002
            # Return a per-query dict where each metric maps to 0.5 — enough
            # to force the wrapper through its averaging code path.
            return {qid: dict.fromkeys(captured["metric_set"], 0.5) for qid in run}

    import pytrec_eval
    monkeypatch.setattr(pytrec_eval, "RelevanceEvaluator", FakeEvaluator)

    out = metrics.score({"q1": {"d1": 1}}, {"q1": {"d1": 1.0}})
    assert captured["qrels"] == {"q1": {"d1": 1}}
    assert captured["metric_set"] >= METRIC_KEYS
    for k in METRIC_KEYS:
        assert out[k] == pytest.approx(0.5)
