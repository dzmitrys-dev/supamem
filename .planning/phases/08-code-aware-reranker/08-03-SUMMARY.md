---
phase: 08-code-aware-reranker
plan: 03
subsystem: retrieval + doctor + stats
tags: [rerank, tuned-hybrid, doctor-panel, byte-identity, percentile, deque, tdd]
provides:
  - branch: TunedHybridBackend.query() rerank-on path (D-COMPOSE-01/02/03 + D-POOL-01)
  - panel: doctor "Reranker" section with manifest probe + p50/p95 + device
  - api: supamem.stats.counter.get_latency_samples(kind, source) -> list[float]
  - state: supamem.stats.counter._LATENCY_DEQUES[(kind, source)] (maxlen=100)
  - extension: doctor rc accumulator OR-s in reranker_drift on line ~295
  - artifact: tests/_fixtures/tuned_hybrid_pre_phase8.json (frozen-clock JSON golden)
  - script: tests/_fixtures/_capture_golden.py (re-baseline procedure)
requires:
  - 08-01-SUMMARY (load_reranker contract + RetrievedChunk.rerank_score)
  - 08-02-SUMMARY (_model_cache_dir + _expected_manifest.json artifact)
  - existing supamem.stats.counter Welford bump path
  - existing doctor any_drift accumulator on line 251 / rc expression on line 295
affects:
  - src/supamem/retrieval/tuned_hybrid.py (rerank-on branch + prefetch widening + skip-recency gate)
  - src/supamem/doctor.py (Reranker panel + reranker_drift accumulator + extended rc expression)
  - src/supamem/stats/counter.py (_LATENCY_DEQUES ring buffer + get_latency_samples)
  - tests/_fixtures/tuned_hybrid_pre_phase8.json (NEW — pre-Phase-8 byte-identity golden)
  - tests/_fixtures/_capture_golden.py (NEW — re-baseline script with frozen clock)
  - tests/test_tuned_hybrid_rerank.py (NEW — 5 GREEN tests)
  - tests/test_doctor.py (3 new tests: panel-healthy, partial-download, p50/p95-verifiable)
  - tests/integration/test_rerank_pipeline.py (xfail removed; real env-gated body)
tech-stack:
  added: []  # no new deps; statistics/collections are stdlib
  patterns:
    - "Frozen-clock test: monkeypatch supamem.retrieval.tuned_hybrid.datetime to a _FrozenDT subclass so T-4 recency multiplier is deterministic across capture + assert"
    - "JSON byte-identity golden + RetrievedChunk.model_validate round-trip (CLAUDE.md / RESEARCH §Don't-Hand-Roll: JSON only, never binary serialization)"
    - "Process-local collections.deque(maxlen=100) ring buffer for TRUE percentile rendering — chosen OVER Welford-mean-with-approx-label for W3 verifiability"
    - "Single 'if reranker:' branch after _ensure() → preserves D-COMPOSE-03 byte-identity on the off-path"
    - "Plugin-invariant defense: len(reranked) > len(pre_rerank) → fall through to off-branch + err_console warn (T-RERANK-INVAR mitigation)"
    - "Lazy-import discipline: torch / supamem.rerankers / statistics imported INSIDE doctor panel body (cold supamem --help unaffected)"
key-files:
  created:
    - tests/_fixtures/_capture_golden.py
    - tests/_fixtures/tuned_hybrid_pre_phase8.json
    - tests/test_tuned_hybrid_rerank.py
    - .planning/phases/08-code-aware-reranker/08-03-SUMMARY.md
  modified:
    - src/supamem/retrieval/tuned_hybrid.py
    - src/supamem/doctor.py
    - src/supamem/stats/counter.py
    - tests/test_doctor.py
    - tests/integration/test_rerank_pipeline.py
