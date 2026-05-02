---
phase: 08-code-aware-reranker
plan: 01
subsystem: rerankers + config + retrieval
tags: [reranker, plugin-loader, config, frozen-pydantic, lazy-import, tdd]
provides:
  - module: supamem.rerankers (RerankerProtocol + load_reranker)
  - module: supamem.rerankers.mxbai_v2 (MxbaiV2Reranker default impl)
  - field: RetrievedChunk.rerank_score: Optional[float] = None
  - fields: ResolvedConfig.reranker_{name,model_id,top_n,prefetch_per_arm,batch_size}
  - entry-point: supamem.reranker -> mxbai_v2 = MxbaiV2Reranker
  - validation: load_config() unknown reranker_name -> err_console + SystemExit(2)
requires:
  - 08-00-SUMMARY (mock_reranker fixture, RED skeletons, conftest fixtures)
  - existing supamem.embedders loader pattern
  - existing TunedHybridBackend._ensure() lazy-init pattern
  - existing _NESTED_TABLES + _apply_nested precedent
affects:
  - src/supamem/rerankers/__init__.py (NEW)
  - src/supamem/rerankers/mxbai_v2.py (NEW)
  - src/supamem/retrieval/types.py (added rerank_score field)
  - src/supamem/config.py (5 fields + ConfigChain mirror + _NESTED_TABLES + validation gate)
  - pyproject.toml (entry-point group + 3 deps)
  - tests/test_rerankers.py (5 -> 7 tests, xfail removed)
  - tests/test_config.py (3 new reranker tests)
tech-stack:
  added:
    - mxbai-rerank>=0.1.6,<0.2
    - huggingface_hub>=0.24
    - filelock>=3.13
  patterns:
    - "Plugin loader via importlib.metadata.entry_points (mirrors embedders/__init__.py)"
    - "Lazy heavy-import inside _ensure() body (mirrors tuned_hybrid._ensure)"
    - "Frozen Pydantic update via model_copy(update=...) (T-FROZEN-01)"
    - "_NESTED_TABLES flat-mapping for [supamem.reranker] table (D-CONFIG-03)"
    - "fail-closed config validation: err_console + SystemExit(2) on unknown plugin"
key-files:
  created:
    - src/supamem/rerankers/__init__.py
    - src/supamem/rerankers/mxbai_v2.py
  modified:
    - src/supamem/retrieval/types.py
    - src/supamem/config.py
    - pyproject.toml
    - uv.lock
    - tests/test_rerankers.py
    - tests/test_config.py
decisions:
  - "Loader instantiates on .load() with config kwarg (vs embedders which return the class) — required because MxbaiV2Reranker.__init__(*, config) needs config (D-CONTRACT-04)"
  - "name == 'off' short-circuits before iterating entry-points — cheap fast-path that preserves pre-Phase-8 byte-identical retrieval (D-CONFIG-03)"
  - "Validation gate lives at end of load_config(), NOT in ResolvedConfig.__post_init__ (raw dataclass construction must not raise — _cfg_with_caps test helper depends on it)"
metrics:
  duration: ~12 min
  tasks: 3
  files: 6
  completed: 2026-05-02
---

# Phase 8 Plan 01: Reranker Plugin Contract + MxbaiV2 Default Impl Summary

The `supamem.reranker` plugin contract landed: `RerankerProtocol` + `load_reranker()` loader (mirrors `embedders/__init__.py` shape) with `"off"` sentinel short-circuit, `MxbaiV2Reranker` default impl using lazy `_ensure()` (heavy imports — torch, transformers, mxbai_rerank — live inside the function body so cold `supamem --help` does not pay the cost), `RetrievedChunk.rerank_score: Optional[float] = None` (frozen=True preserved; updates flow through `model_copy(update=)`), 5 new `ResolvedConfig.reranker_*` flat fields populated from `[supamem.reranker]` via `_NESTED_TABLES`, and a fail-closed validation gate at the end of `load_config()` that exits with `err_console` + `SystemExit(2)` when `reranker_name` doesn't resolve to a registered entry-point. TDD cycle clean: RED `1ee8659`, GREEN `0df0540`, REFACTOR skipped (no diff). Full suite: **435 passed, 1 skipped, 3 xfailed** (the 3 xfailed are eager_fetch RED skeletons targeted by Plan 08-02). Lazy-import audit: zero `from torch` / `from transformers` / `from mxbai_rerank` at module top in `src/supamem/rerankers/`.

