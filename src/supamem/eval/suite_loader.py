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
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from supamem.eval.datasets.longmemeval_loader import (
    build_smoke_subset,
    load_longmemeval,
    resolve_cache_dir,
)

# Phase 15 Plan A Task A2 — entry-point dispatch for the new ``supamem.eval``
# plugin group. Names registered here resolve to suite *classes* (e.g.
# ``CodeRAGSuite``), not flat lists of records. ``load_suite`` is overloaded:
# names that match a registered entry-point return the loaded class; names
# matching the legacy bundled-fixture set still return ``list[dict]``.
_EVAL_ENTRY_POINT_GROUP = "supamem.eval"

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


def _entry_point_suite(name: str) -> Any | None:
    """Resolve ``name`` against the ``supamem.eval`` entry-point group.

    Returns the loaded class on hit, ``None`` on miss. Never raises on
    discovery — entry-point loading errors propagate (consistent with the
    other ``supamem.*`` plugin groups).
    """
    for ep in entry_points(group=_EVAL_ENTRY_POINT_GROUP):
        if ep.name == name:
            return ep.load()
    return None


def load_suite(name: str) -> Any:
    """Resolve a suite name.

    Two dispatch surfaces are supported:

    1. ``supamem.eval`` entry-point group (Phase 15 Plan A) — returns the
       loaded suite *class* (e.g. ``CodeRAGSuite``). Third-party packages
       can register additional suites here without forking supamem.
    2. Legacy bundled-fixture names (Phase 14) — returns
       ``list[dict[str, Any]]`` of normalized question records.

    Raises ``ValueError`` for unknown suite names.
    """
    suite_cls = _entry_point_suite(name)
    if suite_cls is not None:
        return suite_cls
    if name == "longmemeval_scoped_smoke":
        return _load_longmemeval_scoped_smoke()
    raise ValueError(f"unknown suite: {name!r}")


def list_suites() -> list[str]:
    """Enumerate every registered suite — entry-point + legacy fixture names.

    The order is deterministic: entry-point names sorted alphabetically,
    then legacy bundled-fixture names appended.
    """
    ep_names = sorted({ep.name for ep in entry_points(group=_EVAL_ENTRY_POINT_GROUP)})
    legacy = ["longmemeval_scoped_smoke"]
    return ep_names + [n for n in legacy if n not in ep_names]


__all__ = [
    "build_smoke_subset",
    "list_suites",
    "load_longmemeval",
    "load_suite",
    "resolve_cache_dir",
]
