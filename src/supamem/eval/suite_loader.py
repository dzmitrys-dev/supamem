"""Suite-level dataset loader entry points for the bench harness.

This module is the public import surface that
:mod:`tests.test_eval_dataset_loader` (Plan 10-01 RED) and the future
:func:`supamem.eval.runner.run_bench` dispatcher (Plan 10-02) consume.
Per-dataset loaders live under :mod:`supamem.eval.datasets`; this module
re-exports them so callers do not have to know which dataset module
implements which loader.

Re-exports:

- :func:`load_longmemeval` -- LongMemEval_S lazy-fetch loader
  (D-VEND-01..D-VEND-03).
- :func:`build_smoke_subset` -- D-SUBSET-01 axis-stratified sampler.
- :func:`resolve_cache_dir` -- per-revision cache prefix resolver.
"""
from __future__ import annotations

from supamem.eval.datasets.longmemeval_loader import (
    build_smoke_subset,
    load_longmemeval,
    resolve_cache_dir,
)

__all__ = [
    "build_smoke_subset",
    "load_longmemeval",
    "resolve_cache_dir",
]