## What Shipped

### Task 1 — RED (`1ee8659`)

| File | Change |
|------|--------|
| `tests/test_rerankers.py` | Removed `pytestmark = pytest.mark.xfail` block; added `test_top_n_clamp_warns` (capsys-asserted err_console warning); added `test_off_short_circuits_without_loading_model` (monkeypatched `entry_points` spy proves zero iterations on `"off"`). 7 tests total. |
| `tests/test_config.py` | Added `test_reranker_defaults_match_d_config_02` (locks D-CONFIG-02 defaults), `test_reranker_nested_table_flattens` (`[supamem.reranker]` → 5 flat fields + ConfigChain attribution), `test_unregistered_reranker_name_exits_2` (asserts `SystemExit(2)` + err_console contains the bad name). |

10 tests failed RED — 7 with `ModuleNotFoundError(supamem.rerankers)`, 2 with `AttributeError(reranker_*)`, 1 `DID NOT RAISE SystemExit`. All failures named the missing symbols, proving the tests target the right contract.

### Task 2 — GREEN (`0df0540`)

**`src/supamem/rerankers/__init__.py` (new — 56 lines)**
- `RerankerProtocol` (runtime-checkable `typing.Protocol`) with `name: str`, `model_id: str`, `rerank(query, candidates) -> list[RetrievedChunk]`.
- `load_reranker(name, config) -> Optional[Any]`:
  - `name == "off"` → returns `None` BEFORE iterating entry-points (cheap fast-path, asserted by `test_off_short_circuits_without_loading_model`).
  - Iterates `entry_points(group="supamem.reranker")`, finds matching `ep.name`, calls `ep.load()(config=config)`.
  - On no match, raises `LookupError` with the sorted list of registered names in the message.
- Stub comment block documenting the eager-fetch helpers Plan 08-02 will add (`_model_cache_dir`, `prepare()`).

**`src/supamem/rerankers/mxbai_v2.py` (new — 90 lines)**
- `class MxbaiV2Reranker` with class-level `name = "mxbai_v2"`, `model_id = "mixedbread-ai/mxbai-rerank-base-v2"`.
- `__init__(*, config: ResolvedConfig)` — cheap; just stashes `config` and sets `self._model = None`.
- `_ensure()` — first-call materialization: `from mxbai_rerank import MxbaiRerankV2` inside the function (lazy-import discipline), instantiates with `self.config.reranker_model_id`, bumps `kind="rerank", source="load_latency_ms"` on the Welford counter (failure swallowed via `except Exception: pass` per CLAUDE.md non-essential-probe sanction).
- `rerank(query, candidates)`:
  - Empty short-circuit returns `[]`.
  - Top-n clamp: `min(reranker_top_n, len(candidates))`. If `top_n > len(candidates)`, prints `[supamem.warn]` warning to `err_console` (D-POOL-02) BEFORE materializing the model.
  - Calls `model.rank(query, documents, top_k=, batch_size=, return_documents=False, sort=True, show_progress=False)`.
  - Bumps `rerank_latency_ms` counter.
  - Builds output via `src.model_copy(update={"score": float(r.score), "rerank_score": float(r.score)})` — frozen-Pydantic-safe (T-FROZEN-01).

**`src/supamem/retrieval/types.py`** — single line appended after `payload`: `rerank_score: Optional[float] = None`. `frozen=True` preserved (verified by all 435 existing tests still green).