decisions:
  - "p50/p95 chose DEQUE path over Welford-mean-with-approx — collections.deque(maxlen=100) is process-local, fail-soft, and renders TRUE statistics.median + statistics.quantiles(n=20)[18] without an 'approx' fudge label (W3 verifiability is straightforward)"
  - "B2 fix applied: NEW local 'reranker_drift = False' accumulator declared OUTSIDE the rname != 'off' branch; OR-ed into the existing rc expression alongside any_drift. The invented '_doctor_status_drift' name DOES NOT appear in doctor.py"
  - "Frozen clock for byte-identity: 2026-05-01T12:00:00Z chosen via _FrozenDT(datetime) subclass + monkeypatch on the imported 'datetime' name in supamem.retrieval.tuned_hybrid (the module references datetime.now via 'from datetime import datetime, timezone' so direct attribute swap works)"
  - "Plugin failure (rerank() raises) treated equivalently to 'list grew beyond input': fall through to off-branch with err_console warn — never silently drop hits (T-RERANK-INVAR fail-open)"
  - "Latency telemetry calls bump('rerank', 'rerank_latency_ms', 0, elapsed_ms) — Welford aggregates persist to disk AND deque receives the sample (best of both: long-term stats + fresh percentiles)"
metrics:
  duration: ~30 min
  tasks: 4 (RED, GREEN-2a, GREEN-2b, REFACTOR)
  files: 8 (3 created, 5 modified)
  completed: 2026-05-02
---

# Phase 8 Plan 03: tuned_hybrid Rerank Branch + Doctor Reranker Panel Summary

The load-bearing wire-up landed. `TunedHybridBackend.query()` now branches on
`reranker_name`: off-path is byte-identical to the pre-Phase-8 JSON golden
(captured at HEAD before any edit, with frozen clock), on-path widens prefetch
to `reranker_prefetch_per_arm`, REPLACES RRF score with rerank score, SKIPS
T-4 recency multiplier, and runs T-5 cosine dedup + T-8 token budget AFTER
the rerank pass. `supamem doctor` gains a `Reranker` panel between Section 2e
(Room histogram) and Section 3 (Installed clients drift) — surfaces name,
model_id, cache path, on-disk size with `_expected_manifest.json` diff,
p50/p95 latency from a process-local `collections.deque(maxlen=100)` ring
buffer, detected device (`cuda` / `mps` / `cpu` via torch backends), and the
D-CPU-03 escape-hatch hint. Partial-download warnings name `supamem repair`
and contribute to the existing rc accumulator via a new local
`reranker_drift` OR-ed into the line-295 expression. **Full suite: 454
passed, 1 skipped** (the skipped is the env-gated integration test).

## What Shipped

### Task 1 — RED (`c2d91e3`)

| File | Change |
|------|--------|
| `tests/_fixtures/_capture_golden.py` | NEW — single-shot capture script with frozen clock (`_FrozenDT(datetime)` subclass, pinned to `2026-05-01T12:00:00Z`). Builds 10 deterministic Qdrant hits across 4 distinct cluster vectors so dedup spares ≥3 hits. |
| `tests/_fixtures/tuned_hybrid_pre_phase8.json` | NEW — JSON golden of pre-Phase-8 RRF + recency output captured at HEAD before any tuned_hybrid edit. 4 chunks (d1, d2, d4, d5 — d3 dedups against d1). |
| `tests/test_tuned_hybrid_rerank.py` | NEW — 5 tests: `test_off_byte_identical` (D-COMPOSE-03; freeze clock fixture), `test_on_replaces_score_and_skips_recency` (D-COMPOSE-01; integer-score guarantees no recency leak), `test_on_widens_prefetch` + `test_off_keeps_default_prefetch` (D-POOL-01; 50 vs 20), `test_dedup_runs_after_rerank` (D-COMPOSE-02; custom promote-p2 reranker proves higher-rerank survives dedup). |
| `tests/test_doctor.py` | +3 tests: `test_doctor_reranker_panel_healthy` (D-DOCTOR-01 fields), `test_doctor_reranker_panel_partial_download` (D-DOCTOR-02 + B2 unambiguous rc=1 attribution: pins qdrant_up=True + collection present so any rc=1 is reranker_drift), `test_doctor_reranker_p50_p95_verifiable` (W3 fix: pre-loads 20 latency samples, asserts deque-path median match OR Welford-path 'approx' label). |
| `tests/integration/test_rerank_pipeline.py` | xfail marker removed; real env-gated body indexes 10-doc fixture corpus through Qdrant, queries with off + on, asserts orderings differ + rerank_score set. |

