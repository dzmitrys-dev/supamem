"""Phase 15 Plan C Task C3 — three-baseline variance check (INV-07).

Verifies that the three v0.3.0a4 baseline JSONs committed under
``.planning/phases/15-agentic-coding-eval-suite/`` exhibit per-metric
stddev within INV-07 bounds (≤10% of mean for ranking metrics; ≤20% for
latency).

ε derivation rule (locked in code; 15-E ADR will lock the numerical
floors as Phase 13 ship-gate baselines):

- ε_ranking = max(stddev_across_3_runs, 0.005)  — ~1× stddev
- ε_latency = max(0.05 * mean, 5ms)              — ~5% relative

The test SKIPs gracefully when fewer than 3 baseline JSONs are present
(e.g. agents executing the 15-C ritual on a worker without live Qdrant
will commit only the surface; the orchestrator's live-stack rerun
populates the JSONs and the test then runs in real CI).

Note on ``BASELINE_DIR``: ``.planning/`` is gitignored in supamem; the
orchestrator copies the JSONs into the phase dir on rescue from each
agent's worktree. We resolve the path relative to the test file so the
test is portable across worktrees.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = (
    REPO_ROOT / ".planning" / "phases" / "15-agentic-coding-eval-suite"
)
BASELINES = [BASELINE_DIR / f"15-BASELINE-{i}.json" for i in (1, 2, 3)]

RANKING_METRICS = (
    "recall_at_1", "recall_at_5", "recall_at_10", "recall_at_20",
    "mrr", "ndcg_at_10",
)
LATENCY_METRICS = ("latency_ms_p50", "latency_ms_p95")
RANKING_BOUND = 0.10
LATENCY_BOUND = 0.20


def _load_runs() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in BASELINES if p.exists()]


def test_three_baselines_committed() -> None:
    runs = _load_runs()
    if len(runs) < 3:
        pytest.skip(
            "Need 3 baseline JSONs at .planning/phases/15-agentic-coding-eval-suite/"
            f" — found {len(runs)}. The 15-C ritual + orchestrator rescue must"
            " complete before this gates."
        )
    assert len(runs) == 3


def test_baseline_variance_within_inv_07_bounds() -> None:
    runs = _load_runs()
    if len(runs) < 3:
        pytest.skip("Need 3 baseline runs (15-C ritual)")

    violations: list[str] = []
    for axis in ("code_fact", "decision_rationale"):
        for col in ("supamem_only", "combined"):  # fastapi_only optional / null
            vals_per_metric: dict[str, list[float]] = {}
            for r in runs:
                cell = r.get("scores", {}).get(axis, {}).get(col)
                if cell is None:
                    continue
                for m in RANKING_METRICS + LATENCY_METRICS:
                    v = cell.get(m)
                    if v is None:
                        continue
                    vals_per_metric.setdefault(m, []).append(float(v))

            for m, vals in vals_per_metric.items():
                if len(vals) < 3:
                    continue
                mu = mean(vals)
                if mu == 0:
                    continue  # degenerate — bound is undefined
                sd = stdev(vals)
                # ε derivation rule (locked in code):
                #   ε_ranking = max(stddev, 0.005)
                #   ε_latency = max(0.05 * mean, 5ms)
                # The variance gate compares stddev to ε. For latency, when
                # absolute stddev is below the 5ms floor it cannot violate
                # INV-07 regardless of relative ratio (microsecond-scale
                # jitter on offline runs would otherwise spuriously fail).
                if m in LATENCY_METRICS and sd < 5.0:
                    continue
                if m not in LATENCY_METRICS and sd < 0.005:
                    continue
                rel = sd / abs(mu)
                bound = LATENCY_BOUND if m in LATENCY_METRICS else RANKING_BOUND
                if rel > bound:
                    violations.append(
                        f"{axis}.{col}.{m}: stddev/mean={rel:.3f} > {bound} "
                        f"(stddev={sd:.4f}, mean={mu:.4f})"
                    )

    assert not violations, "INV-07 violated:\n  " + "\n  ".join(violations)


def test_baseline_envelopes_carry_schema_version() -> None:
    runs = _load_runs()
    if len(runs) < 3:
        pytest.skip("Need 3 baseline runs (15-C ritual)")
    for i, r in enumerate(runs, start=1):
        assert r.get("report_schema_version") == "coderag.v1", (
            f"15-BASELINE-{i}.json: missing/wrong report_schema_version "
            f"({r.get('report_schema_version')!r})"
        )