**`src/supamem/config.py`** — 4 stanzas:
1. `ResolvedConfig`: 5 new flat fields with D-CONFIG-02 defaults (`reranker_name="mxbai_v2"`, `reranker_model_id="mixedbread-ai/mxbai-rerank-base-v2"`, `reranker_top_n=50`, `reranker_prefetch_per_arm=50`, `reranker_batch_size=16`).
2. `ConfigChain`: 5 corresponding `: Source = "default"` lines.
3. `_NESTED_TABLES`: `("reranker", { "name": "reranker_name", "model_id": "reranker_model_id", "top_n": "reranker_top_n", "prefetch_per_arm": "reranker_prefetch_per_arm", "batch_size": "reranker_batch_size" })`.
4. End of `load_config()` — validation gate: when `cfg.reranker_name != "off"`, look up `entry_points(group="supamem.reranker")` and `raise SystemExit(2)` with an `err_console` message naming the bad value + the registered set if no match.

**`pyproject.toml`** — appended `[project.entry-points."supamem.reranker"]` group with `mxbai_v2 = "supamem.rerankers.mxbai_v2:MxbaiV2Reranker"`; added 3 deps (`mxbai-rerank>=0.1.6,<0.2`, `huggingface_hub>=0.24`, `filelock>=3.13`). `uv.lock` regenerated by `uv sync --extra dev`.

### Task 3 — REFACTOR (skipped — no diff)

- Lazy-import audit: `grep -E "^(from|import) (torch|transformers|mxbai_rerank)" src/supamem/rerankers/*.py` → 0 matches. All heavy imports live inside `_ensure()` / `rerank()` function bodies.
- Frozen-copy audit: `grep -c "\.score = \|\.rerank_score = "` on `mxbai_v2.py` → 0; `grep -c "model_copy(update="` → 1. Only path that mutates rerank scores goes through the frozen-safe API.
- Full pytest + ruff already green at end of Task 2; nothing to refactor. Plan instructs "skip commit if nothing to refactor" — followed.

## Verification Snapshot

| Check | Result |
|-------|--------|
| `uv run pytest -q` (full suite) | 435 passed, 1 skipped, 3 xfailed |
| `uv run pytest tests/test_rerankers.py tests/test_config.py -q` | 20 passed |
| `uv run ruff check src tests` | All checks passed |
| `python -X importtime -c "import supamem.rerankers" 2>&1 \| grep -ci torch` | 0 |
| `grep -c "rerank_score: Optional\[float\] = None" src/supamem/retrieval/types.py` | 1 |
| `grep -c "reranker_name: str = \"mxbai_v2\"" src/supamem/config.py` | 1 |
| `grep -c '\[project.entry-points."supamem.reranker"\]' pyproject.toml` | 1 |
| Smoke: `load_reranker("off", ResolvedConfig())` | returns `None` |

## Threat Model Compliance

- **T-PLUGIN-01 (accept)**: documented in `pyproject.toml` comment above the new entry-point group — third-party `supamem.reranker` packages execute on `.load()` per the standard Python plugin trust model.
- **T-CONFIG-01 (mitigate)**: `load_config()` validation gate fails closed with `err_console` + `SystemExit(2)` on unknown `reranker_name` (asserted by `test_unregistered_reranker_name_exits_2`).
- **T-FROZEN-01 (mitigate)**: `RetrievedChunk.rerank_score` added with default `None`, `frozen=True` preserved (asserted indirectly by all existing model-construction tests still passing). All updates in `MxbaiV2Reranker.rerank()` go through `model_copy(update={...})` — verified by frozen-copy audit grep.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 – Blocking] Add inline `# noqa: PLC0415` on lazy imports**