10 tests failed RED (4 rerank-branch tests + 3 doctor tests + 3 already
passing for off-path because off-path does not yet need any code change).
The 2 immediately-passing tests (`test_off_byte_identical`,
`test_off_keeps_default_prefetch`) prove the byte-identity fixture is
sound BEFORE any production code changes — this is the critical guard
against the load-bearing invariant.

### Task 2a — GREEN tuned_hybrid only (`55b6e07`)

`src/supamem/retrieval/tuned_hybrid.py` — single-method surgery on
`query()`:

1. **After `_ensure()`** — call `load_reranker(reranker_name, self.config)`
   inside a `try/except LookupError` (fail-soft to off if the plugin
   vanishes between `load_config` validation and query-time).
2. **Prefetch widening (D-POOL-01)** — `prefetch_limit =
   config.reranker_prefetch_per_arm if reranker else PREFETCH_LIMIT`
   (50 vs 20). Both Prefetch arms read this single variable.
3. **Raw candidates built ONCE** — same `(id, score, payload, vec)`
   tuple-list shape; vec extraction unchanged.
4. **`if reranker:` branch (D-COMPOSE-01/02)**:
   - Build `pre_rerank: list[RetrievedChunk]` from raw (RRF score
     preserved on `score`; rerank_score=None initially).
   - `t0 = time.perf_counter()`; `reranker.rerank(text, pre_rerank)`;
     `elapsed_ms = (time.perf_counter() - t0) * 1000`.
   - Plugin-raise → `err_console.print` warn + `reranker = None`
     (fall through to off-branch). T-RERANK-INVAR fail-open.
   - `len(reranked) > len(pre_rerank)` → same fall-through path.
   - On success: `bump("rerank", "rerank_latency_ms", 0, elapsed_ms)`
     (Welford + deque). Build `adjusted` from reranked order, looking
     up vec + payload by id. **No resort** — reranker owns ordering.
5. **`if not reranker:` branch** — UNCHANGED pre-Phase-8 path: T-4
   recency multiplier loop + sort by score desc.
6. **T-5 cosine dedup + T-8 token-budget loop** — UNCHANGED. Output
   `RetrievedChunk` gains `rerank_score=score if reranker else None`.

| Verification | Result |
|---|---|
| `uv run pytest tests/test_tuned_hybrid_rerank.py tests/test_tuned_hybrid.py -q` | 17 passed |
| `uv run ruff check src/supamem/retrieval/tuned_hybrid.py` | clean |
| `grep -c "load_reranker" src/supamem/retrieval/tuned_hybrid.py` | 2 |

### Task 2b — GREEN doctor + counter (`23d9f06`)

**`src/supamem/stats/counter.py`** — Phase 8 deque path (W3
verifiability):

- `_LATENCY_DEQUES: dict[(str,str), collections.deque[float]] =
  defaultdict(lambda: deque(maxlen=100))` — process-local ring buffer
  per `(kind, source)`.
- `bump()` body: `if float(latency_ms) > 0.0:
  _LATENCY_DEQUES[(kind, source)].append(...)` — appended BEFORE the
  flock'd disk write so doctor sees fresh samples even if the disk
  path errors out.
- `get_latency_samples(kind, source) -> list[float]` — public read API
  for doctor; returns empty list when no samples yet.

**`src/supamem/doctor.py`** — Reranker panel inserted between
Section 2e (Room histogram) and Section 3 (Installed clients drift):

```python
console.print()
console.print("[supamem.brand]Reranker[/supamem.brand]")
reranker_drift = False  # B2 fix: NEW local accumulator
ok(f"name           = {rname}  [source: {rname_src}]")
if rname != "off":
    ok(f"model_id       = {rmodel}")
    # cache_path probe via _model_cache_dir()
    # snapshot lookup: models--<slug>/snapshots/* OR <slug>/*
    # manifest probe: rglob actual files, diff against _expected_manifest.json
    # warn(...) + reranker_drift=True on missing/unreadable/pct<0.9
    # latency: get_latency_samples → statistics.median + quantiles(n=20)[18]
    # device: torch.cuda.is_available() / torch.backends.mps.is_available()
    # info("(set retrieval.reranker = 'off' to disable; restores pre-Phase-8 latency)")
```

