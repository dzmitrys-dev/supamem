"""``_run_coderag`` mirrors ``_run_longmemeval`` shape (A-D-PLAN-01: by FUNCTION
NAME, not line number).

Plan 15-A scope: empty three-column-axis envelope.
Plan 15-C scope: real per-query 3-pass retrieval + per-pass scoring + latency.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import numpy as np

from supamem.eval.coderag.metrics import score as score_pytrec
from supamem.eval.coderag.report import column_metrics, envelope_from_results

# 10-query warmup loop precedes the measured loop (Pitfall 5 mitigation —
# cold-start outliers otherwise dominate p95 latency).
WARMUP_QUERIES = 10
DEFAULT_K = 20

# A-D-HAY-04: fastapi has no ADR axis, so the rationale axis skips the
# fastapi_only retrieval pass and emits ``null`` for that column.
_DR_AXIS = "decision_rationale"


def _hit_doc_id(hit: Any) -> str | None:
    """Extract the gold-set citation key from a backend hit's payload.

    The payload contract (set by 15-B ingest under the controlled write path)
    is ``payload["doc_id"] == file_path == repo-relative posix path``. Falls
    back to ``payload["file_path"]`` for forward-compat with older fixtures.
    """
    payload = getattr(hit, "payload", None) or {}
    return payload.get("doc_id") or payload.get("file_path")


def _build_run(qid: str, hits: list) -> dict[str, dict[str, float]]:
    """Translate a list of hits → pytrec_eval ``run`` dict ``{qid: {doc_id: score}}``."""
    run: dict[str, float] = {}
    for hit in hits:
        doc_id = _hit_doc_id(hit)
        if doc_id is None:
            continue
        # Last-write-wins on duplicate doc_ids — runner contract.
        run[doc_id] = float(getattr(hit, "score", 0.0))
    return {qid: run} if run else {}


def _percentile(values: list[float], p: float) -> float | None:
    """numpy.percentile with empty-list short-circuit. Returns None on empty."""
    if not values:
        return None
    return float(np.percentile(values, p))


def _run_coderag(records, backend, *, k: int = DEFAULT_K, **kwargs) -> dict[str, Any]:  # noqa: ANN001, ANN003
    """Per-query: 3 retrieval passes (or 2 for decision_rationale).

    For each record ``{id, axis, repo, text, gold}``:
      - **code_fact** axis: 3 backend.query passes —
        ``where={"repo":["supamem"]}`` / ``where={"repo":["fastapi"]}`` /
        ``where=None`` (combined). Each timed via ``time.perf_counter()``.
      - **decision_rationale** axis: 2 passes — supamem_only + combined.
        ``fastapi_only`` is SKIPPED (A-D-HAY-04). The envelope's
        ``decision_rationale.fastapi_only`` is ``null`` and ``combined``
        collapses to ``supamem_only`` per INV-A1 (enforced in
        :func:`envelope_from_results`).

    A 10-query warmup pass (untimed) precedes the measured loop — Pitfall 5
    mitigation: without warmup, the first query's cold-start I/O + JIT +
    page-fault tail dominates p95.
    """
    # ── Step 0: warmup (untimed) ────────────────────────────────────────
    # Fire WARMUP_QUERIES untimed combined-pass queries so the embedder,
    # reranker, and Qdrant connection are all hot before we measure
    # anything. Failures are swallowed — warmup is best-effort.
    for q in records[:WARMUP_QUERIES]:
        try:
            backend.query(q["text"], k=k, where=None)
        except Exception:  # noqa: BLE001 — warmup is best-effort
            pass

    # ── Step 1: per-axis × per-column accumulators ──────────────────────
    # Accumulate run dicts and qrels across all records of the same axis,
    # then score once per (axis, column). This is what pytrec_eval canonically
    # expects (one Evaluator instance averages across queries).
    per_axis_run: dict[str, dict[str, dict[str, dict[str, float]]]] = defaultdict(
        lambda: {"supamem_only": {}, "fastapi_only": {}, "combined": {}}
    )
    per_axis_qrels: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    per_axis_lat: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"supamem_only": [], "fastapi_only": [], "combined": []}
    )

    for q in records:
        qid = str(q["id"])
        axis = q["axis"]
        gold = list(q.get("gold") or [])
        text = q["text"]

        # Per-query qrels: every gold doc_id has relevance 1. Joined into the
        # axis's accumulated qrels dict.
        per_axis_qrels[axis][qid] = {doc_id: 1 for doc_id in gold}

        # ── Pass 1: supamem_only ────────────────────────────────────────
        t0 = time.perf_counter()
        hits_sup = backend.query(text, k=k, where={"repo": ["supamem"]})
        per_axis_lat[axis]["supamem_only"].append(
            (time.perf_counter() - t0) * 1000.0
        )
        run_sup = _build_run(qid, hits_sup)
        if run_sup:
            per_axis_run[axis]["supamem_only"].update(run_sup)

        # ── Pass 2: fastapi_only — SKIP for decision_rationale (A-D-HAY-04) ─
        if axis != _DR_AXIS:
            t0 = time.perf_counter()
            hits_fap = backend.query(text, k=k, where={"repo": ["fastapi"]})
            per_axis_lat[axis]["fastapi_only"].append(
                (time.perf_counter() - t0) * 1000.0
            )
            run_fap = _build_run(qid, hits_fap)
            if run_fap:
                per_axis_run[axis]["fastapi_only"].update(run_fap)

        # ── Pass 3: combined ────────────────────────────────────────────
        t0 = time.perf_counter()
        hits_comb = backend.query(text, k=k, where=None)
        per_axis_lat[axis]["combined"].append(
            (time.perf_counter() - t0) * 1000.0
        )
        run_comb = _build_run(qid, hits_comb)
        if run_comb:
            per_axis_run[axis]["combined"].update(run_comb)

    # ── Step 2: score each axis × column ────────────────────────────────
    per_axis_per_column: dict[str, dict[str, dict | None]] = {}
    for axis in ("code_fact", _DR_AXIS):
        qrels = per_axis_qrels.get(axis, {})
        cols: dict[str, dict | None] = {}
        for col in ("supamem_only", "fastapi_only", "combined"):
            if axis == _DR_AXIS and col == "fastapi_only":
                # INV-A1: fastapi_only column is null on the rationale axis.
                cols[col] = None
                continue
            if not qrels:
                cols[col] = None
                continue
            run_dict = per_axis_run[axis].get(col, {})
            pytrec_scores = score_pytrec(qrels, run_dict)
            lats = per_axis_lat[axis][col]
            cols[col] = column_metrics(
                pytrec_scores,
                _percentile(lats, 50),
                _percentile(lats, 95),
            )
        per_axis_per_column[axis] = cols

    # ── Step 3: build envelope (envelope_from_results enforces INV-A1) ──
    return envelope_from_results(per_axis_per_column, peers=kwargs.get("peers"))


__all__ = ["WARMUP_QUERIES", "DEFAULT_K", "_run_coderag"]