- **Found during:** Task 2 GREEN verification (initial `ruff check` reported `PLC0415` — `import-outside-top-level` — on the inside-function imports of `mxbai_rerank`, `supamem.stats.counter`).
- **Issue:** Lazy-import discipline is the explicit project pattern (PATTERNS.md §"Lazy Heavy-Import"; mirrors `tuned_hybrid._ensure`), but ruff's stricter ruleset flags it.
- **Fix:** Added `# noqa: PLC0415` comments on the in-function imports (inside `_ensure()`, inside `rerank()`'s telemetry probe, inside the `load_config()` validation gate). The contract REQUIRES these to be inside the function body — silencing the lint is the correct local fix.
- **Files modified:** `src/supamem/rerankers/mxbai_v2.py`, `src/supamem/config.py`.
- **Commit:** `0df0540` (folded into Task 2 GREEN commit).

**2. [Rule 3 – Blocking] Fixture `mock_reranker_entry_point` patches `supamem.rerankers.entry_points`**

- **Found during:** Reading `tests/conftest.py` before Task 1 RED.
- **Issue:** The fixture (committed in Plan 08-00) patches *both* `importlib.metadata.entry_points` AND `supamem.rerankers.entry_points` (with `raising=False`) — a future-proofing for "if rerankers package imported `entry_points` at module top, also override there." The plan didn't specify; the fixture authors anticipated.
- **Fix:** Imported `entry_points` at module top in `src/supamem/rerankers/__init__.py` exactly so the fixture's `raising=False` setattr lands. This is consistent with the embedders pattern. No code change required from me — the fixture already accommodates both shapes.
- **Files modified:** none additional.
- **Commit:** n/a.

**Bypassed deviation (NOT applied):**

- The plan's commit instructions specified `--no-verify` for parallel-execution worktree commits. The repo's `block-no-verify` git hook intercepted both attempts and rejected the flag. I committed without `--no-verify`; pre-commit hooks ran and passed both times. This is per CLAUDE.md "Never skip hooks unless the user has explicitly asked for it" — the hook itself is the explicit instruction NOT to skip.

No CLAUDE.md hard-constraint violations: zero bare-print calls, no stdout writes from MCP server (no MCP changes here), `config.collection` never hardcoded, license metadata untouched, no GSD `.planning/` artifacts staged for commit.

## Dual-Memory Disclosure

Per CLAUDE.md: this executor agent's environment did NOT expose `mcp__supamem__*` tools (only context7/microsoft-learn/serena MCPs were attached per the system reminder). I proceeded from the canonical references already loaded into the plan — `08-CONTEXT.md` (D-CONTRACT-01..05, D-CONFIG-01..03, D-POOL-01..04, D-FETCH-01..07), `08-RESEARCH.md` (mxbai-rerank API, lazy-import / frozen-copy pitfalls), `08-PATTERNS.md` (analog citations to `embedders/__init__.py`, `tuned_hybrid._ensure`, classifier_rooms `_NESTED_TABLES` precedent), and direct reads of every cited source file. The plan was authored *with* dual-memory context already; I did not need to re-derive decisions during implementation.

## TDD Gate Compliance

| Gate | Commit | Status |
|------|--------|--------|
| RED | `1ee8659` `test(08-01): RED — reranker plugin loader + config contract` | Present (10 tests failing on missing symbols) |
| GREEN | `0df0540` `feat(08-01): GREEN — supamem.reranker plugin contract + mxbai_v2 default impl` | Present (20 tests passing) |
| REFACTOR | _skipped_ | Lazy-import + frozen-copy audits already green at end of GREEN; nothing to clean |

All three TDD gates of the cycle accounted for; sequence verified in `git log 3f6aef5..HEAD`.

## Self-Check: PASSED

- `src/supamem/rerankers/__init__.py` — FOUND
- `src/supamem/rerankers/mxbai_v2.py` — FOUND
- `src/supamem/retrieval/types.py` contains `rerank_score: Optional[float] = None` — FOUND
- `src/supamem/config.py` contains `reranker_name: str = "mxbai_v2"` — FOUND
- `pyproject.toml` contains `[project.entry-points."supamem.reranker"]` — FOUND
- Commit `1ee8659` (RED) — FOUND
- Commit `0df0540` (GREEN) — FOUND
- `uv run pytest -q` — 435 passed, 1 skipped, 3 xfailed (matches expected: prior 425 + 10 newly-green Phase 8 tests; xfailed 8 → 3 because 5 turned GREEN this plan)
- `uv run ruff check src tests` — clean
- Lazy-import audit (`grep -E "^(from|import) (torch|transformers|mxbai_rerank)" src/supamem/rerankers/*.py`) — 0 matches
- Frozen-copy audit on `mxbai_v2.py` — 0 direct attribute assignments, 1 `model_copy(update=)`