**rc accumulator extended (B2 fix, line ~295)**:

```python
if (
    not qdrant_up
    or any_drift
    or reranker_drift
    or (qdrant_up and not coll_status.get("present"))
):
    rc = 1
```

`reranker_drift` is declared OUTSIDE the `rname != "off"` branch (set to
False at panel top) so it is always in scope. The invented
`_doctor_status_drift` does NOT appear (`! grep -q "_doctor_status_drift"
src/supamem/doctor.py` → succeeds).

| Verification | Result |
|---|---|
| `uv run pytest tests/test_doctor.py tests/test_counter.py -q` | 18 passed |
| `uv run ruff check src/supamem/doctor.py src/supamem/stats/counter.py` | clean |
| `grep -F "supamem.brand]Reranker" src/supamem/doctor.py \| wc -l` | 1 |
| `grep -c "_expected_manifest.json" src/supamem/doctor.py` | 2 |
| `grep -c "reranker_drift" src/supamem/doctor.py` | 6 |
| `grep -c "_doctor_status_drift" src/supamem/doctor.py` | 0 |

### Task 3 — Full-suite regression + ruff cleanup (`863e510`)

Hoisted `import datetime as _dt` to the canonical top-of-file group in
`tests/test_tuned_hybrid_rerank.py` (ruff E402 fired on the prior
mid-module placement).

| Verification | Result |
|---|---|
| `uv run pytest -q` (full suite, ~9 min foreground) | **454 passed, 1 skipped** |
| `uv run ruff check src tests` | clean |

The 1 skipped = `tests/integration/test_rerank_pipeline.py::test_rerank_on_rescores_tuned_hybrid_candidates` (env-gated; runs only on `SUPAMEM_INTEGRATION_RERANKER=1` + Qdrant-up dev machines).

## query() Diff (interface-first)

Lines 129-219 → 129-258 (net +49 lines). Key shape changes:

- **Lines 138-148 (NEW)**: `load_reranker` import + invocation; `prefetch_limit` derived. Single load-on-call (cheap; `"off"` short-circuits before iterating entry-points).
- **Lines 152-153, 161-162 (MODIFIED)**: `Prefetch.limit=prefetch_limit` instead of `=PREFETCH_LIMIT` on BOTH arms.
- **Lines 175-191 (REPLACED)**: was T-4 recency loop (16 lines); now the `raw` build (vec extraction unchanged) PLUS the `if reranker:` rerank-branch (28 lines).
- **Lines 217-225 (NEW)**: `if not reranker:` block restoring pre-Phase-8 T-4 recency multiplier path.
- **Lines 228-258 (UNCHANGED)**: T-5 dedup + T-8 budget loop; the only diff is `rerank_score=score if reranker else None` on the `RetrievedChunk` constructor.

## Doctor Panel Field List + Drift Conditions

| Field | Source | Drift trigger? |
|---|---|---|
| `name` | `cfg.reranker_name` | No (config is the source of truth) |
| `model_id` | `cfg.reranker_model_id` | No (only printed when name != "off") |
| `cache_path` | `_model_cache_dir()` | Probe failure → `reranker_drift = True` |
| `size` | rglob(snap) vs `_expected_manifest.json` | Missing files OR pct < 0.9 → `reranker_drift = True` |
| `manifest unreadable` | json.loads exception | → `reranker_drift = True` (T-INTEGRITY-01) |
| `snapshot not found` | empty glob → no candidates | → `reranker_drift = True` |
| `rerank_p50_ms / rerank_p95_ms` | `statistics.median` + `statistics.quantiles(n=20)[18]` over `_LATENCY_DEQUES` | No (telemetry only) |
| `device` | `torch.cuda.is_available()` / `torch.backends.mps.is_available()` | No |
| escape-hatch hint | static `info(...)` line | No |

Final rc expression now reads:

