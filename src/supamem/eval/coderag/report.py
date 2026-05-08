"""Three-column metric envelope for the coderag suite.

Per D-HAY-02: every metric carries ``supamem_only`` / ``fastapi_only`` /
``combined`` columns so readers can audit self-reference circularity at a
glance (the suite includes the supamem repo as part of its haystack).

Plan 15-A shipped :data:`AXIS_NAMES` / :data:`COLUMN_NAMES` / :data:`METRIC_NAMES`
+ :func:`empty_envelope`. Plan 15-C adds:

- :data:`PYTREC_TO_ENVELOPE` — translate pytrec_eval metric names to envelope
  names (``recall_1`` → ``recall_at_1``, ``recip_rank`` → ``mrr``,
  ``ndcg_cut_10`` → ``ndcg_at_10``).
- :func:`column_metrics` — build one column-cell from one pytrec score dict
  + p50/p95 latency. ``None`` passes through (INV-A1 friendliness).
- :func:`envelope_from_results` — build the full envelope from per-axis
  per-column dicts. Enforces INV-A1 (decision_rationale.fastapi_only is None
  → combined collapses to supamem_only).
"""
from __future__ import annotations

REPORT_SCHEMA_VERSION = "coderag.v1"

AXIS_NAMES: tuple[str, ...] = ("code_fact", "decision_rationale")
COLUMN_NAMES: tuple[str, ...] = ("supamem_only", "fastapi_only", "combined")
METRIC_NAMES: tuple[str, ...] = (
    "recall_at_1",
    "recall_at_5",
    "recall_at_10",
    "recall_at_20",
    "mrr",
    "ndcg_at_10",
    "latency_ms_p50",
    "latency_ms_p95",
)

# Map pytrec_eval canonical names → envelope canonical names (Plan 15-C).
PYTREC_TO_ENVELOPE: dict[str, str] = {
    "recall_1": "recall_at_1",
    "recall_5": "recall_at_5",
    "recall_10": "recall_at_10",
    "recall_20": "recall_at_20",
    "recip_rank": "mrr",
    "ndcg_cut_10": "ndcg_at_10",
}


def empty_envelope() -> dict:
    """Empty three-column-axis envelope shape — Plan 15-A scope.

    Plan 16-D bumps the schema to carry ``comparisons: {}`` alongside the
    existing ``peers: {}`` — non-peer runs MUST emit both keys as empty dicts
    (D-PEER-03 stable schema).
    """
    empty_metrics = {m: None for m in METRIC_NAMES}
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scores": {
            axis: {col: dict(empty_metrics) for col in COLUMN_NAMES}
            for axis in AXIS_NAMES
        },
        "peers": {},
        "comparisons": {},
    }


def column_metrics(
    pytrec_scores: dict[str, float] | None,
    latency_p50: float | None,
    latency_p95: float | None,
) -> dict | None:
    """Translate one pytrec_eval result dict to envelope keys + add latencies.

    ``None`` in → ``None`` out (INV-A1: decision_rationale's fastapi_only
    column is null in every emitted JSON).
    """
    if pytrec_scores is None:
        return None
    out: dict = {
        envelope_key: pytrec_scores.get(pytrec_key, 0.0)
        for pytrec_key, envelope_key in PYTREC_TO_ENVELOPE.items()
    }
    out["latency_ms_p50"] = latency_p50
    out["latency_ms_p95"] = latency_p95
    return out


