"""Bench runner — Phase 80.1 v0.1.x goldens path + Phase 10 suite dispatch.

Two execution paths share one public entrypoint:

1. Goldens (legacy v0.1.x): ``run_bench(suite="goldens")`` — bundled JSONL
   golden records, recall@5 substring matching, regression vs ``BASELINE``
   thresholds when ``regress=True``. Behavior is BYTE-IDENTICAL to v0.1.x
   for the regression path (D-VEND-04 regression-guarded).

2. LongMemEval_S (Phase 10): lazy-fetch (or ``dataset_path=`` per
   D-VEND-03) → smoke subset (D-SUBSET-01) → heuristic metrics + RAGAS
   adapter + judge → by-axis rollup → baseline delta → D-REPORT-01
   envelope written to ``~/.supamem/eval/<utc-iso>.json``.

D-07 invariant: ``assert_no_saas_llm_env()`` invoked at the runner entry
for the longmemeval path; ``dispatch_judge`` re-asserts internally.

Per CLAUDE.md hard constraint: NO bare ``print()`` — all stdout output
goes through :mod:`supamem.console`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from supamem.config import ResolvedConfig
from supamem.console import console, err_console
from supamem.eval.auto_goldens import assert_no_saas_llm_env
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

SuiteName = Literal["goldens", "longmemeval_s"]
_VALID_SUITES: frozenset[str] = frozenset({"goldens", "longmemeval_s"})


# v0.1.x goldens helpers ------------------------------------------------

def _resolve_baseline(cfg: ResolvedConfig) -> dict[str, float]:
    """Merge BASELINE defaults <- config <- env-var overrides.

    Env vars (highest precedence): SUPAMEM_BASELINE_RECALL_AT_5,
    SUPAMEM_BASELINE_TOTAL_TOKENS, SUPAMEM_BASELINE_P95_LATENCY_MS.
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
            log.warning("supamem: ignoring malformed %s=%r", env_var, raw)
    return out


def _load_goldens(path: str | None) -> list[dict[str, Any]]:
    """Load JSONL records from ``path`` or the bundled corpus."""
    if path:
        body = Path(path).read_text(encoding="utf-8")
    else:
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


def _run_goldens_legacy(
    *,
    regress: bool,
    goldens_path: str | None,
    config: ResolvedConfig | None,
) -> int:
    """v0.1.x goldens path — preserved BYTE-IDENTICAL behavior under
    ``regress=True``. Bare ``print`` calls are replaced with
    ``console.print`` per CLAUDE.md hard constraint, but the visible text
    + stdout ordering is unchanged so existing CI scrapers keep working.
    """
    cfg = config or ResolvedConfig()
    resolved_goldens = goldens_path or (cfg.goldens_path or None)
    try:
        records = _load_goldens(resolved_goldens)
    except (FileNotFoundError, OSError) as exc:
        log.error("supamem: failed to load goldens: %s", exc)
        return 1
    if not records:
        log.warning("supamem: no golden records loaded")
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
            log.warning("supamem: query %r failed: %s", query, type(exc).__name__)
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

    console.print("supamem — bench summary")
    console.print(f"  queries          : {summary['queries']}")
    console.print(f"  mean recall@5    : {summary['mean_recall_at_5']}")
    console.print(f"  p95 latency (ms) : {summary['p95_latency_ms']}")
    console.print(f"  total tokens     : {summary['total_tokens']}")

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
        console.print("")
        console.print("supamem — REGRESSION:")
        for line in breaches:
            console.print(f"  - {line}")
        return 1

    console.print("")
    console.print("supamem — regress: PASS")
    return 0


# Phase 10 longmemeval helpers -----------------------------------------


