"""Usage-counter rendering for ``supamem stats``.

Reads ``audit.jsonl`` written by :mod:`supamem.stats.counter`, filters by the
selected window, and aggregates count + tokens + p50/p95 latency per
``(kind, source)``. Returns either JSON or a plain-text table.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Literal

from supamem.stats.counter import DEFAULT_CACHE_DIR, bump

__all__ = ["bump", "render"]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return float(s[k])


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _filter_window(records: list[dict[str, Any]], show: str) -> list[dict[str, Any]]:
    today = _dt.date.today()
    if show == "today":
        cutoff = today.isoformat()
        return [r for r in records if str(r.get("ts", "")).startswith(cutoff)]
    if show == "week":
        start = today - _dt.timedelta(days=7)
        return [r for r in records if str(r.get("ts", ""))[:10] >= start.isoformat()]
    return records


def _aggregate(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    buckets: dict[str, dict[str, list[float]]] = {}
    for r in records:
        key = f"{r.get('kind','?')}:{r.get('source','?')}"
        slot = buckets.setdefault(key, {"tokens": [], "latency": []})
        slot["tokens"].append(float(r.get("tokens") or 0.0))
        slot["latency"].append(float(r.get("latency_ms") or 0.0))
    out: dict[str, dict[str, float]] = {}
    for key, slot in buckets.items():
        out[key] = {
            "count": len(slot["tokens"]),
            "tokens_total": sum(slot["tokens"]),
            "tokens_mean": (sum(slot["tokens"]) / len(slot["tokens"])) if slot["tokens"] else 0.0,
            "p50_latency_ms": _percentile(slot["latency"], 50.0),
            "p95_latency_ms": _percentile(slot["latency"], 95.0),
        }
    return out


def render(
    show: Literal["today", "week", "all"] = "today",
    format: Literal["json", "table"] = "table",
    *,
    cache_dir: Path | None = None,
) -> str:
    target = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    records = _filter_window(_read_jsonl(target / "audit.jsonl"), show)
    summary = _aggregate(records)

    if format == "json":
        return json.dumps(summary, indent=2, sort_keys=True)

    lines = [
        f"supamem stats — {show}",
        "",
        f"{'kind:source':<32} {'count':>8} {'tokens':>10} {'p50_ms':>10} {'p95_ms':>10}",
        "-" * 74,
    ]
    if not summary:
        lines.append("(no records)")
    else:
        for key in sorted(summary):
            row = summary[key]
            lines.append(
                f"{key:<32} {int(row['count']):>8} {int(row['tokens_total']):>10} "
                f"{row['p50_latency_ms']:>10.2f} {row['p95_latency_ms']:>10.2f}"
            )
    return "\n".join(lines)
