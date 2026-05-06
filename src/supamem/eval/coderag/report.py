"""Three-column metric envelope for the coderag suite.

Per D-HAY-02: every metric carries ``supamem_only`` / ``fastapi_only`` /
``combined`` columns so readers can audit self-reference circularity at a
glance (the suite includes the supamem repo as part of its haystack).
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


__all__ = [
    "AXIS_NAMES",
    "COLUMN_NAMES",
    "METRIC_NAMES",
    "REPORT_SCHEMA_VERSION",
    "empty_envelope",
]
