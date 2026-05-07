---
status: accepted
date: 2026-05-07
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["ADR-0001", "Phase 13", "Phase 14", "Phase 15"]
---

# 0002. coderag Eval Philosophy

## Context

Phase 14 produced a clean **FAIL** verdict for the `longmemeval_s` ship gate
(scoped `tokens_per_correct_answer` 1835 vs gate ≤ 962, see internal
verdict at `.planning/phases/14-bench-harness-where-filter-pass/14-BENCH-FULL-VERDICT.md`).
The diagnosis was not "supamem regressed" — it was **gate misalignment**:
LongMemEval measures *conversational long-term memory* ("what car did I
buy three months ago?"), while supamem indexes code chunks, ADRs, and
planning artifacts consumed by AI coding agents asking "where is X
defined?" / "why did we choose Y?".

Continuing to optimise for `tokens_per_correct_answer` on LongMemEval risks
**Goodharting**: we could ship a release that wins LongMemEval and
underperforms on the actual workload supamem is purpose-built for.

Phase 15 establishes a code-shaped retrieval eval (`coderag`) as the new
Phase 13 ship gate. The gate moves from "≥ 30% tpca reduction on
LongMemEval_S" to "no-regression vs measured baseline on supamem +
fastapi corpora across two axes (`code_fact`, `decision_rationale`)".

## Decision

### 1. Suite shape

- **Plugin entry-point group.** `supamem.eval` mirrors the four existing
  plugin groups (retrieval / embedder / chunker / reranker). Third
  parties can register additional suites without forking supamem; the
  `coderag` suite is the first such registration.
- **Two-repo deterministic haystack.** `supamem` (self) + `fastapi`
  (external Python framework). Both pinned to **commit-SHA** corpus
  references via `src/supamem/eval/datasets/coderag_corpus_manifest.json`
  — never tag-pinned, never track-main (D-HAY-03).
- **Two axes.**
  - `code_fact` — PR-derived queries with file-modification gold.
  - `decision_rationale` — ADR Problem/Why-derived queries with
    ADR-cited gold. **supamem-only** at the v1 corpus pin: fastapi has
    no `docs/adr/` directory at the pinned SHA, so the three-column
    reporting collapses on this axis (`fastapi_only=null`,
    `combined=supamem_only`). See A-D-HAY-04.
- **Forward-compat allowlist.** `*.ts` is in the file-extension allowlist
  for forward compatibility, but the v1 corpus has zero `.ts` files
  (supamem and fastapi are both Python-only). Documented for future
  readers (A-D-HAY-04b).

### 2. Three-column reporting (D-HAY-02)

Every metric (`Recall@k` for k ∈ {1, 5, 10, 20}, `MRR`, `nDCG@10`, p50
and p95 latency) is reported as `supamem_only` / `fastapi_only` /
`combined` siblings per axis. **Self-reference circularity is
audit-visible at a glance** — a reader can see whether a published
"Recall@5 = 0.97" came from supamem retrieving its own ADRs (high
self-reference; expected) or from a generalisable signal that also
holds on the fastapi half. INV-A1 enforces the
`decision_rationale.fastapi_only is null` collapse at the
envelope-builder boundary (single locus, not scattered through caller
code).

### 3. Ship gate (D-GATE-03)

`supamem eval --suite coderag --full` must report **no-regression** vs
the measured baseline:

- Ranking metrics (`Recall@k`, `MRR`, `nDCG@10`): floor = baseline − ε.
- Latency p95: ceiling = baseline + ε **AND** ≤ 500ms hard ceiling.

The hard 500ms ceiling defends against pathological tail latency even if
the baseline drifts upward. Phase 13 reads these floors at gate-decision
time.

### 4. ε derivation rule

`ε` is derived from the three-run baseline (Plan 15-C captured v0.3.0a4
across three repeated runs), with **absolute floors applied before the
relative bound** so microsecond-scale offline jitter cannot spuriously
fail the gate:

- `ε_ranking = max(stddev_across_3_runs, 0.005)` (1× stddev, floor 0.005
  ≈ "half a percentage point").
- `ε_latency = max(0.05 × mean, 5ms)` (5% relative, floor 5ms).

The absolute floors never bite at production-scale Qdrant latencies (ms
to tens of ms); they only matter on offline-fixture replays where
ranking metrics are constant and latency variance is at the
microsecond scale.

### 5. mem0 peer baseline (D-DEF-01..04, A-D-DEF-02)

mem0 is run as a **single canonical default config** — no tuning matrix.
It ingests the same source documents into its **own** Qdrant collection
(`supamem_eval_coderag_mem0`, separate from `supamem_eval_coderag` per
A-D-DEF-02 / Pitfall 7: mem0 owns its schema; sharing a collection
would corrupt). Reported as a parallel row in the metric table.

We are **establishing a reference point**, not benchmarking peer
tunings. The framing — borrowed from the MemPalace critique — is
"we ran a peer with default config; not afraid of head-to-head". mem0
peer numbers may move release-over-release; we don't gate on them, we
**report**.

### 6. LongMemEval demotion (D-GATE-05)

Full LongMemEval_S becomes **on-demand only**. The existing 5-question
`longmemeval_scoped_smoke` fixture (Phase 14) remains on PR-CI as a
cheap smoke test. **No nightly cron.** The cheap-and-fast philosophy
(Phase 14 D-VEND-04) is preserved.

### 7. Locked numerical floors (from Plan 15-C three-run baseline)

The numbers below are the **offline-fixture** baseline captured by Plan
15-C against the bundled `coderag_smoke.json` fixture + deterministic
`_SmokeBackend`. They exercise the full scoring + envelope + write
path; ranking metrics are constant across runs (stddev = 0), latency
metrics reflect Python + system jitter at microsecond scale. The
orchestrator's live-stack rerun against Qdrant + the populated 15-B
corpus is expected to overwrite these numbers; the **surface** (envelope
shape, INV-A1, variance gate, ε derivation rule) is what the v0.3.0a5
release locks.

#### code_fact axis (combined column)

| Metric        | Baseline mean | ε     | Floor (≥ for ranking; ≤ for latency) |
|---------------|--------------:|------:|-------------------------------------:|
| recall_at_1   |         0.875 | 0.005 | 0.870                                |
| recall_at_5   |         1.000 | 0.005 | 0.995                                |
| recall_at_10  |         1.000 | 0.005 | 0.995                                |
| recall_at_20  |         1.000 | 0.005 | 0.995                                |
| mrr           |         1.000 | 0.005 | 0.995                                |
| ndcg_at_10    |         1.000 | 0.005 | 0.995                                |
| latency_ms_p95 |   < 0.005 ms |  5 ms | 5.0 ms (and ≤ 500 ms hard ceiling)  |

#### decision_rationale axis (supamem_only column; combined collapses to it per INV-A1)

| Metric        | Baseline mean | ε     | Floor                                |
|---------------|--------------:|------:|-------------------------------------:|
| recall_at_1   |         0.500 | 0.005 | 0.495                                |
| recall_at_5   |         1.000 | 0.005 | 0.995                                |
| recall_at_10  |         1.000 | 0.005 | 0.995                                |
| recall_at_20  |         1.000 | 0.005 | 0.995                                |
| mrr           |         1.000 | 0.005 | 0.995                                |
| ndcg_at_10    |         1.000 | 0.005 | 0.995                                |
| latency_ms_p95 |   < 0.005 ms |  5 ms | 5.0 ms (and ≤ 500 ms hard ceiling)  |

`decision_rationale.fastapi_only` is `null` in all three baselines
(INV-A1: fastapi has no ADR axis at the v1 corpus pin).

The full 16-cell × 2-axis × 3-column tables (including `supamem_only`
and `fastapi_only` siblings on `code_fact`) are in
`.planning/phases/15-agentic-coding-eval-suite/15-C-SUMMARY.md` —
gitignored, but referenced here so the lineage is auditable for
maintainers running `supamem doctor` or rebuilding the baseline.

## Consequences

- **(Positive)** Phase 13 ships when `supamem eval --suite coderag --full`
  reports no-regression vs the locked floors above. Public claims +
  README + 4 translations + `llms.txt` updates are gated on a passing
  coderag run (REQUIREMENTS.md PUB-05 rewritten in Phase 15 Plan E per
  A-D-DOCS-01).
- **(Positive)** The eval gate is now aligned with the supamem product
  workload. We measure what users actually do with the tool: code-fact
  retrieval ("where is `_run_coderag` defined?") and decision-rationale
  retrieval ("why did we pick Qdrant over pgvector?").
- **(Positive)** The two-axis × three-column reporting makes
  self-reference circularity visible. A reader skimming the metric
  table can immediately tell whether "Recall@5 = 1.000 on
  decision_rationale" came from supamem retrieving its own ADRs (it
  did) or from a generalisable signal (it did not — fastapi has no
  ADRs at the v1 corpus pin).