def envelope_from_results(
    per_axis_per_column: dict[str, dict[str, dict | None]],
    *,
    peers: dict[str, dict] | None = None,
    peer_run_data: dict[str, dict] | None = None,
) -> dict:
    """Build the full envelope from per-axis × per-column metric dicts.

    ``per_axis_per_column[axis][column]`` is one column-cell (the dict
    returned by :func:`column_metrics`) or ``None``.

    Enforces INV-A1: if ``decision_rationale.fastapi_only`` is ``None``,
    ``combined`` collapses to ``supamem_only`` regardless of what the caller
    passed for ``combined``. This is the canonical surface for the carry-lock
    contract (A-D-HAY-04 — fastapi has no ADR axis).

    Plan 16-D peer support (D-PEER-01..03)
    --------------------------------------
    When ``peer_run_data`` is supplied, the envelope also carries:

    - ``peers[peer]["scores"]`` — peer's per-axis × per-col × per-metric scores,
      mirror of the supamem ``scores`` nesting (D-PEER-01).
    - ``comparisons["{peer}_vs_supamem"][axis][col][metric]`` — paired-bootstrap
      delta + 95% CI + ``qualitative`` win/loss/tie label (D-PEER-02).

    ``peer_run_data`` shape (caller-built, 16-E live runs):

    .. code-block:: python

        {
            "mem0": {
                "scores": {<same nesting as envelope.scores for the peer>},
                "per_query_metrics": {
                    axis: {col: {metric: {q_id: float, ...}, ...}, ...}, ...
                },
                "supamem_per_query_metrics": {<same shape>},
            },
        }

    Caller (runner.py) MUST pair by ``query_id`` upstream — :func:`envelope_from_results`
    only intersects keys deterministically (sorted) before flattening to the
    paired arrays consumed by
    :func:`supamem.eval.coderag.metrics.paired_bootstrap_delta` (D-BOOT-02).

    Sign convention: ``mean(peer) - mean(supamem)`` so positive ``delta`` means
    peer is better (D-PEER-02).

    Qualitative derivation (mechanical):

    - ``"win"``  ⇔ ``ci_lower > 0``
    - ``"loss"`` ⇔ ``ci_upper < 0``
    - ``"tie"``  otherwise

    Backwards-compat: when neither ``peers`` nor ``peer_run_data`` is provided,
    both ``envelope["peers"]`` and ``envelope["comparisons"]`` are emitted as
    empty dicts (D-PEER-03 — keys present, never absent). The legacy ``peers``
    kwarg (15-C stub) is preserved; if both are passed, ``peer_run_data`` takes
    precedence.
    """
    scores: dict = {}
    for axis in AXIS_NAMES:
        cols = per_axis_per_column.get(axis, {})
        sup = cols.get("supamem_only")
        fap = cols.get("fastapi_only")
        comb = cols.get("combined")
        if axis == "decision_rationale" and fap is None:
            # INV-A1: fap is None → combined collapses to supamem_only
            comb = sup
        scores[axis] = {
            "supamem_only": sup,
            "fastapi_only": fap,
            "combined": comb,
        }

    peers_out: dict = {}
    comparisons_out: dict = {}
    if peer_run_data:
        # Local imports keep the non-peer code path free of numpy/metrics import
        # cost (matches D-BOOT-05 zero-cost discipline for non-peer runs).
        import numpy as _np  # noqa: PLC0415

        from supamem.eval.coderag.metrics import (  # noqa: PLC0415
            paired_bootstrap_delta,
        )

        for peer_name, peer_blob in peer_run_data.items():
            peers_out[peer_name] = {"scores": peer_blob["scores"]}
            comp_key = f"{peer_name}_vs_supamem"  # D-PEER-02 sign convention
            comp: dict = {}
            peer_pq = peer_blob["per_query_metrics"]
            sup_pq = peer_blob["supamem_per_query_metrics"]
            for axis, axis_blob in peer_pq.items():
                comp[axis] = {}
                for col, col_blob in axis_blob.items():
                    comp[axis][col] = {}
                    for metric, peer_qmap in col_blob.items():
                        sup_qmap = sup_pq.get(axis, {}).get(col, {}).get(metric, {})
                        # Pair by query_id deterministically (sorted intersection).
                        common_qids = sorted(set(peer_qmap) & set(sup_qmap))
                        if not common_qids:
                            continue
                        a = _np.array(
                            [peer_qmap[q] for q in common_qids], dtype=float
                        )
                        b = _np.array(
                            [sup_qmap[q] for q in common_qids], dtype=float
                        )
                        result = paired_bootstrap_delta(a, b)
                        # qualitative derivation per D-PEER-02
                        if result["ci_lower"] > 0:
                            qualitative = "win"
                        elif result["ci_upper"] < 0:
                            qualitative = "loss"
                        else:
                            qualitative = "tie"
                        comp[axis][col][metric] = {**result, "qualitative": qualitative}
            comparisons_out[comp_key] = comp
    elif peers:
        # Legacy 15-C stub: caller supplied a pre-built peers blob with no
        # per-query data — emit it verbatim, no comparisons derivable.
        peers_out = peers

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scores": scores,
        "peers": peers_out,
        "comparisons": comparisons_out,
    }


__all__ = [
    "AXIS_NAMES",
    "COLUMN_NAMES",
    "METRIC_NAMES",
    "PYTREC_TO_ENVELOPE",
    "REPORT_SCHEMA_VERSION",
    "column_metrics",
    "empty_envelope",
    "envelope_from_results",
]