def _resolve_smoke_ids() -> set[str]:
    """Load the D-SUBSET-01 frozen smoke-subset IDs.

    Lookup order:
      1. tests/eval/smoke_ids.json walking up from this module (dev checkout).
      2. Wheel-bundled src/supamem/eval/datasets/smoke_ids.json.
    Returns empty set on every failure; caller falls back to full iteration.
    """
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tests" / "eval" / "smoke_ids.json"
        if candidate.exists():
            candidates.append(candidate)
            break
    bundled = here.parent / "datasets" / "smoke_ids.json"
    if bundled.exists():
        candidates.append(bundled)

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            err_console.print(
                f"[supamem.warn]smoke_ids.json unreadable at {path}: {exc}[/supamem.warn]"
            )
            continue
        ids = {entry["id"] for entry in data.get("ids", []) if isinstance(entry, dict)}
        if ids:
            return ids
    return set()


def _estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token estimate (no tiktoken in core)."""
    return max(1, len(text or "") // 4)


def _heuristic_recall_at_5(retrieved: list[RetrievedChunk], answer: str) -> float:
    """Substring-matching recall@5 against the canonical record's answer.

    Returns the fraction of 4+-char answer tokens that appear in the
    top-5 retrieved blob. Tokens shorter than 4 chars are skipped to
    avoid false positives on stop-words. When the answer has no scoring
    tokens, fall back to a single substring check.
    """
    if not answer:
        return 0.0
    blob = " ".join(c.text or "" for c in retrieved[:5])
    if not blob:
        return 0.0
    tokens = [t for t in answer.split() if len(t) >= 4]
    if not tokens:
        return 1.0 if answer in blob else 0.0
    hits = sum(1 for t in tokens if t in blob)
    return hits / len(tokens)


def _config_sha(cfg: ResolvedConfig) -> str:
    """Compute a stable digest of the resolved config.

    Uses ``json.dumps(..., sort_keys=False, default=str)`` so the
    config-discovery order (D-38 — project -> user -> defaults) survives
    into the digest. The python-hashing insight applies: when key order
    encodes precedence, sorting keys would decouple the digest from
    observable behavior. ``Path`` and other non-JSON-native values are
    stringified rather than raising.
    """
    import hashlib

    try:
        payload = asdict(cfg)
    except TypeError:
        payload = {"repr": repr(cfg)}
    body = json.dumps(payload, sort_keys=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _aggregate_by_axis(per_record: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Group per-record metric rows by axis and average each metric."""
    from supamem.eval.report import REPORT_METRIC_NAMES

    buckets: dict[str, dict[str, list[float]]] = {}
    for row in per_record:
        axis = row.get("axis") or "unknown"
        bucket = buckets.setdefault(axis, {name: [] for name in REPORT_METRIC_NAMES})
        for name, value in (row.get("metrics") or {}).items():
            if name in bucket and value is not None:
                try:
                    bucket[name].append(float(value))
                except (TypeError, ValueError):
                    continue

    out: dict[str, dict[str, float]] = {}
    for axis, mvals in buckets.items():
        out[axis] = {
            name: (sum(vs) / len(vs)) if vs else 0.0 for name, vs in mvals.items()
        }
    return out


