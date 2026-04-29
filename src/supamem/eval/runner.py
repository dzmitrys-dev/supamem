"""Bench runner for ``supamem eval --regress``.

Loads a JSONL golden set (bundled or external), runs each query against
:class:`supamem.retrieval.tuned_hybrid.TunedHybridBackend`, computes
recall@5 via substring matching against each record's
``required_substrings`` list, and aggregates to mean recall + p95 latency
+ total tokens. ``--regress`` mode compares the aggregate to Phase 80.1
locked thresholds and exits non-zero on any breach (SC-9 regression gate).
"""
from __future__ import annotations

import json
import logging
import os
import time
from importlib import resources
from pathlib import Path
from typing import Any

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk

log = logging.getLogger("supamem.eval.runner")

# Phase 80.1 locked thresholds (D-19) — defaults; project-tunable since v0.1.2
# via [supamem.eval] baseline_* keys in .supamem/config.toml or env vars
# SUPAMEM_BASELINE_{RECALL_AT_5,TOTAL_TOKENS,P95_LATENCY_MS}.
BASELINE = {
    "mean_recall_at_5": 0.60,
    "total_tokens": 4000,
    "p95_latency_ms": 500,
}

BUNDLED_GOLDENS = "phase_80_1_tuned_hybrid.jsonl"


def _resolve_baseline(cfg: ResolvedConfig) -> dict[str, float]:
    """Merge BASELINE defaults ← config ← env-var overrides.

    Env vars (highest precedence): ``SUPAMEM_BASELINE_RECALL_AT_5``,
    ``SUPAMEM_BASELINE_TOTAL_TOKENS``, ``SUPAMEM_BASELINE_P95_LATENCY_MS``.
    Malformed values are logged and fall back to the config value.
    """
    out = {
        "mean_recall_at_5": float(cfg.regress_baseline_recall_at_5),
        "total_tokens": int(cfg.regress_baseline_total_tokens),
        "p95_latency_ms": float(cfg.regress_baseline_p95_latency_ms),
    }
    overrides = (
        ("SUPAMEM_BASELINE_RECALL_AT_5", "mean_recall_at_5", float),
        ("SUPAMEM_BASELINE_TOTAL_TOKENS", "total_tokens", int),
        ("SUPAMEM_BASELINE_P95_LATENCY_MS", "p95_latency_ms", float),
    )
    for env_var, key, caster in overrides:
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            continue
        try:
            out[key] = caster(raw)
        except ValueError:
            log.warning("supamem eval: ignoring malformed %s=%r", env_var, raw)
    return out


def _load_goldens(path: str | None) -> list[dict[str, Any]]:
    """Load JSONL records from ``path`` or the bundled corpus."""
    if path:
        body = Path(path).read_text(encoding="utf-8")
    else:
        # The goldens dir is a sub-package; resources.files works because
        # ``supamem.eval.goldens`` has its own __init__.py.
        files = resources.files("supamem.eval.goldens")
        target = files / BUNDLED_GOLDENS
        body = target.read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def _recall_at_5(retrieved: list[RetrievedChunk], required: list[str]) -> float:
    """Substring match: fraction of required substrings present in top-5 blob."""
    if not required:
        return 0.0
    blob = " ".join(c.text or "" for c in retrieved[:5])
    hits = sum(1 for s in required if s in blob)
    return hits / len(required)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return float(s[k])


def _build_backend(config: ResolvedConfig) -> TunedHybridBackend:
    return TunedHybridBackend(config=config)


def run_bench(
    *,
    regress: bool = False,
    goldens_path: str | None = None,
    config: ResolvedConfig | None = None,
) -> int:
    """Run the bench. Returns 0 on pass, 1 on regression / fatal."""
    cfg = config or ResolvedConfig()
    # CLI flag wins over config; both win over bundled goldens (path=None).
    resolved_goldens = goldens_path or (cfg.goldens_path or None)
    try:
        records = _load_goldens(resolved_goldens)
    except (FileNotFoundError, OSError) as exc:
        log.error("supamem eval: failed to load goldens: %s", exc)
        return 1
    if not records:
        log.warning("supamem eval: no golden records loaded")
        return 1

    backend = _build_backend(cfg)
    recalls: list[float] = []
    latencies: list[float] = []
    total_tokens = 0
    rows: list[dict[str, Any]] = []

    for rec in records:
        query = str(rec.get("query") or "").strip()
        required = list(rec.get("required_substrings") or [])
        if not query:
            continue
        t0 = time.perf_counter()
        try:
            chunks = backend.query(query, k=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("supamem eval: query %r failed: %s", query, type(exc).__name__)
            chunks = []
        elapsed = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed)
        recall = _recall_at_5(chunks, required)
        recalls.append(recall)
        total_tokens += sum(max(1, len(c.text or "") // 4) for c in chunks)
        rows.append({"id": rec.get("id"), "recall": recall, "latency_ms": elapsed})

    mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
    p95 = _percentile(latencies, 95.0)
    summary = {
        "queries": len(records),
        "mean_recall_at_5": round(mean_recall, 4),
        "p95_latency_ms": round(p95, 2),
        "total_tokens": total_tokens,
    }

    print("supamem eval — bench summary")
    print(f"  queries          : {summary['queries']}")
    print(f"  mean recall@5    : {summary['mean_recall_at_5']}")
    print(f"  p95 latency (ms) : {summary['p95_latency_ms']}")
    print(f"  total tokens     : {summary['total_tokens']}")

    if not regress:
        return 0

    baseline = _resolve_baseline(cfg)
    breaches: list[str] = []
    if mean_recall < baseline["mean_recall_at_5"]:
        breaches.append(
            f"mean_recall_at_5={mean_recall:.4f} < baseline {baseline['mean_recall_at_5']}"
        )
    if total_tokens > baseline["total_tokens"]:
        breaches.append(
            f"total_tokens={total_tokens} > baseline {baseline['total_tokens']}"
        )
    if p95 > baseline["p95_latency_ms"]:
        breaches.append(
            f"p95_latency_ms={p95:.2f} > baseline {baseline['p95_latency_ms']}"
        )

    if breaches:
        print()
        print("supamem eval — REGRESSION:")
        for line in breaches:
            print(f"  - {line}")
        return 1

    print()
    print("supamem eval — regress: PASS")
    return 0


__all__ = ["BASELINE", "run_bench"]
