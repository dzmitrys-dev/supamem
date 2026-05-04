"""MTEB-style report envelope builder + writer — Phase 10 D-REPORT-01/02.

Builds the ``supamem eval`` JSON envelope. The shape is locked by Plan
10-01 RED tests against CONTEXT.md decisions:

- Top-level keys (exactly 10 in non-verbose mode):
  ``supamem_version, config_sha, collection, suite, dataset, judge,
  main_score, scores, by_axis, baseline``. ``per_question`` appears only
  when ``verbose=True``.
- ``scores`` carries exactly the 9 metric names from D-REPORT-01.
- ``main_score`` = ``scores['tokens_per_correct_answer']`` for
  ``longmemeval_s``, ``scores['recall_at_5']`` for ``goldens``
  (D-REPORT-02). Unknown suites raise ValueError.
- ``baseline.delta`` carries signed floats only for metrics present in
  BOTH the current run and the loaded baseline JSON; missing metrics are
  silently omitted (no KeyError).
- Writer filename: ``YYYY-MM-DDTHH-MM-SSZ.json`` (UTC, colons hyphenated
  for filesystem safety on Windows).
- ``supamem_version`` resolved via ``importlib.metadata.version`` with a
  ``"0.0.0+unknown"`` fallback for test environments that haven't run
  ``pip install -e .``.
- ``config_sha`` is caller-provided — the runner (Plan 10-04) computes
  it; this module does not invent one.

Per CLAUDE.md hard constraint: no bare ``print``. JSON write uses
``sort_keys=False`` so the envelope's documented top-level key order is
preserved on disk.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import metadata, resources
from pathlib import Path
from typing import Any

# D-REPORT-01: the 9 metric names. Tuple form is the immutable public
# contract; tests assert against this exact set.
REPORT_METRIC_NAMES: tuple[str, ...] = (
    "recall_at_5",
    "context_precision",
    "context_recall",
    "answer_relevance",
    "tokens_per_correct_answer",
    "context_compression_ratio",
    "input_tokens_p50",
    "input_tokens_p95",
    "write_cost",
)

# D-REPORT-02: per-suite main_score selector. Unknown suites raise.
_MAIN_SCORE_BY_SUITE: dict[str, str] = {
    "longmemeval_s": "tokens_per_correct_answer",
    "goldens": "recall_at_5",
}


def _resolve_supamem_version() -> str:
    """Return the installed package version, or ``0.0.0+unknown`` fallback.

    PackageNotFoundError fires when tests run against an un-installed
    source tree; the fallback keeps the envelope serialisable without
    forcing every test to ``pip install -e .`` first.
    """
    try:
        return metadata.version("supamem")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _compute_main_score(suite: str, scores: dict[str, Any]) -> float:
    """Pick the suite's headline metric per D-REPORT-02."""
    metric = _MAIN_SCORE_BY_SUITE.get(suite)
    if metric is None:
        raise ValueError(
            f"unknown suite {suite!r}; main_score is defined only for "
            f"{tuple(_MAIN_SCORE_BY_SUITE)}"
        )
    return float(scores.get(metric, 0.0))


def _compute_delta(
    scores: dict[str, Any], baseline_scores: dict[str, Any]
) -> dict[str, float]:
    """Per-metric signed-float delta, dropping metrics whose values are
    non-numeric in either side. Keeps the contract simple: delta carries
    floats only.
    """
    delta: dict[str, float] = {}
    for name, current in scores.items():
        if name not in baseline_scores:
            continue
        try:
            delta[name] = float(current) - float(baseline_scores[name])
        except (TypeError, ValueError):
            continue
    return delta