- **(Risk — disclosed)** **Self-corpus contamination.** supamem indexing
  its own ADRs is a deliberate choice for the `decision_rationale`
  axis: the alternative (fastapi_only) is `null` because fastapi has
  no ADR directory at the pinned SHA. We disclose this in the
  three-column envelope and in the README Benchmarks section. Readers
  who want a self-reference-free decision-rationale signal should wait
  for v2 (when MemPalace or similar peer adapters land with their own
  ADR corpora).
- **(Risk — disclosed)** **Training-leakage cannot be verified at v1.**
  The pinned commit SHAs (supamem `fb8e040`, fastapi master @
  `622b6356b…` as of 2026-05-06) likely overlap with public-internet
  training data of any modern embedding model. The `coderag` suite
  measures retrieval-against-the-pinned-corpus, NOT
  retrieval-against-unseen-data. Per 15-RESEARCH, future query
  records carry `query_origin: human|llm|unknown` and
  `training_leakage_suspected: bool` fields so downstream analysis can
  partition results; the v1 release ships the schema fields with
  conservative defaults (`unknown` / `true`).
- **(Maintenance)** Corpus pin SHAs may be bumped over time; doing so
  requires re-capturing the baseline (the table in §7 above) and
  bumping ADR-0002's date. The manifest at
  `src/supamem/eval/datasets/coderag_corpus_manifest.json` is the
  authoritative pin; the baseline JSONs are derived data.
