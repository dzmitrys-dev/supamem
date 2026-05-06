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
_VALID_SUITES: frozenset[str] = frozenset({"goldens", "longmemeval_s", "coderag"})


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


class _CoderagSmokeHit:
    """Inline-haystack hit shape — duck-types ``runner._build_run`` access."""

    __slots__ = ("payload", "score")

    def __init__(self, doc_id: str, score: float) -> None:
        self.score = score
        self.payload = {"doc_id": doc_id}


class _CoderagSmokeBackend:
    """Offline backend for the bundled ``coderag_smoke.json`` fixture.

    Mirrors the ``_SmokeBackend`` used by ``tests/test_coderag_invariants.py``
    (deliberately duplicated rather than imported from the test tree —
    src/ must never import from tests/). Each question carries an inline
    haystack of (path, content) stubs sufficient to exercise the full
    scoring path without Qdrant or a live corpus walk. The matching
    repo's gold docs sort to the top of the result list so the smoke
    invocation produces deterministic, non-trivial metrics.

    Plan 15-D D2: this backend powers the default
    ``supamem eval --suite coderag`` invocation; ``--full`` continues to
    route through the live Qdrant-backed ``_build_backend``.
    """

    def __init__(self, smoke: dict[str, Any]) -> None:
        self._by_text: dict[str, dict[str, Any]] = {
            q["text"]: q for q in smoke.get("questions", [])
        }

    def query(
        self, text: str, k: int = 20, *, where: dict[str, Any] | None = None
    ) -> list[_CoderagSmokeHit]:
        q = self._by_text.get(text)
        if q is None:
            return []
        repo = q["repo"]
        gold = list(q["gold"])
        haystack_docs = [h["path"] for h in q.get("haystack", [])]
        non_gold = [d for d in haystack_docs if d not in gold]
        if where is None:
            ranked = list(gold) + non_gold
        else:
            asked = where.get("repo")
            if asked is None or asked == [repo]:
                ranked = list(gold) + non_gold
            else:
                ranked = []
        return [
            _CoderagSmokeHit(doc_id, score=float(len(ranked) - i))
            for i, doc_id in enumerate(ranked[:k])
        ]


def _build_backend(
    config: ResolvedConfig, *, suite: str | None = None
) -> TunedHybridBackend:
    """Build the retrieval backend, optionally swapping ``cfg.collection``
    to the isolated bench prefix for the longmemeval_s suite.

    Phase 14 Plan A Task A3 (D-SCOPE-05): when ``suite='longmemeval_s'``,
    we shallow-copy ``config`` with ``collection`` overridden to
    :func:`supamem.eval.longmemeval_ingest.eval_collection_name(cfg, suite)`
    so retrieval queries hit the eval collection, NOT the user's
    production collection. The caller's ``config`` is NEVER mutated.

    For ``suite=None`` (default — preserves the byte-identical
    ``_run_goldens_legacy`` call site that reads ``_build_backend(cfg)``)
    and ``suite='goldens'``, behavior is unchanged: the backend is
    constructed against the caller's original cfg.
    """
    if suite == "longmemeval_s":
        # Lazy import — avoids loading the ingest module on the goldens path.
        from dataclasses import replace as _replace  # noqa: PLC0415

        from supamem.eval.longmemeval_ingest import (  # noqa: PLC0415
            eval_collection_name,
        )

        bench_cfg = _replace(config, collection=eval_collection_name(config, suite))
        return TunedHybridBackend(config=bench_cfg)
    if suite == "coderag":
        # Phase 15 Plan A Task A2 — parallel branch for the coderag suite.
        # Lazy import: keeps the longmemeval / goldens paths free of pytrec_eval
        # / mem0 import cost, and keeps coderag/ingest off the goldens hot path.
        from dataclasses import replace as _replace  # noqa: PLC0415

        from supamem.eval.coderag.ingest import (  # noqa: PLC0415
            coderag_collection_name,
        )

        bench_cfg = _replace(config, collection=coderag_collection_name())
        return TunedHybridBackend(config=bench_cfg)
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
    """Group per-record metric rows by axis and average each metric.

    Legacy flat shape: ``per_record[i]["metrics"]`` is a flat dict of 9
    metrics. Returns ``{<axis>: {<metric>: avg, ...}}``. Retained for
    callers that pre-date Phase 14 Plan B.
    """
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


