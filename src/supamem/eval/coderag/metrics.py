"""Thin :mod:`pytrec_eval` wrapper. BEIR-canonical scorer; don't hand-roll IR metrics.

Plan 15-A defined the surface; Plan 15-C wires the ``RelevanceEvaluator`` call.
Plan 16-C adds :func:`paired_bootstrap_delta` for peer-vs-supamem CI (D-BOOT-01).
Plan 17-A adds :func:`recall_at_k_chunk` + :func:`derive_gold_chunks` for
chunk-level recall (D-METRIC-01 + D-METRIC-03).
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
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
    # Average over ALL queries in qrels — not just those present in ``run``.
    # Queries with no retrieved hits contribute 0 to every metric. This is
    # required for INV-03 combined-dominance: a column where one repo's
    # queries retrieve nothing must NOT inflate the column's metric average
    # by silently dropping those queries from the denominator. Falls back
    # to ``len(results)`` only when qrels is empty (degenerate caller).
    n = len(qrels) if qrels else len(results)
    if n == 0:
        return {m: 0.0 for m in METRIC_SET}
    return {
        m: sum(r.get(m, 0.0) for r in results.values()) / n
        for m in METRIC_SET
    }


def paired_bootstrap_delta(
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    *,
    n_resamples: int = 10000,
    seed: int = 42,
) -> dict[str, float | int]:
    """Paired bootstrap delta with 95% percentile CI (D-BOOT-01).

    Returns ``mean(samples_a) - mean(samples_b)`` with 2.5/97.5 percentile CI
    from ``n_resamples`` paired index resamples. Caller is responsible for
    pairing samples by ``query_id`` before flattening to arrays (D-BOOT-02).

    Hand-rolled numpy — no scipy dependency (D-BOOT-05). Percentile CI is
    sufficient at retrieval-eval metric scale (Recall@k, MRR, nDCG ∈ [0,1]
    at n_queries ≥ 50, per D-BOOT-03).

    Sign convention is ``mean(samples_a) - mean(samples_b)``; Plan 16-D uses
    ``samples_a = peer_metric, samples_b = supamem_metric`` so positive delta
    = peer is better (D-PEER-02).

    Returns
    -------
    dict
        ``{"delta", "ci_lower", "ci_upper", "n_resamples", "seed"}``.
    """
    a = np.asarray(samples_a, dtype=float)
    b = np.asarray(samples_b, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError(
            "paired_bootstrap_delta: samples_a and samples_b must be 1-D arrays of equal length"
        )
    delta = float(a.mean() - b.mean())
    n = a.shape[0]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    deltas = a[idx].mean(axis=1) - b[idx].mean(axis=1)
    ci_lower = float(np.percentile(deltas, 2.5))
    ci_upper = float(np.percentile(deltas, 97.5))
    return {
        "delta": delta,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "n_resamples": int(n_resamples),
        "seed": int(seed),
    }


def recall_at_k_chunk(
    gold_chunk_ids: set[str],
    retrieved_chunk_ids: list[str],
    k: int,
) -> float:
    """Pure set-ratio chunk-level recall@k (D-METRIC-01).

    ``|gold ∩ top_k(retrieved)| / |gold|`` — the chunk-level analogue of
    ``pytrec_eval.recall``. Empty ``gold_chunk_ids`` yields 0.0 (zero-guard,
    no divide-by-zero) — matches the all-zero contract of :func:`score` for
    consistency with the doc-level path.

    Caller responsibility: ensure ``retrieved_chunk_ids`` is sorted by
    descending score (the runner builds it from a backend-ranked hit list).
    """
    if not gold_chunk_ids:
        return 0.0
    top_k = set(retrieved_chunk_ids[:k])
    return len(gold_chunk_ids & top_k) / len(gold_chunk_ids)


def derive_gold_chunks(
    gold_file_paths: Iterable[str],
    repo_root: Path,
    chunker_fn: Callable[[str], list[str]],
) -> dict[str, set[str]]:
    """Re-run the chunker over each gold file → ``{file_path: {chunk_id, ...}}``.

    For chunk-level scoring we don't carry chunk_ids in the gold-set on disk
    (RESEARCH Q-1 recommendation: ingest-time emission only, no manifest
    schema change). At scoring time we rebuild the gold chunk-id sets by
    re-running the same chunker over the gold files and applying the same
    SHA1[:12] formula used at ingest. Same chunker + same files = same hashes.

    Files that don't exist (corpus drift between run and gold-set) are
    skipped silently — the caller is expected to validate corpus integrity
    upstream. UTF-8 decode errors propagate; mismatched encodings between
    ingest and scoring would corrupt the hash equivalence regardless.
    """
    out: dict[str, set[str]] = {}
    for rel in gold_file_paths:
        path = repo_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        chunks = chunker_fn(text) or [text]
        ids: set[str] = set()
        for chunk in chunks:
            if not chunk.strip():
                continue
            ids.add(f"{rel}#{hashlib.sha1(chunk.encode()).hexdigest()[:12]}")
        out[rel] = ids
    return out


__all__ = [
    "METRIC_SET",
    "derive_gold_chunks",
    "paired_bootstrap_delta",
    "recall_at_k_chunk",
    "score",
]
