"""CodeRAG no-regression floor gate (ADR-0002 §7, Plan 18-H).

Rule-based absolute-floor and baseline-relative checks on eval envelope
cells. No LLM diagnosis — autotune ``--apply`` uses this before persisting
config (Plan 18-I).
"""
from __future__ import annotations

import json
from importlib import resources
from typing import Any

_FLOORS_FILENAME = "coderag_floors.json"


def load_floors() -> dict[str, Any]:
    """Load structured floor constants shipped with the package."""
    files = resources.files("supamem.eval.baselines")
    raw = (files / _FLOORS_FILENAME).read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        msg = f"unsupported coderag_floors schema_version: {data.get('schema_version')!r}"
        raise ValueError(msg)
    return data


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


def _format_violation(
    path: str,
    candidate: float,
    *,
    bound: float,
    is_latency: bool,
    mode: str,
) -> str:
    if is_latency:
        if mode == "baseline":
            return f"{path}: {candidate:.4g} > baseline+ε {bound:.4g}"
        return f"{path}: {candidate:.4g} > ceiling {bound:.4g}"
    if mode == "baseline":
        return f"{path}: {candidate:.4g} < baseline-ε {bound:.4g}"
    return f"{path}: {candidate:.4g} < floor {bound:.4g}"


def passes_no_regression_floor(
    envelope: dict[str, Any],
    floors: dict[str, Any] | None = None,
    *,
    epsilon: float | None = None,
    baseline: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Return ``(ok, violations)`` for envelope against floors and optional baseline.

  When *baseline* is provided, each gated metric must satisfy
  ``candidate >= baseline - epsilon`` (ranking) or
  ``candidate <= baseline + epsilon`` (latency ceilings).

  When *baseline* is ``None``, compare against absolute floors from *floors*
  (ranking minimums and latency ceilings from ADR-0002 §7).
    """
    spec = floors if floors is not None else load_floors()
    eps = epsilon if epsilon is not None else float(spec.get("epsilon", 0.01))
    floor_map: dict[str, float] = spec.get("floors", {})
    violations: list[str] = []
    mode = "baseline" if baseline is not None else "absolute"

    for path, floor_value in floor_map.items():
        _axis, _column, metric = path.split(".", 2)
        candidate = _envelope_value(envelope, path)
        if candidate is None:
            continue

        is_latency = _is_latency_metric(metric)
        if baseline is not None:
            base_value = _envelope_value(baseline, path)
            if base_value is None:
                continue
            bound = base_value + eps if is_latency else base_value - eps
            failed = candidate > bound if is_latency else candidate < bound
        else:
            bound = float(floor_value)
            failed = candidate > bound if is_latency else candidate < bound

        if failed:
            violations.append(
                _format_violation(
                    path,
                    candidate,
                    bound=bound,
                    is_latency=is_latency,
                    mode=mode,
                )
            )

    return (len(violations) == 0, violations)
