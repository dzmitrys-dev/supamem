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

New in Phase 14 Plan C, Task C2 (D-SMOKE-03 — new-suite path picked over
extending the existing ``smoke_ids.json`` 10-Q axis-stratified fast-path):

- :func:`load_suite` -- single-entry suite-name dispatch returning a list
  of normalized question records. The new ``longmemeval_scoped_smoke``
  suite resolves to the bundled
  ``src/supamem/eval/datasets/longmemeval_scoped_smoke.json`` fixture
  (committed by Task C1) without triggering the ~3 GB lazy-fetch
  (D-SMOKE-04 lock).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supamem.eval.datasets.longmemeval_loader import (
    build_smoke_subset,
    load_longmemeval,
    resolve_cache_dir,
)

# Bundled package-data path for the scoped-smoke fixture (D-SMOKE-01).
# We resolve via ``Path(__file__).parent`` — the same precedent used by
# ``runner._resolve_smoke_ids`` for the wheel-shipped ``smoke_ids.json``
# bundled fixture. Using ``importlib.resources`` would also work but adds
# a Traversable abstraction the rest of the eval/ package does not use.
_SCOPED_SMOKE_FIXTURE: Path = (
    Path(__file__).resolve().parent / "datasets" / "longmemeval_scoped_smoke.json"
)


def _load_longmemeval_scoped_smoke() -> list[dict[str, Any]]:
    """Read the bundled scoped-smoke fixture and return its question records.

    Returns the ``data["questions"]`` list verbatim — each record carries
    ``id, question, answer, axis, sessions`` (matching the canonical
    ``load_longmemeval`` shape) plus ``haystack``, ``expected_unscoped_tpca``,
    and ``expected_scoped_tpca`` for offline-only consumption by the
    Phase 14 CI smoke test (``tests/test_scoped_smoke_fixture.py``).

    NEVER triggers ``huggingface_hub.snapshot_download`` — the fixture is
    fully self-contained (D-SMOKE-04 lock).
    """
    if not _SCOPED_SMOKE_FIXTURE.exists():
        raise FileNotFoundError(
            f"bundled scoped-smoke fixture missing at {_SCOPED_SMOKE_FIXTURE}; "
            "wheel build may have excluded src/supamem/eval/datasets/*.json"
        )
    payload = json.loads(_SCOPED_SMOKE_FIXTURE.read_text(encoding="utf-8"))
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise ValueError(
            f"scoped-smoke fixture at {_SCOPED_SMOKE_FIXTURE} missing 'questions' list"
        )
    return list(questions)


def load_suite(name: str) -> list[dict[str, Any]]:
    """Resolve a suite name to a list of normalized question records.

    Currently supported names:

    - ``"longmemeval_scoped_smoke"`` — bundled self-contained fixture
      (Phase 14 Plan C). Each record additionally carries ``haystack``
      and ``expected_*_tpca`` fields for the offline CI smoke test.

    Other suite names (``"longmemeval_s"``, ``"goldens"``, ``"smoke_ids"``)
    are reserved for future expansion of this dispatch surface; today
    callers continue to use ``load_longmemeval`` directly for those paths.

    Raises ``ValueError`` for unknown suite names — defensive guard
    against typos and stale CI configs (mirrors the ``run_bench(suite=...)``
    contract from Plan 10-02).
    """
    if name == "longmemeval_scoped_smoke":
        return _load_longmemeval_scoped_smoke()
    raise ValueError(f"unknown suite: {name!r}")


__all__ = [
    "build_smoke_subset",
    "load_longmemeval",
    "load_suite",
    "resolve_cache_dir",
]
