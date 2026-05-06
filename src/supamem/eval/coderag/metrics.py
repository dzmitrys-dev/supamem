"""Thin :mod:`pytrec_eval` wrapper. BEIR-canonical scorer; don't hand-roll IR metrics.

Plan 15-A defined the surface; Plan 15-C wires the ``RelevanceEvaluator`` call.
"""
from __future__ import annotations

import pytrec_eval

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
    """Score ``run`` against ``qrels`` returning a dict averaged across queries.

    ``qrels[qid][docid] = relevance``; ``run[qid][docid] = score`` (higher better).
    Returns the 6 :data:`METRIC_SET` keys, each averaged across queries.
    Empty ``run`` (no retrieved hits anywhere) yields all-zero metrics — by
    contract, no divide-by-zero, no exception, callers downstream can treat
    "scoreable but missed every gold" the same as "no run available".
    """
    if not run:
        return {m: 0.0 for m in METRIC_SET}
    evaluator = pytrec_eval.RelevanceEvaluator(qrels, set(METRIC_SET))
    results = evaluator.evaluate(run)
    if not results:
        return {m: 0.0 for m in METRIC_SET}
    n = len(results)
    return {
        m: sum(r.get(m, 0.0) for r in results.values()) / n
        for m in METRIC_SET
    }


__all__ = ["METRIC_SET", "score"]
