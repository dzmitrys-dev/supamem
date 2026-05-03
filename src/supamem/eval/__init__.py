"""Bench harness for supamem retrieval — recall@5 + p95 latency + token totals.

Public API:

- :func:`run_bench` — executes the bench against bundled or external goldens
- :func:`derive_required_substrings` — D-07-safe local extractor
- :func:`assert_no_saas_llm_env` — D-07 invariant guard
"""
from __future__ import annotations

from supamem.eval.auto_goldens import (
    assert_no_saas_llm_env,
    derive_required_substrings,
)
from supamem.eval.report import REPORT_METRIC_NAMES
from supamem.eval.runner import BASELINE, run_bench

__all__ = [
    "BASELINE",
    "REPORT_METRIC_NAMES",
    "assert_no_saas_llm_env",
    "derive_required_substrings",
    "run_bench",
]
