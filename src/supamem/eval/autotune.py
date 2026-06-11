"""Rule-based CodeRAG config autotune (Plan 18-I, Req-05).

EvolveMem-inspired closed loop without LLM diagnosis: observe → diagnose →
propose → gate → apply. Explicit CLI invoke only — no background daemon.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import tomllib
import tomli_w

from supamem.config import ResolvedConfig
from supamem.console import console, err_console
from supamem.eval.coderag.gate import load_floors, passes_no_regression_floor

_PREFETCH_CAP = 80
_PREFETCH_STEP = 10
_TOP_N_FLOOR = 20
_TOP_N_STEP = 10
_DEDUP_THRESHOLD_FLOOR = 0.90
_DEDUP_THRESHOLD_STEP = 0.02

_FIELD_TO_TOML: dict[str, tuple[str, str]] = {
    "reranker_prefetch_per_arm": ("reranker", "prefetch_per_arm"),
    "reranker_top_n": ("reranker", "top_n"),
    "adaptive_depth_enabled": ("retrieval.adaptive_depth", "enabled"),
    "adaptive_depth_delta": ("retrieval.adaptive_depth", "delta"),
    "adaptive_depth_k_max": ("retrieval.adaptive_depth", "k_max"),
    "dedup_enabled": ("retrieval.dedup", "enabled"),
    "dedup_cosine_threshold": ("retrieval.dedup", "cosine_threshold"),
}


@dataclass(frozen=True)
class ConfigDelta:
    """One proposed config mutation from the rule table."""

    field: str
    value: Any
    reason: str


@dataclass
class AutotuneResult:
    """Summary of an autotune invocation."""

    proposals: list[ConfigDelta]
    applied: bool
    exit_code: int


def _is_latency_metric(metric: str) -> bool:
    return metric.startswith("latency_")


def _envelope_value(envelope: dict[str, Any], path: str) -> float | None:
    axis, column, metric = path.split(".", 2)
    try:
        value = envelope["scores"][axis][column][metric]
    except (KeyError, TypeError):
        return None
    if value is None:
        return None
    return float(value)


def _ranking_below_floor(envelope: dict[str, Any], floors: dict[str, Any], path: str) -> bool:
    value = _envelope_value(envelope, path)
    if value is None:
        return False
    floor_val = floors.get("floors", {}).get(path)
    if floor_val is None:
        return False
    return value < float(floor_val)


def _ranking_at_or_above_floor(
    envelope: dict[str, Any], floors: dict[str, Any], path: str
) -> bool:
    value = _envelope_value(envelope, path)
    if value is None:
        return False
    floor_val = floors.get("floors", {}).get(path)
    if floor_val is None:
        return True
    return value >= float(floor_val)


def _latency_above_ceiling(envelope: dict[str, Any], floors: dict[str, Any], path: str) -> bool:
    value = _envelope_value(envelope, path)
    if value is None:
        return False
    ceiling = floors.get("floors", {}).get(path)
    if ceiling is None:
        return False
    return value > float(ceiling)


def _latency_has_headroom(envelope: dict[str, Any], floors: dict[str, Any], path: str) -> bool:
    value = _envelope_value(envelope, path)
    if value is None:
        return False
    ceiling = floors.get("floors", {}).get(path)
    if ceiling is None:
        return True
    return value <= float(ceiling)


def diagnose(
    envelope: dict[str, Any],
    floors: dict[str, Any],
    *,
    cfg: ResolvedConfig | None = None,
) -> list[ConfigDelta]:
    """Map CodeRAG floor breach patterns to config proposals (hand-written rules)."""
    resolved = cfg or ResolvedConfig()
    proposals: list[ConfigDelta] = []

    r1_path = "decision_rationale.supamem_only.recall_at_1"
    r5_path = "decision_rationale.supamem_only.recall_at_5"
    if _ranking_below_floor(envelope, floors, r1_path) and _ranking_at_or_above_floor(
        envelope, floors, r5_path
    ):
        current = int(resolved.reranker_prefetch_per_arm)
        proposals.append(
            ConfigDelta(
                field="reranker_prefetch_per_arm",
                value=min(current + _PREFETCH_STEP, _PREFETCH_CAP),
                reason=f"{r1_path} below floor while {r5_path} passes",
            )
        )

    ndcg_path = "code_fact.combined.ndcg_at_10"
    lat_path = "code_fact.combined.latency_ms_p95"
    if _ranking_below_floor(envelope, floors, ndcg_path) and _latency_has_headroom(
        envelope, floors, lat_path
    ):
        proposals.append(
            ConfigDelta(
                field="adaptive_depth_enabled",
                value=True,
                reason=f"{ndcg_path} below floor with latency headroom on {lat_path}",
            )
        )
        proposals.append(
            ConfigDelta(
                field="adaptive_depth_delta",
                value=0.25,
                reason=f"{ndcg_path} below floor with latency headroom on {lat_path}",
            )
        )

    if _latency_above_ceiling(envelope, floors, lat_path):
        current_top_n = int(resolved.reranker_top_n)
        proposals.append(
            ConfigDelta(
                field="reranker_top_n",
                value=max(current_top_n - _TOP_N_STEP, _TOP_N_FLOOR),
                reason=f"{lat_path} above latency ceiling",
            )
        )

    r20_path = "code_fact.combined.recall_at_20"
    r5_combined = "code_fact.combined.recall_at_5"
    if _ranking_below_floor(envelope, floors, r20_path) and _ranking_at_or_above_floor(
        envelope, floors, r5_combined
    ):
        if not resolved.dedup_enabled:
            proposals.append(
                ConfigDelta(
                    field="dedup_enabled",
                    value=True,
                    reason=f"{r20_path} below floor while {r5_combined} passes",
                )
            )
        else:
            new_threshold = max(
                _DEDUP_THRESHOLD_FLOOR,
                float(resolved.dedup_cosine_threshold) - _DEDUP_THRESHOLD_STEP,
            )
            proposals.append(
                ConfigDelta(
                    field="dedup_cosine_threshold",
                    value=new_threshold,
                    reason=f"{r20_path} below floor while dedup already enabled",
                )
            )

    return proposals


def apply_delta(cfg: ResolvedConfig, deltas: list[ConfigDelta]) -> ResolvedConfig:
    """Return a new :class:`ResolvedConfig` with *deltas* applied."""
    updates = {d.field: d.value for d in deltas}
    return replace(cfg, **updates)


def _set_nested_toml(table: dict[str, Any], table_path: str, key: str, value: Any) -> None:
    parts = table_path.split(".")
    node = table
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    leaf = parts[-1]
    bucket = node.setdefault(leaf, {})
    bucket[key] = value


def persist_config(
    cfg: ResolvedConfig,
    deltas: list[ConfigDelta],
    path: Path | None = None,
) -> None:
    """Merge *deltas* into the project ``.supamem/config.toml``."""
    target = path or (Path.cwd() / ".supamem" / "config.toml")
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.is_file():
        existing = tomllib.loads(target.read_text(encoding="utf-8"))
    supamem = existing.setdefault("supamem", {})
    delta_fields = {d.field for d in deltas}
    for field in delta_fields:
        mapping = _FIELD_TO_TOML.get(field)
        if mapping is None:
            continue
        table_path, key = mapping
        _set_nested_toml(supamem, table_path, key, getattr(cfg, field))
    target.write_text(tomli_w.dumps(existing), encoding="utf-8")


def _observe_bench(cfg: ResolvedConfig) -> dict[str, Any]:
    """Run a CodeRAG bench and return the envelope dict."""
    from supamem.eval.runner import run_bench

    offline = os.environ.get("SUPAMEM_AUTOTUNE_OFFLINE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        rc = run_bench(
            suite="coderag", config=cfg, full=not offline, out=out_path
        )
        if rc != 0:
            err_console.print(
                "[supamem.error]autotune: baseline bench failed "
                f"(exit {rc})[/supamem.error]"
            )
        body = out_path.read_text(encoding="utf-8")
        return json.loads(body)
    finally:
        out_path.unlink(missing_ok=True)


def run_autotune(
    cfg: ResolvedConfig,
    *,
    dry_run: bool = True,
    apply: bool = False,
) -> int:
    """Observe → diagnose → gate → optionally apply config deltas.

    Default ``dry_run=True`` performs zero config writes. ``apply=True``
    persists only when the gated re-bench passes
    :func:`~supamem.eval.coderag.gate.passes_no_regression_floor`.
    """
    floors = load_floors()
    baseline = _observe_bench(cfg)
    proposals = diagnose(baseline, floors, cfg=cfg)

    if not proposals:
        console.print("supamem — autotune: all floors pass; no proposals")
        return 0

    console.print(f"supamem — autotune: {len(proposals)} proposal(s)")
    for proposal in proposals:
        console.print(
            f"  - {proposal.field}={proposal.value!r} ({proposal.reason})"
        )

    if dry_run and not apply:
        console.print("supamem — autotune: dry-run (no config writes)")
        return 0

    candidate = apply_delta(cfg, proposals)
    trial = _observe_bench(candidate)
    ok, violations = passes_no_regression_floor(
        trial, floors, baseline=baseline
    )
    if not ok:
        err_console.print("[supamem.error]autotune: gate refused apply[/supamem.error]")
        for line in violations:
            err_console.print(f"  - {line}")
        return 1

    if apply:
        persist_config(candidate, proposals)
        console.print("supamem — autotune: applied gate-passing config deltas")
    else:
        console.print("supamem — autotune: gate passed (dry-run, not persisting)")

    return 0


__all__ = [
    "AutotuneResult",
    "ConfigDelta",
    "apply_delta",
    "diagnose",
    "persist_config",
    "run_autotune",
]