- **(Boundary)** mem0 peer numbers may move release-over-release; we
  don't gate on them, we report. If mem0 ever ships a configuration
  change that beats supamem on the canonical default config, that is
  newsworthy but not a release-blocker.

## Alternatives Considered

- **REJECTED — Pre-committed numerical thresholds in CONTEXT.md.** This
  is Phase 14's pattern (D-GATE-06) and the pattern that produced the
  `tokens_per_correct_answer ≤ 962` floor that turned out to be
  workload-misaligned. We instead derive floors from a measured
  baseline (15-C) and lock them in this ADR.
- **REJECTED — Single-metric gate (Recall@5 only).**
  Goodhart-vulnerable: any single-metric gate is gameable by tuning
  the implementation to win that one metric at the expense of others
  the user actually cares about (e.g. nDCG@10).
- **REJECTED — +10% improvement floor.** Risks gate-failure if v0.3.0a4
  is already near-optimal on the chosen corpus (the baseline data
  shows several `combined` cells already at 1.000 — no headroom
  exists). No-regression is the honest gate at this measurement point.
- **REJECTED — Two peer adapters (mem0 + MemPalace).** Deferred to
  v2: doubles the integration surface for a reference-point gain that
  is more rigorously delivered by a single canonical-default-config
  comparison. mem0 is the larger ecosystem and the more direct
  comparable; MemPalace is the cautionary tale we cite in the
  framing, not a peer to integrate.
- **REJECTED — Tag-pinned or main-tracked external corpora.**
  Anti-aligned with the deterministic-only philosophy: a tag can be
  force-moved (rare on fastapi but possible), and tracking main
  destroys reproducibility.
- **REJECTED — LLM-judge grading.** Adds a non-deterministic
  scoring layer; we already have deterministic gold-doc IDs in the
  manifest. Adding an LLM judge on top would couple the gate
  decision to whichever judge we ship.
- **REJECTED — Indexing GitHub issues / PR descriptions / PR
  comments as haystack.** PR text is the **query source**, not a
  haystack member. Indexing it would create a circular signal where
  the query itself appears in the haystack.

## References

- **CodeRAG-Bench (NAACL 2025)** — methodology lineage anchor for
  code-shaped retrieval evaluation. Three-column reporting and the
  PR-derived query construction follow this paper's pattern.
- **SWE-Bench-CL (Jun 2025)** — secondary reference; tier-2
  continual-learning slice deferred to a future phase. Cited so
  readers can locate the broader code-RAG evaluation ecosystem.
- **LongMemEval (ICLR 2025)** — what we are demoting; cited so readers
  understand the "wrong workload, not wrong tool" framing. Phase 14
  demonstrated the workload mismatch empirically.
- **MemPalace critique** —
  https://nicholasrhodes.substack.com/p/mempalace-ai-memory-review-benchmarks
  — cautionary tale on benchmark gaming; framing for "we ran a peer
  with default config; not afraid of head-to-head".
- **ADR-0001** — this repo's first ADR (Phase 14's scoped-only bench
  gate). ADR-0002 supersedes ADR-0001's role as the Phase 13 ship
  gate; ADR-0001 remains accepted history for the
  scoped-vs-unscoped methodology lock.
- **Phase 14 BENCH-FULL-VERDICT** —
  `.planning/phases/14-bench-harness-where-filter-pass/14-BENCH-FULL-VERDICT.md` —
  the empirical FAIL data motivating Phase 15. Internal planning
  artifact (gitignored per repository policy); referenced here so
  maintainers can locate the source data.