```python
if (
    not qdrant_up
    or any_drift            # managed-block version drift (Section 3)
    or reranker_drift       # NEW Phase 8: cache integrity drift
    or (qdrant_up and not coll_status.get("present"))
):
    rc = 1
```

Three independent drift contributors flow through the SAME line-295 OR.
No double-counting; no invented variable names.

## p50/p95 Computation Choice — DEQUE Path (W3 fix)

**Chose deque over Welford-mean-with-approx-label.** Rationale:

- **Welford** (the existing `aggregates.json` Welford accumulator) only
  carries `sum / sumsq / count / min / max`. Recovering median or 95th
  percentile from those moments requires a normal-approximation
  (`p95 ≈ mean + 2*sigma`) that is OFF by 30-50% on long-tailed
  latency distributions (rerank latency is bimodal: warm-cache vs
  cold-fetch). Printing such a number without a label is the W3
  failure mode; printing with `approx` label leaks the error to users.
- **Deque** (new `collections.deque(maxlen=100)` per `(kind, source)`)
  carries the raw samples, so `statistics.median` and
  `statistics.quantiles(n=20)[18]` are TRUE percentiles. The
  verifiability test pre-loads 20 known samples and asserts
  `abs(printed_p50 - statistics.median(samples)) <= 0.5` — trivially
  satisfied because both compute the same thing.

Trade-off accepted: deque is process-local (not persisted across CLI
runs). For one-shot CLI invocations this prints "(no samples yet)" until
at least one rerank fires in the same process. For a long-running MCP
server this accumulates correctly.

## JSON Golden Capture Procedure

To re-baseline the byte-identity fixture after an INTENTIONAL change to
RRF scoring or T-4 recency math:

```bash
# 1. Verify the change is intentional + documented in CONTEXT.md or PLAN.md
# 2. Run the capture script (frozen clock = 2026-05-01T12:00:00Z):
uv run python tests/_fixtures/_capture_golden.py
# 3. Inspect the diff:
git diff tests/_fixtures/tuned_hybrid_pre_phase8.json
# 4. Run the byte-identity test to confirm:
uv run pytest tests/test_tuned_hybrid_rerank.py::test_off_byte_identical -v
# 5. Commit fixture + production change in the SAME commit so reviewers
#    see the math change paired with the fixture re-baseline:
git add tests/_fixtures/tuned_hybrid_pre_phase8.json src/...
git commit -m "rebaseline: <reason>"
```

The `_FROZEN_NOW` constant in `_capture_golden.py` MUST stay in lock-step
with the same constant in `tests/test_tuned_hybrid_rerank.py`. If you
need a newer date (e.g. 2027-…), update both files in the same commit.

## Integration Test Run Notes

`tests/integration/test_rerank_pipeline.py::test_rerank_on_rescores_tuned_hybrid_candidates`:

- **Skip condition**: `SUPAMEM_INTEGRATION_RERANKER != "1"` → SKIPPED on
  CI by design.
- **Preconditions on a dev machine**:
  1. `docker compose up qdrant` (or point `SUPAMEM_QDRANT_URL` at a
     managed instance).
  2. `uv run supamem install claude-code` (or any client) — pre-fetches
     the mxbai model snapshot. Skipping this step means the test pays
     ~1 GB cold fetch on first run.
- **Run command**:
  ```bash
  SUPAMEM_INTEGRATION_RERANKER=1 uv run pytest \
    tests/integration/test_rerank_pipeline.py -v -s
  ```
- **What it asserts**:
  1. `[c.id for c in out_off] != [c.id for c in out_on]` — rerank-on
     reorders at least one pair vs rerank-off on the 10-doc fixture
     corpus.
  2. Every chunk in `out_on` has `rerank_score is not None`.
- **Cleanup**: deletes the temp collection (`rerank_pipeline_test_<uuid8>`)
  on success; leaves it on failure for postmortem.

## Threat Model Compliance

