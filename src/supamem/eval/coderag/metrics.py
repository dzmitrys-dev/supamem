"""Thin :mod:`pytrec_eval` wrapper.

Plan 15-A defines surface; Plan 15-C wires the ``RelevanceEvaluator`` call.
"""
from __future__ import annotations

METRIC_SET = frozenset(
    {
        "recall_1",
        "recall_5",
        "recall_10",
        "recall_20",
        "recip_rank",
        "ndcg_cut_10",
    }
)


def score(
    qrels: dict[str, dict[str, int]],
    run: dict[str, dict[str, float]],
) -> dict[str, float]:
    """``qrels[qid][docid] = relevance``; ``run[qid][docid] = score`` (higher better)."""
    raise NotImplementedError("Plan 15-C wires pytrec_eval.RelevanceEvaluator.")


__all__ = ["METRIC_SET", "score"]
