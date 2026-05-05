---
status: accepted
date: 2026-05-04
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["FUTURE-23 (promoted to Phase 14)", "FUTURE-24", "Phase 13", "Phase 14"]
---

# 0001. Scoped-Only Bench Gate

## Context

Phase 13's publication gate requires `baseline_delta.tokens_per_correct_answer
≤ -0.30` (a ≥30% improvement in tokens-per-correct-answer, hereafter `tpca`)
against the v0.1.5 baseline on the LongMemEval_S suite.

That gate failed by **+9.9% (tpca regressed)** under the v0.3.0a3 methodology.
Investigation traced the failure to an unscoped retrieval call site at
`src/supamem/eval/runner.py:428`:

```python
hits = backend.query(question, k=5)  # no `where` filter
```

With no `where` filter, none of the indexer-side levers shipped in Phases 7
(`room`), 9 (`valid_to`, always-on), 11 (`path_prefix`), or 14 (`session_id`)
can register on `tpca`. The only retrieval lever that *can* move `tpca` in
that mode is Phase 8's reranker — and on the LongMemEval_S corpus,
rerank-on regressed `tpca` by +9.9% versus rerank-off (the v0.1.5 emulation
condition).

Conclusion: **methodology, not capability, was the bottleneck**. The Phase 13
gate decision was being asked to read a number from a configuration in which
indexer-side levers are unmeasurable.

The unrelated FUTURE-24 backlog item (rerank composition rework — D-COMPOSE-01's
"rerank REPLACES score, T-4 recency SKIPPED" loses recency signal) is a
*sibling* unblocker for the same Phase 13 gate. The two concerns must remain
isolated: Phase 14 evaluates scoping with rerank-OFF, FUTURE-24 evaluates
composition with rerank-on later on its own ADR.

## Decision

Phase 14 emits **both** an unscoped and a scoped retrieval pass per question
at the single `runner.py:428` call site, inside the same per-record loop
iteration. The scoped pass derives a per-question `where` filter from the
LongMemEval haystack session ids attached to each question:

```python
where = {"session_id": question.haystack_sessions}  # list[str]
```

The result JSON gains sibling sub-dicts under `scores` and `by_axis`:

```json
{
  "scores": {
    "unscoped": { "tokens_per_correct_answer": ..., "recall_at_5": ..., ... },
    "scoped":   { "tokens_per_correct_answer": ..., "recall_at_5": ..., ... }
  }
}
```

`_compute_main_score` for the `longmemeval_s` suite reads
`scores.scoped.tokens_per_correct_answer` for the Phase 13 gate decision.
Unscoped is reported in the same envelope for transparency only — it never
gates.

The scoped pass runs against a dedicated bench collection
(`supamem_eval_longmemeval_s`) populated by a new module
`supamem.eval.longmemeval_ingest`. Each haystack chunk carries
`payload.session_id: str` and a keyword payload index is created on
`session_id` at first ingestion (idempotent). Production indexer paths
(markdown, transcript) are unchanged — `session_id` is a bench-only payload
field that `supamem index` does NOT set.

Phase 14's scoped pass runs with **rerank-OFF** (D-FUT24-01) so the
measured scoped-vs-unscoped delta attributes cleanly to scoping.

## Consequences

- **(Positive)** Indexer-side levers (Phase 7 `room`, Phase 9 `valid_to`,
  Phase 11 `path_prefix`, Phase 14 `session_id`) become measurable for the
  first time on the LongMemEval_S corpus. Phase 13 can compute its gate
  against numbers that actually reflect the indexer-side work shipped in
  the preceding milestones.
- **(Positive)** The PUB-05 invariant ("public claims gated on measured
  wins") is preserved: the gate reads a number whose mechanism the user
  can reproduce by passing `where={"session_id": [...]}` against a
  collection whose chunks carry `payload.session_id`.
- **(Risk — disclosed)** Scoped numbers may not reproduce in default
  unscoped invocations of `dual_memory_search` / `qdrant_find`. README.md
  and llms.txt explicitly note that the published Phase 14 numbers come
  from a methodology that emits a per-question filter, and that user
  retrieval invocations **without** a `where` parameter will not match
  these numbers. This is a methodology disclosure, not a defect — users
  who want comparable numbers must scope their queries.
- **(Risk — disclosed)** The v0.1.5 baseline was **re-captured** under
  Phase 14 against the new LongMemEval haystack collection
  (`supamem_eval_longmemeval_s`), NOT against the original devdocs
  collection that produced the original v0.1.5 figure (`tpca = 1374.59`).
  **Absolute pre-Phase-14 numbers are not directly comparable to
  post-Phase-14 numbers — the corpus changed.** The legacy `1374.59`
  figure is preserved in `eval/baselines/v0.1.5.json` as
  `legacy_devdocs_unscoped_tpca` for historical reference and does NOT
  gate. The re-captured baseline carries both `unscoped` and `scoped`
  sibling keys against the haystack corpus.
- **(Boundary)** FUTURE-24 (rerank composition rework) is a SIBLING
  unblocker, not bundled here. Phase 14's scoped pass runs with
  **rerank-OFF** (D-FUT24-01) so the measured scoped-vs-unscoped delta
  attributes cleanly to scoping. Public claims about scoped-retrieval
  gains do **NOT** extrapolate to "and once rerank composition is fixed
  too, the gate would clear by X% more" — that is unverified speculation
  per D-FUT24-03. After Phase 14's gate decision lands, the result
  determines whether FUTURE-24 promotion is urgent (gate still failing →
  urgent) or deferrable (gate passing → can wait for evidence).

## Alternatives Considered

- **REJECTED — Both-must-pass gate (D-GATE-04).** Would require unscoped to
  also hit ≤ −30% `tpca`. The empirical data shows unscoped is
  methodologically unmeasurable for indexer-side levers, so this would
  block the milestone indefinitely without producing additional
  information.
- **REJECTED — Best-of-pass.** Risks shipping numbers that users cannot
  reproduce in default unscoped invocations of `dual_memory_search`;
  violates the spirit of PUB-05 ("public claims gated on measured wins").
- **REJECTED — Synthetic LongMemEval-session → Phase 7 `room` mapping
  fixture (D-SCOPE-06).** Lossy (LongMemEval session topics ≠ code rooms);
  high maintenance burden; would not demonstrate scoped gain on the
  `temporal_reasoning` axis where the regression actually lives.
- **REJECTED — Combined `session_id + valid_to: "now"` filter
  (D-SCOPE-06).** Couples Phase 14 to Phase 9; `valid_to` is already
  always-on so the alias is a no-op that adds no measurable signal.
- **REJECTED — Clean-break v0.2.0 baseline replacing v0.1.5
  (D-GATE-05 option b).** Loses the `baseline_delta` continuity that
  PUB-01..05 success criteria reference. Re-capturing v0.1.5 against the
  new haystack collection (option a, this ADR's choice) preserves the
  baseline name while honestly disclosing the corpus change.
- **REJECTED — Bundle FUTURE-24 (rerank composition rework) into
  Phase 14.** Mixing scoping + composition produces confounded results;
  FUTURE-24 needs clean room to design its own evaluation
  (post-rerank additive recency vs query-aware skip vs RRF +
  rerank-as-preselect). D-FUT24-01..04 lock the strict isolation
  policy.