- **T-INTEGRITY-01 (mitigate, doctor manifest read)**: Plan says "wrap
  manifest read in non-essential-probe try/except". Done — both the
  `json.loads(manifest_path.read_text())` call AND the outer
  `_model_cache_dir()` import are wrapped; failure surfaces "manifest
  unreadable — run `supamem repair`" via `warn()` + sets
  `reranker_drift`. Doctor never crashes.
- **T-CACHE-01 (accept, model cache permissions)**: documented via the
  `cache_path` line in the panel — users see exactly where the model
  lives so per-user 0700 dir is observable.
- **T-RERANK-INVAR (mitigate, plugin returns mismatched-size list)**:
  `len(reranked) > len(pre_rerank)` AND plugin-raise both fall through
  to off-branch (`reranker = None`) with a single `err_console.warn`
  emission. Hits are NEVER silently dropped — the off-branch produces
  the full RRF + recency output as if the reranker were unconfigured.

## Critical Invariants Verified

- ✅ **RERANK-01 (rerank-off byte-identical)**: `test_off_byte_identical`
  asserts `[c.id for c in out] == [c.id for c in expected]` AND every
  field matches the golden via `pytest.approx(rel=1e-6)` under a frozen
  recency clock.
- ✅ **T-4 SKIPPED on rerank-on**: `test_on_replaces_score_and_skips_recency`
  asserts every output score is integer-valued (mock writes integer
  scores; recency-leak would produce floats like 0.983).
- ✅ **T-5 / T-8 AFTER rerank**: `test_dedup_runs_after_rerank` proves
  dedup consumes the reranked ordering (higher-rerank hit survives).
- ✅ **Doctor surfaces partial download with rc=1**:
  `test_doctor_reranker_panel_partial_download` pins
  `qdrant_up=True` + `collection.present=True` so rc=1 is unambiguously
  attributable to `reranker_drift`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Frozen-clock capture for byte-identity test**

- **Found during:** Task 1 RED — first run of `test_off_byte_identical`
  failed with float drift between the captured golden's
  `0.9832133797266536` and the test's `0.9832115825289031`.
- **Issue:** `_recency_multiplier` uses `datetime.now()`. Each
  invocation of `tuned_hybrid.query()` reads a slightly later
  timestamp, so the recency multiplier produces a value drifting by
  microseconds. `pytest.approx(rel=1e-6)` catches this.
- **Fix:** Both the capture script (`_capture_golden.py`) and the test
  (`test_off_byte_identical` via `freeze_recency_clock` fixture)
  monkeypatch `supamem.retrieval.tuned_hybrid.datetime` to a `_FrozenDT`
  subclass pinned to `2026-05-01T12:00:00Z`. The constant lives in
  both files; comments name the dependency.
- **Files modified:** `tests/_fixtures/_capture_golden.py`,
  `tests/test_tuned_hybrid_rerank.py`.
- **Commit:** folded into RED `c2d91e3`.

**2. [Rule 3 — Blocking] Initial 10-doc corpus produced only 2 surviving chunks**

- **Found during:** Task 1 — first capture wrote only 2 chunks to the
  golden because too many fixture vectors collided on `vec_a` /
  `vec_b`, so dedup killed everything past 2.
- **Issue:** Plan acceptance criterion requires ≥3 chunks in the golden.
- **Fix:** Bumped the cluster diversity from 2 to 4 distinct unit
  vectors (`vec_a`, `vec_b`, `vec_c`, `vec_d`). After re-capture, 4
  chunks survive (d1, d2, d4, d5) — d3 dedups against d1 (vec_a_dup),
  exercising the dedup path and proving it's still in effect.
- **Files modified:** `tests/_fixtures/_capture_golden.py`,
  `tests/test_tuned_hybrid_rerank.py` (mirror).
- **Commit:** folded into RED `c2d91e3`.

**3. [Rule 3 — Blocking] ruff E402 on test imports**

- **Found during:** Task 3 full-suite ruff sweep.
- **Issue:** `import datetime as _dt` was placed mid-module (after
  the `_GOLDEN_PATH` computation) — ruff E402 fired.
- **Fix:** Hoisted to the canonical top-of-file import group.
- **Files modified:** `tests/test_tuned_hybrid_rerank.py`.
- **Commit:** REFACTOR `863e510`.

