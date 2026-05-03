"""Phase 10 Plan 10-01 — RED tests for the suite-dispatch contract.

These tests exercise the public surface that Plans 10-02..10-05 will land:
``run_bench(suite=...)`` plus a ``--dataset-path`` override (D-VEND-03) and a
defensive ValueError on unknown suites. The v0.1.x ``BASELINE`` constant is
checked verbatim as a regression guard so suite-dispatch work cannot silently
break the existing goldens path (D-VEND-04).

Every test in this module MUST FAIL today — the ``suite`` kwarg does not exist
on ``run_bench`` yet. They flip GREEN once Plan 10-02 lands suite dispatch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_smoke_ids_subset_shape() -> None:
    """D-SUBSET-01 frozen contract: 10 IDs, 5 axes, 2 per axis, seeded with 0."""
    here = Path(__file__).parent / "eval" / "smoke_ids.json"
    data = json.loads(here.read_text(encoding="utf-8"))
    assert data["seed"] == 0
    assert len(data["axes"]) == 5
    assert len(data["ids"]) == 10
    axes_seen = {entry["axis"] for entry in data["ids"]}
    assert len(axes_seen) == 5, f"expected 5 distinct axes, got {axes_seen!r}"
    # Exactly two questions per axis bucket.
    counts: dict[str, int] = {}
    for entry in data["ids"]:
        counts[entry["axis"]] = counts.get(entry["axis"], 0) + 1
    assert all(v == 2 for v in counts.values()), counts


def test_baseline_unchanged_regression_guard() -> None:
    """The v0.1.x ``BASELINE`` constant is part of the public eval API.

    Plan 10-02 extends ``run_bench`` with ``suite=...``; it MUST NOT touch
    the BASELINE shape. If this test fails, suite dispatch has regressed
    the existing goldens path.
    """
    from supamem.eval import BASELINE

    assert BASELINE == {
        "mean_recall_at_5": 0.60,
        "total_tokens": 4000,
        "p95_latency_ms": 500,
    }


def test_run_bench_suite_goldens_returns_zero() -> None:
    """``run_bench(suite='goldens')`` runs the bundled v0.1.x goldens path
    and returns exit code 0 (no regressions vs. baseline).

    RED today: ``run_bench`` does not accept ``suite`` yet."""
    from supamem.eval import run_bench

    rc = run_bench(suite="goldens")
    assert rc == 0


def test_run_bench_suite_longmemeval_accepts_dataset_path(tmp_path) -> None:
    """D-VEND-03: ``--dataset-path`` override skips HF fetch entirely.

    Plan 10-02 must accept ``dataset_path`` kwarg on ``run_bench`` for
    air-gapped CI mirrors. Today RED: the kwarg does not exist."""
    from supamem.eval import run_bench

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    # We do not assert exit code here — Plan 10-03 lands the actual loader.
    # We only assert the kwarg is accepted (no TypeError).
    run_bench(suite="longmemeval_s", dataset_path=str(fixture))


def test_run_bench_unknown_suite_raises_value_error() -> None:
    """``run_bench(suite='unknown')`` must raise ValueError with a clear
    message — defensive guard against typos and stale CI configs."""
    from supamem.eval import run_bench

    with pytest.raises(ValueError, match="unknown suite"):
        run_bench(suite="unknown")