def _baseline_envelope(
    scores: dict[str, Any],
    baseline_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``baseline`` envelope sub-tree.

    Two shapes are accepted (D-GATE-03 backwards-compat + Phase 14 Plan A):

    - **Legacy:** baseline JSON carries top-level ``scores`` + ``by_axis``.
      The envelope returns ``{version, delta}`` where ``delta`` is the
      per-metric signed float against ``scores``.
    - **Migrated (Phase 14):** baseline JSON carries sibling
      ``unscoped: {scores, by_axis}`` and ``scoped: {scores, by_axis}``
      keys (and a legacy mirror at top level for migration safety). The
      envelope returns ``{version, delta, delta_unscoped, delta_scoped}``.
      Plan B's gate logic reads ``delta_scoped[<metric>]``; ``delta`` is
      a mirror of ``delta_unscoped`` for tooling that pre-dates the
      migration.

    Missing metrics are silently dropped — D-REPORT-01: "no KeyError when
    the baseline pre-dates a metric introduction".
    """
    if not baseline_data:
        return {"version": None, "delta": {}}

    version = baseline_data.get("version")

    # Migrated shape detection: presence of either sibling key triggers
    # the per-pass envelope. The legacy mirror at top-level is still
    # honored so old readers keep working.
    has_migrated = (
        isinstance(baseline_data.get("unscoped"), dict)
        or isinstance(baseline_data.get("scoped"), dict)
    )

    legacy_bscores = baseline_data.get("scores") or {}
    legacy_delta = _compute_delta(scores, legacy_bscores)

    out: dict[str, Any] = {"version": version, "delta": legacy_delta}

    if has_migrated:
        unscoped = baseline_data.get("unscoped") or {}
        scoped = baseline_data.get("scoped") or {}
        out["delta_unscoped"] = _compute_delta(scores, unscoped.get("scores") or {})
        out["delta_scoped"] = _compute_delta(scores, scoped.get("scores") or {})

    return out


def build_report(
    *,
    suite: str,
    scores: dict[str, Any],
    by_axis: dict[str, Any] | None = None,
    judge: dict[str, Any] | None = None,
    dataset: dict[str, Any] | None = None,
    config_sha: str = "",
    collection: str = "",
    supamem_version: str | None = None,
    baseline_data: dict[str, Any] | None = None,
    per_question: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build the MTEB-style envelope. See module docstring for contract.

    The non-verbose envelope has exactly the 10 top-level keys from
    D-REPORT-01. ``per_question`` is included ONLY when ``verbose=True``.

    All sub-dicts are caller-provided so the runner can assemble the
    envelope without this module knowing about retrieval / judge wiring.
    """
    envelope: dict[str, Any] = {
        "supamem_version": supamem_version or _resolve_supamem_version(),
        "config_sha": config_sha,
        "collection": collection,
        "suite": suite,
        "dataset": dataset or {},
        "judge": judge or {},
        "main_score": _compute_main_score(suite, scores),
        "scores": dict(scores),
        "by_axis": by_axis or {},
        "baseline": _baseline_envelope(scores, baseline_data),
    }
    if verbose:
        envelope["per_question"] = list(per_question or [])
    return envelope


def write_report(envelope: dict[str, Any], out_dir: Path | None = None) -> Path:
    """Write the envelope as JSON to ``<out_dir>/<utc-iso>.json``.

    Defaults to ``~/.supamem/eval/`` when ``out_dir`` is ``None``.
    Filename uses UTC strftime ``%Y-%m-%dT%H-%M-%SZ`` — colons are
    hyphenated for filesystem safety (Windows rejects colons in file
    names). JSON is written with ``indent=2, sort_keys=False`` so the
    documented top-level key order survives the round-trip.
    """
    target_dir = out_dir if out_dir is not None else Path.home() / ".supamem" / "eval"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    out_path = target_dir / f"{stamp}.json"
    out_path.write_text(
        json.dumps(envelope, indent=2, sort_keys=False, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def load_baseline(version: str = "v0.1.5") -> dict[str, Any]:
    """Load a baseline JSON shipped under ``supamem.eval.baselines``.

    Plan 10-04 lands the actual ``v0.1.5.json`` file once a real bench
    run produces canonical numbers. This loader is the contract callers
    rely on; raising ``FileNotFoundError`` on a missing version keeps
    fallback policy in the caller's hands.
    """
    files = resources.files("supamem.eval.baselines")
    target = files / f"{version}.json"
    try:
        body = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"supamem eval: baseline {version!r} not shipped; expected at "
            f"src/supamem/eval/baselines/{version}.json"
        ) from exc
    return json.loads(body)


__all__ = [
    "REPORT_METRIC_NAMES",
    "build_report",
    "load_baseline",
    "write_report",
]
