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
    """Empty three-column-axis envelope shape — Plan 15-A scope."""
    empty_metrics = {m: None for m in METRIC_NAMES}
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scores": {
            axis: {col: dict(empty_metrics) for col in COLUMN_NAMES}
            for axis in AXIS_NAMES
        },
        "peers": {},
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
) -> dict:
    """Build the full envelope from per-axis × per-column metric dicts.

    ``per_axis_per_column[axis][column]`` is one column-cell (the dict
    returned by :func:`column_metrics`) or ``None``.

    Enforces INV-A1: if ``decision_rationale.fastapi_only`` is ``None``,
    ``combined`` collapses to ``supamem_only`` regardless of what the caller
    passed for ``combined``. This is the canonical surface for the carry-lock
    contract (A-D-HAY-04 — fastapi has no ADR axis).
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
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scores": scores,
        "peers": peers or {},
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