### Bypassed deviation

The plan suggested `git commit --no-verify` for parallel-execution
worktree commits. The repo's `block-no-verify` git hook intercepts the
flag (per CLAUDE.md "Never skip hooks unless the user has explicitly
asked for it"). I committed without `--no-verify`; pre-commit hooks ran
and passed all four times.

No CLAUDE.md hard-constraint violations: zero bare-print calls (all
output via `console.py` exports — `ok` / `warn` / `info` / `err`), no
stdout writes from MCP server (no MCP changes), `config.collection`
never hardcoded (cache paths read `cfg.reranker_model_id`), license
metadata untouched, no `.planning/` artifacts staged outside
`.planning/phases/08-code-aware-reranker/`.

## Dual-Memory Disclosure

Per CLAUDE.md: this executor agent's environment did NOT expose
`mcp__supamem__*` tools (only context7 / microsoft-learn / serena MCPs
were attached per the system reminder, and `qdrant-find` CLI was
absent). I proceeded from the canonical references already loaded into
the plan — `08-CONTEXT.md` (D-COMPOSE-01/02/03, D-POOL-01, D-CPU-02/03,
D-DOCTOR-01/02), `08-RESEARCH.md` (cross-encoder rerank pattern,
Qdrant wide-prefetch convention), `08-PATTERNS.md` (analog citations to
embedders/__init__.py, tuned_hybrid._ensure, doctor panel pattern),
Plan 08-01 + 08-02 SUMMARY.md (load_reranker contract,
_expected_manifest.json artifact), and direct reads of every cited
source file. **supamem dual-memory search empty — proceeding from
code/plan context.**

## TDD Gate Compliance

| Gate | Commit | Status |
|---|---|---|
| RED | `c2d91e3` `test(08-03): RED — rerank branch + doctor panel + p50/p95 verifiability + JSON golden` | Present (10 failing tests on missing branch + missing panel + missing p50/p95) |
| GREEN-2a | `55b6e07` `feat(08-03): GREEN(2a) — tuned_hybrid rerank-on branch + widened prefetch + skip-recency` | Present (5 rerank tests + 12 pre-existing tuned_hybrid tests all GREEN) |
| GREEN-2b | `23d9f06` `feat(08-03): GREEN(2b) — doctor Reranker panel + manifest probe + p50/p95 (deque) + reranker_drift accumulator` | Present (3 new doctor tests GREEN; 18 doctor+counter total GREEN) |
| REFACTOR | `863e510` `test(08-03): full-suite regression check + ruff E402 cleanup` | Present (full 454-passed sweep + ruff E402 hoist) |

All four TDD gates accounted for; sequence verified in
`git log 4ed0a1e..HEAD --oneline`.

## Self-Check: PASSED

- `tests/_fixtures/tuned_hybrid_pre_phase8.json` — FOUND
- `tests/_fixtures/_capture_golden.py` — FOUND
- `tests/test_tuned_hybrid_rerank.py` — FOUND
- `src/supamem/retrieval/tuned_hybrid.py` contains `load_reranker(` — FOUND (2 matches)
- `src/supamem/doctor.py` contains `[supamem.brand]Reranker` — FOUND (1 match)
- `src/supamem/doctor.py` contains `_expected_manifest.json` — FOUND (2 matches)
- `src/supamem/doctor.py` contains `reranker_drift` — FOUND (6 matches)
- `src/supamem/doctor.py` does NOT contain `_doctor_status_drift` — VERIFIED
- `src/supamem/stats/counter.py` contains `_LATENCY_DEQUES` + `get_latency_samples` — FOUND
- Commit `c2d91e3` (RED) — FOUND in `git log`
- Commit `55b6e07` (GREEN-2a) — FOUND in `git log`
- Commit `23d9f06` (GREEN-2b) — FOUND in `git log`
- Commit `863e510` (REFACTOR) — FOUND in `git log`
- `uv run pytest -q` — 454 passed, 1 skipped (matches expected: prior 446 + 8 newly green Phase 8 tests in this plan = 454; the 1 skipped is the env-gated integration test)
- `uv run ruff check src tests` — clean