def _run_longmemeval(
    *,
    full: bool,
    dataset_path: Path | str | None,
    judge_spec: str | None,
    baseline_version: str,
    out: Path | None,
    verbose: bool,
    config: ResolvedConfig | None,
) -> int:
    """Orchestrate the LongMemEval_S bench (10-step pipeline per Plan 10-04).

    Returns 0 on success, 1 on fatal (loader, judge dispatch, etc.).
    """
    # Step 1 — D-07 invariant guard at the runner boundary.
    try:
        assert_no_saas_llm_env()
    except RuntimeError as exc:
        err_console.print(f"[supamem.err]{exc}[/supamem.err]")
        return 1

    # Lazy imports — keep cold-start cheap for the goldens path.
    from supamem.eval.judge import dispatch_judge, resolve_judge_from_env
    from supamem.eval.ragas_adapter import compute_ragas_triad_with_reasons
    from supamem.eval.report import (
        REPORT_METRIC_NAMES,
        build_report,
        load_baseline,
        write_report,
    )
    from supamem.eval.suite_loader import load_longmemeval

    cfg = config or ResolvedConfig()

    # Step 5 — resolve judge. Precedence: explicit --judge spec > env var.
    if judge_spec:
        if judge_spec == "heuristic":
            judge = dispatch_judge(kind="heuristic")
        elif judge_spec.startswith("ollama:") or judge_spec.startswith("ollama://"):
            model = (
                judge_spec[len("ollama://"):]
                if judge_spec.startswith("ollama://")
                else judge_spec[len("ollama:"):]
            )
            prior = os.environ.get("EVAL_JUDGE_MODEL")
            os.environ["EVAL_JUDGE_MODEL"] = f"ollama:{model}"
            try:
                judge = resolve_judge_from_env()
            finally:
                if prior is None:
                    os.environ.pop("EVAL_JUDGE_MODEL", None)
                else:
                    os.environ["EVAL_JUDGE_MODEL"] = prior
        else:
            err_console.print(
                f"[supamem.err]supamem: unknown --judge spec {judge_spec!r}; "
                "expected 'heuristic' or 'ollama:<model>'.[/supamem.err]"
            )
            return 1
    else:
        judge = resolve_judge_from_env()

    # Step 2 — resolve subset.
    smoke_ids = set() if full else _resolve_smoke_ids()
    if not full and not smoke_ids:
        err_console.print(
            "[supamem.warn]smoke_ids.json missing or empty; falling back to full "
            "iteration. Pass --full explicitly to silence this warning."
            "[/supamem.warn]"
        )

    # Step 3 — iterate dataset records.
    try:
        records_iter = load_longmemeval(dataset_path=dataset_path)
    except Exception as exc:  # noqa: BLE001
        err_console.print(
            f"[supamem.err]supamem: dataset load failed: "
            f"{type(exc).__name__}: {exc}[/supamem.err]"
        )
        return 1

    backend: TunedHybridBackend | None = None
    try:
        backend = _build_backend(cfg)
    except Exception as exc:  # noqa: BLE001
        err_console.print(
            f"[supamem.warn]supamem: backend init failed "
            f"({type(exc).__name__}: {exc}); per-record retrieval will yield "
            "empty results.[/supamem.warn]"
        )

    per_record: list[dict[str, Any]] = []
    queries: list[str] = []
    retrieved_contexts: list[list[str]] = []
    answers: list[str] = []
    references: list[str] = []
    n_seen = 0

    for rec in records_iter:
        rid = rec.get("id")
        if smoke_ids and rid not in smoke_ids:
            continue
        n_seen += 1
        question = str(rec.get("question") or "").strip()
        answer = str(rec.get("answer") or "").strip()
        axis = rec.get("axis") or "unknown"

        chunks: list[RetrievedChunk] = []
        t0 = time.perf_counter()
        if backend is not None and question:
            try:
                chunks = backend.query(question, k=5)
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]query for {rid!r} failed: "
                    f"{type(exc).__name__}[/supamem.warn]"
                )
        latency_ms = (time.perf_counter() - t0) * 1000.0

        ctx_texts = [c.text or "" for c in chunks[:5]]
        in_tokens = _estimate_tokens(question) + sum(_estimate_tokens(t) for t in ctx_texts)
        ctx_tokens = sum(_estimate_tokens(t) for t in ctx_texts)
        recall = _heuristic_recall_at_5(chunks, answer)

        # Step 4 — heuristic in-process metrics. answer_relevance via judge.
        ar_result = judge.score_answer_relevance(
            question=question, answer=answer, contexts=ctx_texts
        )
        metrics: dict[str, float | None] = {
            "recall_at_5": recall,
            "context_precision": None,
            "context_recall": None,
            "answer_relevance": ar_result.value,
            "tokens_per_correct_answer": (
                in_tokens / recall if recall > 0 else float(in_tokens)
            ),
            "context_compression_ratio": ctx_tokens / max(1, _estimate_tokens(answer)),
            "input_tokens_p50": float(in_tokens),
            "input_tokens_p95": float(in_tokens),
            "write_cost": float(in_tokens + _estimate_tokens(answer)),
        }
        per_record.append(
            {
                "id": rid,
                "axis": axis,
                "latency_ms": latency_ms,
                "metrics": metrics,
            }
        )
        queries.append(question)
        retrieved_contexts.append(ctx_texts)
        answers.append(answer)
        references.append(answer)

    if n_seen == 0:
        err_console.print(
            "[supamem.warn]supamem: no records matched the configured subset; "
            "report envelope will still be written with zeroed scores.[/supamem.warn]"
        )

    # Step 6 — RAGAS triad (heuristic mode reports None with reasons).
    triad, _reasons = compute_ragas_triad_with_reasons(
        queries=queries,
        retrieved_contexts=retrieved_contexts,
        answers=answers,
        references=references,
        judge_kind=judge.kind,
    )

    def _mean(name: str) -> float:
        vals = [
            r["metrics"].get(name)
            for r in per_record
            if r["metrics"].get(name) is not None
        ]
        if not vals:
            return 0.0
        return float(sum(vals) / len(vals))

    def _pct(name: str, pct: float) -> float:
        vals = [
            float(r["metrics"].get(name) or 0.0)
            for r in per_record
            if r["metrics"].get(name) is not None
        ]
        return _percentile(vals, pct)

    scores: dict[str, Any] = {
        "recall_at_5": _mean("recall_at_5"),
        "context_precision": triad.get("context_precision"),
        "context_recall": triad.get("context_recall"),
        "answer_relevance": triad.get("answer_relevance"),
        "tokens_per_correct_answer": _mean("tokens_per_correct_answer"),
        "context_compression_ratio": _mean("context_compression_ratio"),
        "input_tokens_p50": _pct("input_tokens_p50", 50.0),
        "input_tokens_p95": _pct("input_tokens_p95", 95.0),
        "write_cost": _mean("write_cost"),
    }
    assert set(scores.keys()) == set(REPORT_METRIC_NAMES), (
        f"score key drift: {sorted(scores)} vs {sorted(REPORT_METRIC_NAMES)}"
    )

    # Step 7 — by_axis rollup.
    by_axis = _aggregate_by_axis(per_record)

    # Step 8 — load baseline + delta tolerated by build_report.
    baseline_data: dict[str, Any] | None = None
    try:
        baseline_data = load_baseline(baseline_version)
        if baseline_data.get("_baseline_pending"):
            err_console.print(
                f"[supamem.warn]baseline {baseline_version!r} is pending real "
                "capture (see Plan 10-06 release plan); delta numbers are "
                "placeholder.[/supamem.warn]"
            )
    except FileNotFoundError as exc:
        err_console.print(
            f"[supamem.warn]baseline {baseline_version!r} not found: {exc}; "
            "delta will be empty.[/supamem.warn]"
        )

    # Step 9 — build_report + write_report.
    from supamem.eval.datasets.longmemeval_meta import (
        DATASET_NAME,
        PINNED_REVISION,
    )

    dataset = {
        "name": DATASET_NAME,
        "revision": PINNED_REVISION,
        "n": n_seen,
        "subset_ids": sorted(smoke_ids) if smoke_ids else [],
    }
    judge_envelope = {"kind": judge.kind, "model": judge.model}

    envelope = build_report(
        suite="longmemeval_s",
        scores=scores,
        by_axis=by_axis,
        judge=judge_envelope,
        dataset=dataset,
        config_sha=_config_sha(cfg),
        collection=cfg.collection,
        baseline_data=baseline_data,
        per_question=per_record if verbose else None,
        verbose=verbose,
    )

    target_dir: Path | None = Path(out).parent if out is not None else None
    written = write_report(envelope, out_dir=target_dir)
    if out is not None:
        try:
            written.rename(Path(out))
            written = Path(out)
        except OSError as exc:
            err_console.print(
                f"[supamem.warn]could not rename to {out!r}: {exc}; "
                f"envelope at {written}[/supamem.warn]"
            )

    console.print(
        f"supamem — longmemeval_s: {n_seen} records, judge={judge.kind}, "
        f"main_score={envelope['main_score']:.2f}, report={written}"
    )
    return 0


