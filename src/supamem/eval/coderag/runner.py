"""``_run_coderag`` mirrors ``_run_longmemeval`` shape (A-D-PLAN-01: by FUNCTION
NAME, not line number).

Plan 15-A scope: empty three-column-axis envelope.
Plan 15-C scope: real per-query 3-pass retrieval + per-pass scoring + latency.
Plan 16-E Task 3a: peer-scoring loop — when ``peers={name: {"adapter": ...}}``
is supplied, drive each adapter through the same axis × col passes, build
per-query metric maps (UN-averaged), and forward as ``peer_run_data`` so the
16-D bootstrap-delta branch in :func:`envelope_from_results` populates
``envelope.peers[name].scores`` and ``envelope.comparisons.{name}_vs_supamem``.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import numpy as np
import pytrec_eval

from supamem.console import err_console
from supamem.eval.coderag.metrics import (
    METRIC_SET,
    derive_gold_chunks,
    recall_at_k_chunk,
)
from supamem.eval.coderag.metrics import score as score_pytrec
from supamem.eval.coderag.report import column_metrics, envelope_from_results

_CHUNK_RECALL_KS: tuple[int, ...] = (1, 5, 10, 20)

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


def _hit_chunk_id(hit: Any) -> str | None:
    """Extract the chunk-level identifier set by Phase 17-A ingest.

    Mirrors :func:`_hit_doc_id` shape. Reads ``payload["chunk_id"]`` with no
    fallback — chunk_id is REQUIRED post-Phase 17 (D-METRIC-03). Returns
    None for hits whose payload predates the migration so the runner stays
    forward-compatible with mixed corpora during the transition.
    """
    payload = getattr(hit, "payload", None) or {}
    return payload.get("chunk_id")


def _build_run_chunk(qid: str, hits: list) -> dict[str, dict[str, float]]:
    """Chunk-level sibling of :func:`_build_run` — does NOT dedup.

    Each distinct ``chunk_id`` gets its own slot in the inner dict, so two
    hits referencing different chunks of the same file both contribute.
    Pitfall 4 mitigation: the doc-level path's last-write-wins on doc_id
    collapses chunk-level signal; this function is the carry path that
    keeps that signal alive for chunk-level recall scoring.
    """
    run: dict[str, float] = {}
    for hit in hits:
        cid = _hit_chunk_id(hit)
        if cid is None:
            continue
        # NO doc-level dedup — distinct chunk_ids of the same doc_id all
        # get separate slots. (Same chunk_id seen twice is degenerate and
        # last-write-wins is safe per chunk.)
        run[cid] = float(getattr(hit, "score", 0.0))
    return {qid: run} if run else {}


def _percentile(values: list[float], p: float) -> float | None:
    """numpy.percentile with empty-list short-circuit. Returns None on empty."""
    if not values:
        return None
    return float(np.percentile(values, p))


def _per_query_metric_map(
    qrels: dict[str, dict[str, int]],
    run: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Pivot ``pytrec_eval.RelevanceEvaluator.evaluate`` → ``{metric: {qid: float}}``.

    Plan 16-E Task 3a: paired_bootstrap_delta needs UN-averaged per-query
    samples — :func:`score` averages across queries (metrics.py:45-50), so
    we call the evaluator directly here. Empty ``run`` short-circuits to
    empty per-metric maps; queries absent from ``run`` simply don't appear
    in the per-query map (the bootstrap then pairs by sorted intersection).
    """
    metrics = set(METRIC_SET)
    if not qrels or not run:
        return {m: {} for m in metrics}
    evaluator = pytrec_eval.RelevanceEvaluator(qrels, metrics)
    per_qid = evaluator.evaluate(run)
    pivoted: dict[str, dict[str, float]] = {m: {} for m in metrics}
    for qid, metric_map in per_qid.items():
        for m in metrics:
            pivoted[m][qid] = float(metric_map.get(m, 0.0))
    return pivoted


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

    Plan 16-E Task 3a — peer scoring
    --------------------------------
    When ``kwargs["peers"]`` is supplied as ``{name: {"adapter": <obj>, ...}}``
    (the 15-C-shape that ``eval/runner.py`` builds for ``--peer mem0``), each
    adapter is driven through the same axis × col passes. Adapter-side hits
    populate ``peer_axis_run[name][axis][col]`` and ``peer_axis_lat[name]...``.
    After the supamem-side scoring (Step 2), peer ``column_metrics`` and
    per-query metric maps are computed and forwarded as ``peer_run_data`` so
    :func:`envelope_from_results` (the 16-D bootstrap-delta branch) populates
    ``envelope.peers[name].scores`` and ``envelope.comparisons.{name}_vs_supamem``.

    Adapter faults are caught and surfaced via :data:`err_console`; the failing
    peer is marked ``failed`` and dropped from ``peer_run_data`` so the
    supamem-side scoring is never crashed by a peer fault. Non-peer runs (no
    ``peers`` kwarg or no ``adapter`` slot) bypass this entire branch and pay
    zero cost (D-BOOT-05).
    """
    # ── Peer adapter detection ──────────────────────────────────────────
    raw_peers = kwargs.get("peers") or {}
    peer_adapters: dict[str, Any] = {
        name: blob["adapter"]
        for name, blob in raw_peers.items()
        if isinstance(blob, dict) and "adapter" in blob
    }
    peer_axis_run: dict[str, dict[str, dict[str, dict[str, float]]]] = {
        name: defaultdict(
            lambda: {"supamem_only": {}, "fastapi_only": {}, "combined": {}}
        )
        for name in peer_adapters
    }
    peer_axis_lat: dict[str, dict[str, dict[str, list[float]]]] = {
        name: defaultdict(
            lambda: {"supamem_only": [], "fastapi_only": [], "combined": []}
        )
        for name in peer_adapters
    }
    peer_failed: set[str] = set()

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

    # Phase 17-A: per-query chunk-level runs (NOT deduped on doc_id; see
    # `_build_run_chunk`). Indexed by (axis, col, qid) → ordered list of
    # chunk_ids returned by the backend (rank-preserving). Used for
    # chunk-level recall@k scoring at Step 2 below.
    per_axis_chunk_runs: dict[
        str, dict[str, dict[str, list[str]]]
    ] = defaultdict(
        lambda: {"supamem_only": {}, "fastapi_only": {}, "combined": {}}
    )
    # Per-query chunk_id sets for the gold side. None when the caller did
    # not supply (repo_root, chunker_fn) — chunk-level scoring then no-ops
    # and column_metrics emits None chunk_recalls (T-17-03 friendliness).
    repo_root = kwargs.get("repo_root_for_chunks")
    chunker_fn = kwargs.get("chunker_for_chunks")
    chunk_scoring_enabled = repo_root is not None and chunker_fn is not None
    per_qid_gold_chunks: dict[str, set[str]] = {}

    def _peer_pass(name: str, adapter: Any, qid: str, axis: str, col: str,
                   text: str, where: dict[str, list[str]] | None) -> None:
        """Drive one adapter pass; on failure, mark peer failed and surface once."""
        if name in peer_failed:
            return
        try:
            t0 = time.perf_counter()
            hits = adapter.query(text, k=k, where=where)
            peer_axis_lat[name][axis][col].append(
                (time.perf_counter() - t0) * 1000.0
            )
            run_p = _build_run(qid, hits)
            if run_p:
                peer_axis_run[name][axis][col].update(run_p)
        except Exception as exc:  # noqa: BLE001 — peer fault is degraded-not-swallowed
            err_console.print(
                f"[supamem.warn]coderag: peer {name} query failed at "
                f"qid={qid} axis={axis} col={col}: {exc!r}; "
                f"degrading to empty peer envelope[/supamem.warn]"
            )
            peer_failed.add(name)

    for q in records:
        qid = str(q["id"])
        axis = q["axis"]
        gold = list(q.get("gold") or [])
        text = q["text"]

        # Per-query qrels: every gold doc_id has relevance 1. Joined into the
        # axis's accumulated qrels dict.
        per_axis_qrels[axis][qid] = {doc_id: 1 for doc_id in gold}

        # Phase 17-A: derive gold-chunk set once per qid (deterministic
        # SHA1[:12] over chunker output of each gold file). No-ops when the
        # caller did not supply repo_root + chunker_fn.
        if chunk_scoring_enabled:
            gold_chunk_map = derive_gold_chunks(gold, repo_root, chunker_fn)
            qid_gold: set[str] = set()
            for ids in gold_chunk_map.values():
                qid_gold.update(ids)
            per_qid_gold_chunks[qid] = qid_gold

        # ── Pass 1: supamem_only ────────────────────────────────────────
        t0 = time.perf_counter()
        hits_sup = backend.query(text, k=k, where={"repo": ["supamem"]})
        per_axis_lat[axis]["supamem_only"].append(
            (time.perf_counter() - t0) * 1000.0
        )
        run_sup = _build_run(qid, hits_sup)
        if run_sup:
            per_axis_run[axis]["supamem_only"].update(run_sup)
        if chunk_scoring_enabled:
            per_axis_chunk_runs[axis]["supamem_only"][qid] = [
                cid for cid in (_hit_chunk_id(h) for h in hits_sup) if cid
            ]
        for name, adapter in peer_adapters.items():
            _peer_pass(name, adapter, qid, axis, "supamem_only", text,
                       {"repo": ["supamem"]})

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
            if chunk_scoring_enabled:
                per_axis_chunk_runs[axis]["fastapi_only"][qid] = [
                    cid for cid in (_hit_chunk_id(h) for h in hits_fap) if cid
                ]
            for name, adapter in peer_adapters.items():
                _peer_pass(name, adapter, qid, axis, "fastapi_only", text,
                           {"repo": ["fastapi"]})

        # ── Pass 3: combined ────────────────────────────────────────────
        t0 = time.perf_counter()
        hits_comb = backend.query(text, k=k, where=None)
        per_axis_lat[axis]["combined"].append(
            (time.perf_counter() - t0) * 1000.0
        )
        run_comb = _build_run(qid, hits_comb)
        if run_comb:
            per_axis_run[axis]["combined"].update(run_comb)
        if chunk_scoring_enabled:
            per_axis_chunk_runs[axis]["combined"][qid] = [
                cid for cid in (_hit_chunk_id(h) for h in hits_comb) if cid
            ]
        for name, adapter in peer_adapters.items():
            _peer_pass(name, adapter, qid, axis, "combined", text, None)

    # ── Step 2: score each axis × column (supamem) ──────────────────────
    per_axis_per_column: dict[str, dict[str, dict | None]] = {}
    supamem_per_query_metrics: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for axis in ("code_fact", _DR_AXIS):
        qrels = per_axis_qrels.get(axis, {})
        cols: dict[str, dict | None] = {}
        supamem_per_query_metrics[axis] = {}
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
            chunk_recalls: dict[str, float] | None = None
            if chunk_scoring_enabled:
                # Per-query mean across the qrels denominator (matches the
                # doc-level path's "average over ALL queries" contract in
                # metrics.score — INV-03 combined-dominance preservation).
                chunk_runs_for_col = per_axis_chunk_runs[axis].get(col, {})
                n_q = len(qrels) or 1
                chunk_recalls = {}
                for kk in _CHUNK_RECALL_KS:
                    total = 0.0
                    for q_id_inner in qrels:
                        gold_set = per_qid_gold_chunks.get(q_id_inner, set())
                        retrieved = chunk_runs_for_col.get(q_id_inner, [])
                        total += recall_at_k_chunk(gold_set, retrieved, kk)
                    chunk_recalls[f"recall_at_{kk}_chunk"] = total / n_q
            cols[col] = column_metrics(
                pytrec_scores,
                _percentile(lats, 50),
                _percentile(lats, 95),
                chunk_recalls=chunk_recalls,
            )
            supamem_per_query_metrics[axis][col] = _per_query_metric_map(
                qrels, run_dict
            )
        per_axis_per_column[axis] = cols

    # ── Step 2b: score each axis × column for every active peer ─────────
    peer_scores: dict[str, dict[str, dict[str, dict | None]]] = {}
    peer_per_query_metrics: dict[
        str, dict[str, dict[str, dict[str, dict[str, float]]]]
    ] = {}
    for name in peer_adapters:
        if name in peer_failed:
            continue
        peer_scores[name] = {}
        peer_per_query_metrics[name] = {}
        for axis in ("code_fact", _DR_AXIS):
            qrels = per_axis_qrels.get(axis, {})
            cols_p: dict[str, dict | None] = {}
            peer_per_query_metrics[name][axis] = {}
            for col in ("supamem_only", "fastapi_only", "combined"):
                if axis == _DR_AXIS and col == "fastapi_only":
                    cols_p[col] = None
                    continue
                if not qrels:
                    cols_p[col] = None
                    continue
                run_dict_p = peer_axis_run[name][axis].get(col, {})
                pytrec_scores_p = score_pytrec(qrels, run_dict_p)
                lats_p = peer_axis_lat[name][axis][col]
                cols_p[col] = column_metrics(
                    pytrec_scores_p,
                    _percentile(lats_p, 50),
                    _percentile(lats_p, 95),
                )
                peer_per_query_metrics[name][axis][col] = _per_query_metric_map(
                    qrels, run_dict_p
                )
            peer_scores[name][axis] = cols_p

    # ── Step 3: build envelope (envelope_from_results enforces INV-A1) ──
    peer_run_data: dict[str, dict] | None = None
    if peer_scores:
        peer_run_data = {
            name: {
                "scores": peer_scores[name],
                "per_query_metrics": peer_per_query_metrics[name],
                "supamem_per_query_metrics": supamem_per_query_metrics,
            }
            for name in peer_scores
        }

    # Forward the legacy ``peers`` kwarg ONLY when peer-scoring did NOT fire
    # at all (no adapter slot in any peer blob). When ``peer_adapters`` is
    # non-empty, the peer-scoring branch was taken — even if every peer
    # failed and ``peer_run_data`` is None, the contract is degraded-not-
    # crashed: emit empty peers/comparisons rather than re-emitting the
    # adapter blob (which is JSON-unserializable; that was the 16-E blocker).
    legacy_peers = raw_peers if not peer_adapters and raw_peers else None

    return envelope_from_results(
        per_axis_per_column,
        peers=legacy_peers,
        peer_run_data=peer_run_data,
    )


__all__ = [
    "DEFAULT_K",
    "WARMUP_QUERIES",
    "_build_run",
    "_build_run_chunk",
    "_hit_chunk_id",
    "_hit_doc_id",
    "_run_coderag",
]