def _aggregate_by_axis_per_pass(
    per_record: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, float]]]:
    """Group per-record nested metrics by pass then by axis (Phase 14 Plan B).

    Per RESEARCH §Q1 risk #7 the legacy ``_aggregate_by_axis`` is rewritten
    rather than mutated — the legacy function stays put (no caller other
    than the longmemeval path), this v2 walks the nested
    ``metrics: {"unscoped": {...}, "scoped": {...}}`` shape and emits
    sibling top-level keys.

    Output shape::

        {
            "unscoped": {<axis>: {<metric>: avg, ...}, ...},
            "scoped":   {<axis>: {<metric>: avg, ...}, ...},
        }

    Sub-passes whose per-record value is ``None`` (e.g. scoped pass on a
    record with empty sessions) are silently dropped from the average so
    the scoped-axis number still reflects only records where it ran.
    """
    from supamem.eval.report import REPORT_METRIC_NAMES

    passes: tuple[str, ...] = ("unscoped", "scoped")
    buckets: dict[str, dict[str, dict[str, list[float]]]] = {
        p: {} for p in passes
    }

    for row in per_record:
        axis = row.get("axis") or "unknown"
        metrics = row.get("metrics") or {}
        if not isinstance(metrics, dict):
            continue
        for pname in passes:
            sub = metrics.get(pname)
            if not isinstance(sub, dict):
                continue
            bucket = buckets[pname].setdefault(
                axis, {name: [] for name in REPORT_METRIC_NAMES}
            )
            for mname, mval in sub.items():
                if mname in bucket and mval is not None:
                    try:
                        bucket[mname].append(float(mval))
                    except (TypeError, ValueError):
                        continue

    out: dict[str, dict[str, dict[str, float]]] = {p: {} for p in passes}
    for pname in passes:
        for axis, mvals in buckets[pname].items():
            out[pname][axis] = {
                mname: (sum(vs) / len(vs)) if vs else 0.0
                for mname, vs in mvals.items()
            }
    return out