# Public entrypoint ----------------------------------------------------


def run_bench(
    *,
    suite: str = "goldens",
    regress: bool = False,
    goldens_path: str | None = None,
    config: ResolvedConfig | None = None,
    judge: str | None = None,
    full: bool = False,
    dataset_path: Path | str | None = None,
    out: Path | None = None,
    verbose: bool = False,
    baseline_version: str = "v0.1.5",
) -> int:
    """Run the bench. Returns 0 on pass, 1 on regression / fatal.

    Parameters
    ----------
    suite
        ``"goldens"`` (v0.1.x bundled goldens) or ``"longmemeval_s"``
        (Phase 10 lazy-fetch). Unknown suites raise ``ValueError``.
    regress
        Goldens path only — when True, asserts run aggregates vs the
        v0.1.x BASELINE thresholds and exits non-zero on breach.
    goldens_path
        Goldens path only — override the bundled JSONL.
    config
        Optional pre-resolved config; defaults to fresh ResolvedConfig.
    judge
        Phase 10 — explicit judge spec (``"heuristic"`` or
        ``"ollama:<model>"``). Falls back to EVAL_JUDGE_MODEL env.
    full
        Phase 10 — when False (default), iterate only the D-SUBSET-01
        frozen smoke subset; True iterates the full dataset.
    dataset_path
        Phase 10 — D-VEND-03 air-gapped override. When set, skips the HF
        lazy-fetch entirely.
    out
        Phase 10 — explicit report output path. Default
        ``~/.supamem/eval/<utc-iso>.json`` (D-REPORT-01).
    verbose
        Phase 10 — include the per_question list in the envelope.
    baseline_version
        Phase 10 — which baseline JSON to diff against (default v0.1.5
        per D-BASE-01).
    """
    if suite not in _VALID_SUITES:
        raise ValueError(
            f"unknown suite {suite!r}; expected one of {sorted(_VALID_SUITES)}"
        )

    if suite == "goldens":
        rc = _run_goldens_legacy(
            regress=regress, goldens_path=goldens_path, config=config
        )
        # D-REPORT-02: emit an envelope for non-regress goldens runs so
        # the new --report json contract holds across both suites.
        # Best-effort — never bumps the goldens path's exit code.
        if rc == 0 and not regress:
            try:
                from supamem.eval.report import build_report, write_report

                cfg = config or ResolvedConfig()
                envelope = build_report(
                    suite="goldens",
                    scores={
                        "recall_at_5": 0.0,
                        "context_precision": None,
                        "context_recall": None,
                        "answer_relevance": None,
                        "tokens_per_correct_answer": 0.0,
                        "context_compression_ratio": 0.0,
                        "input_tokens_p50": 0.0,
                        "input_tokens_p95": 0.0,
                        "write_cost": 0.0,
                    },
                    judge={"kind": "heuristic", "model": "n/a"},
                    dataset={"name": "goldens", "revision": "bundled"},
                    config_sha=_config_sha(cfg),
                    collection=cfg.collection,
                )
                target_dir = Path(out).parent if out is not None else None
                write_report(envelope, out_dir=target_dir)
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]goldens envelope write failed: "
                    f"{type(exc).__name__}: {exc}[/supamem.warn]"
                )
        return rc

    # suite == "longmemeval_s"
    return _run_longmemeval(
        full=full,
        dataset_path=dataset_path,
        judge_spec=judge,
        baseline_version=baseline_version,
        out=out,
        verbose=verbose,
        config=config,
    )


__all__ = ["BASELINE", "run_bench"]