def _record_metrics(
    question: str,
    answer: str,
    chunks: list[RetrievedChunk],
    judge: Any,
) -> dict[str, float | None]:
    """Compute the 9 REPORT_METRIC_NAMES for one pass's chunk list.

    Extracted from the per-record loop in ``_run_longmemeval`` so the
    unscoped and scoped passes share the metric-construction code path
    verbatim (Phase 14 Plan B Task B1 — refactor target per RESEARCH §Q1
    minimum-diff sketch). Body is lifted byte-for-byte from the legacy
    inline construction (pre-Phase-14 :436-486) to avoid behavior drift
    on the unscoped pass.
    """
    ctx_texts = [c.text or "" for c in chunks[:5]]
    in_tokens = _estimate_tokens(question) + sum(
        _estimate_tokens(t) for t in ctx_texts
    )
    ctx_tokens = sum(_estimate_tokens(t) for t in ctx_texts)
    recall = _heuristic_recall_at_5(chunks, answer)
    ar_result = judge.score_answer_relevance(
        question=question, answer=answer, contexts=ctx_texts
    )
    return {
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


def _none_metrics() -> dict[str, float | None]:
    """Return a dict carrying exactly the 9 REPORT_METRIC_NAMES, all None.

    Used when the scoped pass is skipped for a record (empty sessions) —
    the per-record envelope must still carry the 9-name shape so the
    aggregator's bucket walk does not implicitly mirror unscoped values.
    """
    from supamem.eval.report import REPORT_METRIC_NAMES

    return {name: None for name in REPORT_METRIC_NAMES}


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
        # Phase 14 Plan A Task A3: pass suite='longmemeval_s' so
        # _build_backend swaps cfg.collection to the isolated bench
        # prefix (D-SCOPE-05). Caller's cfg is unchanged.
        backend = _build_backend(cfg, suite="longmemeval_s")
    except Exception as exc:  # noqa: BLE001
        err_console.print(
            f"[supamem.warn]supamem: backend init failed "
            f"({type(exc).__name__}: {exc}); per-record retrieval will yield "
            "empty results.[/supamem.warn]"
        )

    # Phase 14 Plan B Task B1 (D-FUT24-01): the SCOPED pass MUST run with
    # rerank disabled even if the user invoked the runner with rerank-on.
    # Confounding scoping with rerank composition is exactly what
    # FUTURE-24 is for; Phase 14 strictly isolates from it.
    #
    # Implementation: lazy-built sibling backend whose cfg has
    # ``reranker_name="off"``. Cached for the lifetime of this
    # _run_longmemeval invocation so we don't pay re-init cost per record.
    # When the unscoped backend already runs with reranker off, we reuse
    # the same instance — building twice would just duplicate the embedder
    # warm-up.
    scoped_backend: TunedHybridBackend | None = None
    if backend is not None:
        unscoped_reranker = getattr(cfg, "reranker_name", "off")
        if unscoped_reranker == "off":
            scoped_backend = backend
        else:
            try:
                from dataclasses import replace as _replace  # noqa: PLC0415

                scoped_cfg = _replace(cfg, reranker_name="off")
                scoped_backend = _build_backend(
                    scoped_cfg, suite="longmemeval_s"
                )
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]supamem: scoped-backend init failed "
                    f"({type(exc).__name__}: {exc}); scoped pass will yield "
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
        sessions = rec.get("sessions") or []

        # Step 4 (Phase 14 Plan B Task B1) — DUAL pass at the single call
        # site. Both passes share the same loop iteration; smoke vs full
        # is decided BY the smoke_ids filter above, not by a second
        # physical call site (RESEARCH §Q1).

        # ── Unscoped pass: existing behavior, no `where`. ─────────────
        chunks_unscoped: list[RetrievedChunk] = []
        t0 = time.perf_counter()
        if backend is not None and question:
            try:
                chunks_unscoped = backend.query(question, k=5)
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]unscoped query for {rid!r} failed: "
                    f"{type(exc).__name__}[/supamem.warn]"
                )
        latency_unscoped_ms = (time.perf_counter() - t0) * 1000.0

        # ── Scoped pass: where={"session_id": list(sessions)}. ────────
        # session_id is NOT a magic key — flows through Phase 11's
        # generic pass-through loop in retrieval/filters.py:120-132
        # (D-SCOPE-03 lock). When sessions is empty, skip the pass — the
        # per-record envelope still carries a 'scoped' sub-dict but with
        # all-None metrics so the aggregator doesn't silently mirror.
        chunks_scoped: list[RetrievedChunk] = []
        latency_scoped_ms: float | None = None
        scoped_metrics: dict[str, float | None] | None = None
        if sessions and scoped_backend is not None and question:
            scoped_filter = {"session_id": list(sessions)}
            t1 = time.perf_counter()
            try:
                chunks_scoped = scoped_backend.query(
                    question, k=5, where=scoped_filter
                )
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]scoped query for {rid!r} failed: "
                    f"{type(exc).__name__}[/supamem.warn]"
                )
            latency_scoped_ms = (time.perf_counter() - t1) * 1000.0
            scoped_metrics = _record_metrics(
                question, answer, chunks_scoped, judge
            )
        else:
            # Empty sessions or no scoped backend: emit a 9-name None dict
            # so the by_axis aggregator drops the record from scoped
            # averages instead of mirroring unscoped values.
            scoped_metrics = _none_metrics()

        unscoped_metrics = _record_metrics(
            question, answer, chunks_unscoped, judge
        )

        per_record.append(
            {
                "id": rid,
                "axis": axis,
                "latency_ms": {
                    "unscoped": latency_unscoped_ms,
                    "scoped": latency_scoped_ms,
                },
                "metrics": {
                    "unscoped": unscoped_metrics,
                    "scoped": scoped_metrics,
                },
            }
        )
        # RAGAS triad inputs are sourced from the unscoped pass —
        # Phase 14 keeps RAGAS scope unchanged (D-SCOPE-06: scoped pass
        # is additive; existing aggregates remain unscoped).
        ctx_texts = [c.text or "" for c in chunks_unscoped[:5]]
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

    # Phase 14 Plan B Task B1: per-pass aggregators walk the nested
    # per-record metrics dict. Each pass's scores sub-dict carries the
    # 9 REPORT_METRIC_NAMES exactly.
    def _mean_pass(pass_name: str, metric_name: str) -> float:
        vals: list[float] = []
        for r in per_record:
            sub = (r.get("metrics") or {}).get(pass_name)
            if not isinstance(sub, dict):
                continue
            v = sub.get(metric_name)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if not vals:
            return 0.0
        return float(sum(vals) / len(vals))

    def _pct_pass(pass_name: str, metric_name: str, pct: float) -> float:
        vals: list[float] = []
        for r in per_record:
            sub = (r.get("metrics") or {}).get(pass_name)
            if not isinstance(sub, dict):
                continue
            v = sub.get(metric_name)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        return _percentile(vals, pct)

    def _scores_for(pass_name: str) -> dict[str, Any]:
        """9-metric scores dict for one pass.

        Phase 14 D-FUT24-01: only the unscoped pass merges RAGAS triad
        values (RAGAS runs once over the unscoped contexts). The scoped
        pass surfaces None for triad metrics so we don't pretend a
        triad ran twice.
        """
        triad_vals = (
            {
                "context_precision": triad.get("context_precision"),
                "context_recall": triad.get("context_recall"),
                "answer_relevance": triad.get("answer_relevance"),
            }
            if pass_name == "unscoped"
            else {
                "context_precision": None,
                "context_recall": None,
                "answer_relevance": None,
            }
        )
        out = {
            "recall_at_5": _mean_pass(pass_name, "recall_at_5"),
            **triad_vals,
            "tokens_per_correct_answer": _mean_pass(
                pass_name, "tokens_per_correct_answer"
            ),
            "context_compression_ratio": _mean_pass(
                pass_name, "context_compression_ratio"
            ),
            "input_tokens_p50": _pct_pass(pass_name, "input_tokens_p50", 50.0),
            "input_tokens_p95": _pct_pass(pass_name, "input_tokens_p95", 95.0),
            "write_cost": _mean_pass(pass_name, "write_cost"),
        }
        assert set(out.keys()) == set(REPORT_METRIC_NAMES), (
            f"score key drift on pass {pass_name!r}: "
            f"{sorted(out)} vs {sorted(REPORT_METRIC_NAMES)}"
        )
        return out

    # Sibling-key envelope (Plan B): scores carries unscoped + scoped
    # sub-dicts; build_report (Task B2) emits the nested envelope shape.
    scores: dict[str, Any] = {
        "unscoped": _scores_for("unscoped"),
        "scoped": _scores_for("scoped"),
    }

    # Step 7 — by_axis rollup. Per-pass nested form (Plan B).
    by_axis = _aggregate_by_axis_per_pass(per_record)

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
    peer: str | None = None,
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

    if suite == "coderag":
        # Phase 15 Plan A: entry-point-driven dispatch. Plan 15-C wires the
        # real scoring path. The dispatch references ``_run_coderag`` BY
        # FUNCTION NAME (A-D-PLAN-01) — the import below is the canonical
        # carry-lock anchor that the dispatch test asserts on.
        from supamem.eval.coderag.runner import _run_coderag  # noqa: PLC0415, F401
        from supamem.eval.suite_loader import load_suite as _load_suite  # noqa: PLC0415

        suite_cls = _load_suite("coderag")
        cfg = config or ResolvedConfig()

        # Load smoke fixture records in EVERY invocation (Plan 15-D D2):
        # default ``supamem eval --suite coderag`` runs against the bundled
        # ``coderag_smoke.json`` fixture so CI / dev have a green path
        # without Qdrant. ``--full`` will additionally route through the
        # live backend (and, in 15-E, swap in the auto_queries pipeline
        # driven by the populated coderag_corpus_manifest.json).
        import json as _json  # noqa: PLC0415
        from importlib import resources as _resources  # noqa: PLC0415

        records: list[dict] = []
        smoke: dict = {"questions": []}
        try:
            smoke_blob = (
                _resources.files("supamem.eval.datasets")
                / "coderag_smoke.json"
            ).read_text(encoding="utf-8")
            smoke = _json.loads(smoke_blob)
            records = [
                {
                    "id": q["id"],
                    "axis": q["axis"],
                    "repo": q["repo"],
                    "text": q["text"],
                    "gold": list(q["gold"]),
                }
                for q in smoke.get("questions", [])
            ]
        except Exception as exc:  # noqa: BLE001
            err_console.print(
                f"[supamem.warn]coderag: smoke fixture load failed "
                f"({type(exc).__name__}: {exc}); proceeding with empty "
                f"records.[/supamem.warn]"
            )

        # Backend selection (Plan 15-D D2):
        #   --full  → live ``_build_backend`` against Qdrant + ingested
        #             ``supamem_eval_coderag`` collection (Plan 15-E will
        #             swap to the populated manifest's full corpus).
        #   default → offline ``_CoderagSmokeBackend`` driven by each
        #             question's inline haystack stubs. No Qdrant, no
        #             network — CI determinism (Plan 15-D D2 acceptance).
        if full:
            backend: Any = _build_backend(cfg, suite="coderag")
        else:
            backend = _CoderagSmokeBackend(smoke)

        # Optional ``--peer mem0`` adapter: emits a ``peers["mem0"]`` row
        # alongside the supamem column without replacing it. Lazy import
        # keeps the goldens / longmemeval paths free of mem0 cost. Best-
        # effort: a missing ``mem0ai`` extras install or an unreachable
        # peer Qdrant degrades to a warning + no peer row (never bumps
        # the dispatch's exit code).
        peers_kwarg: dict[str, Any] | None = None
        if peer == "mem0":
            try:
                from supamem.eval.coderag.peers.mem0_adapter import (  # noqa: PLC0415
                    Mem0PeerAdapter,
                )

                _adapter = Mem0PeerAdapter()
                # Plan 15-D scope locks the supamem column as primary; the
                # peer's metrics are a parallel ROW under ``envelope.peers``
                # (see runner.envelope_from_results). 15-E ADR formalizes
                # the peer-row scoring path; for now we forward the adapter
                # under a sentinel key so downstream consumers can wire
                # peer-side scoring without forcing a runner-signature shift.
                peers_kwarg = {"mem0": {"adapter": _adapter, "status": "ready"}}
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]coderag: --peer mem0 unavailable "
                    f"({type(exc).__name__}: {exc}); install peers-mem0 "
                    f"extras and start peer Qdrant to enable.[/supamem.warn]"
                )
        elif peer:
            err_console.print(
                f"[supamem.warn]coderag: --peer {peer!r} unknown; only "
                f"'mem0' is supported in 15-D.[/supamem.warn]"
            )

        if peers_kwarg is not None:
            envelope = suite_cls.run(records, backend, peers=peers_kwarg)
        else:
            envelope = suite_cls.run(records, backend)

        # Optional out-path: 15-C baseline ritual uses ``--out
        # .planning/.../15-BASELINE-{i}.json``. Best-effort — never bumps
        # the dispatch's exit code.
        if out is not None:
            try:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text(
                    json.dumps(envelope, indent=2, sort_keys=False),
                    encoding="utf-8",
                )
                console.print(f"supamem — coderag: envelope -> {out}")
            except Exception as exc:  # noqa: BLE001
                err_console.print(
                    f"[supamem.warn]coderag: envelope write to {out!r} failed: "
                    f"{type(exc).__name__}: {exc}[/supamem.warn]"
                )
        return 0

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
