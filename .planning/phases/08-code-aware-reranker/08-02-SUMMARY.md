---
phase: 08-code-aware-reranker
plan: 02
subsystem: rerankers + install + init + cli
tags: [eager-fetch, filelock, huggingface_hub, snapshot_download, idempotent, air-gapped, tdd]
provides:
  - api: supamem.rerankers.prepare(model_id) -> Path | None
  - api: supamem.rerankers._model_cache_dir() -> Path
  - api: supamem.rerankers._manifest_matches(model_id) -> bool
  - api: supamem.rerankers._write_expected_manifest(snapshot_dir)
  - artifact: <snapshot>/_expected_manifest.json (schema=1; files+total_bytes)
  - cli-flag: supamem install --skip-models / --no-skip-models
  - cli-flag: supamem init --skip-models / --no-skip-models
  - cli-flag: supamem repair --skip-models / --no-skip-models
  - kwarg: supamem.install.install(skip_models=False)
  - kwarg: supamem.install.repair(skip_models=False)
  - kwarg: supamem.init.run_init(skip_models=False)
requires:
  - 08-01-SUMMARY (reranker plugin loader + ResolvedConfig.reranker_* fields)
  - huggingface_hub>=0.24 (snapshot_download)
  - filelock>=3.13 (FileLock + Timeout)
  - platformdirs (user_cache_dir)
affects:
  - src/supamem/rerankers/__init__.py (added prepare + manifest helpers)
  - src/supamem/install/__init__.py (added _maybe_prepare_models hook + skip_models kwarg)
  - src/supamem/init.py (added skip_models kwarg + pre-fetch block)
  - src/supamem/cli.py (added --skip-models on cmd_install/cmd_init/cmd_repair)
  - tests/test_eager_fetch.py (xfail removed; 7 GREEN tests)
  - tests/test_cli_smoke.py (4 new tests: cold-CLI offline + 3 help flag asserts)
tech-stack:
  added: []  # no new deps; huggingface_hub + filelock landed in Plan 08-01
  patterns:
    - "filelock-protected snapshot_download under platformdirs cache (D-FETCH-05/06)"
    - "Idempotent eager-fetch via _expected_manifest.json probe (D-FETCH-03)"
    - "3-attempt exponential backoff retry on transient HF errors (D-FETCH-07)"
    - "Plain bool Typer Option `--skip-models / --no-skip-models` (avoids flag_value drop)"
    - "Lazy import of supamem.rerankers inside install/init function bodies (cold-help purity)"
    - "HF_HUB_OFFLINE forwarded to snapshot_download(local_files_only=) at call-time"
key-files:
  created:
    - .planning/phases/08-code-aware-reranker/08-02-SUMMARY.md
  modified:
    - src/supamem/rerankers/__init__.py
    - src/supamem/install/__init__.py
    - src/supamem/init.py
    - src/supamem/cli.py
    - tests/test_eager_fetch.py
    - tests/test_cli_smoke.py
decisions:
  - "prepare() short-circuits on _manifest_matches BEFORE acquiring the filelock — keeps the healthy-cache path lock-free (D-FETCH-03 idempotency)"
  - "Forwarded HF_HUB_OFFLINE to snapshot_download via local_files_only= at call-time (huggingface_hub caches the constant at import; the env-var alone would not propagate to a long-running process)"
  - "_maybe_prepare_models swallows non-RuntimeError loader pathologies via log.debug — install must NEVER abort on a model-fetch issue (config still wires; user runs supamem repair later)"
  - "repair() does NOT call prepare() directly — it propagates skip_models to install() which calls prepare(); idempotency is handled by prepare() itself via _manifest_matches"
metrics:
  duration: ~25 min (incl. 12-min full pytest + 1.5-min real HF download verifying offline test)
  tasks: 3
  files: 6
  completed: 2026-05-02
---

# Phase 8 Plan 02: Eager-Fetch Helper + install/init/repair Wiring Summary

The `supamem.rerankers.prepare(model_id)` helper landed: idempotent
HuggingFace snapshot fetch under a `filelock` to a supamem-owned cache
(`platformdirs.user_cache_dir("supamem")/models`, env-overridable via
`SUPAMEM_CACHE_DIR`), 3-attempt exponential backoff on transient errors,
post-download `_expected_manifest.json` for Plan 08-03's doctor partial-
download detector. Wired into `install` / `init` / `repair`, with a
`--skip-models / --no-skip-models` Typer option on all three commands
(plain bool — never `flag_value=` per the pinned Typer warning).
Cold post-install `supamem --help` and `supamem --version` trigger zero
HF egress (subprocess test with `HF_HUB_OFFLINE=1`). TDD cycle clean:
RED `9300689`, GREEN `a3680a0`, REFACTOR skipped (lazy-import audit
already passed end of GREEN).

## What Shipped

### Task 1 — RED (`9300689`)

| File | Change |
|------|--------|
| `tests/test_eager_fetch.py` | Removed `pytestmark = pytest.mark.xfail`. Kept the 3 baseline tests (`test_prepare_writes_to_supamem_cache`, `test_prepare_offline_raises_actionable_error`, `test_filelock_prevents_concurrent_corruption`) and added 4 stronger contract tests: `test_retry_on_transient_then_succeeds` (asserts 3 calls), `test_writes_expected_manifest_json` (schema + total_bytes + files dict), `test_filelock_held_during_snapshot` (W5 — concurrent thread MUST hit Timeout), `test_repair_skips_prepare_when_manifest_matches` (W4 idempotency — pre-populates manifest, asserts snapshot_download is NOT called). 7 tests total. |
| `tests/test_cli_smoke.py` | Added `test_cold_cli_no_network` (subprocess `--help`/`--version` with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` — asserts no `snapshot_download` / `downloading` strings in output, exit 0), `test_cmd_init_help_shows_skip_models` (B1 fix — Typer surface contains the flag), `test_cmd_install_help_shows_skip_models`, `test_cmd_repair_help_shows_skip_models`. |

10 tests failed RED (`ImportError: prepare`, `AttributeError: _BACKOFF_BASE_S`, `--skip-models` not in init/repair help). All failures named the missing symbols.

### Task 2 — GREEN (`a3680a0`)

**`src/supamem/rerankers/__init__.py`** — appended ~135 lines:

| Symbol | Behavior |
|---|---|
| `_BACKOFF_BASE_S = 1.0` | Module constant; tests monkeypatch to 0.0 to skip sleep. |
| `_MAX_RETRIES = 3`, `_LOCK_TIMEOUT_S = 3600` | D-FETCH-07 retry + D-FETCH-06 lock-timeout knobs. |
| `_ALLOW_PATTERNS` | `["*.safetensors", "*.json", "tokenizer*", "*.txt", "*.model"]` — keeps the snapshot lean. |
| `_model_cache_dir()` | Honors `SUPAMEM_CACHE_DIR` (test fixture); defaults to `platformdirs.user_cache_dir("supamem")/models` (D-FETCH-05). |
| `_write_expected_manifest(dir)` | rglob the snapshot, exclude the manifest file itself, dump `{"files": {rel: size}, "total_bytes": N, "schema": 1}` to `<dir>/_expected_manifest.json`. |
| `_manifest_matches(model_id)` | Probe HF layout `models--<slug>/snapshots/*` AND test layout `<slug>/*`; verify each manifest entry exists with the right size. Returns False on missing/corrupt/short. |
| `prepare(model_id, *, progress=None)` | (1) `_manifest_matches` short-circuit → return existing snapshot dir, no lock, no network. (2) `mkdir` cache root, acquire `FileLock(.lock, timeout=3600)`. (3) Retry `snapshot_download` up to 3x with `time.sleep(_BACKOFF_BASE_S * 2**attempt)` between attempts; honors `HF_HUB_OFFLINE` via `local_files_only=`. (4) On success → `_write_expected_manifest(snap)` and return path. (5) On retries-exhausted → `err_console.print` actionable message + raise `RuntimeError`. (6) On `Timeout` → distinct actionable error (`"Another supamem install may be running"`). |

**`src/supamem/install/__init__.py`** — added `_maybe_prepare_models(skip_models)` helper that loads config, calls `prepare(cfg.reranker_model_id)` when `reranker_name != "off"`, and routes `RuntimeError` to `warn(...)` (install does NOT abort — client config still wires, user re-runs `supamem repair`). Added `skip_models: bool = False` kwarg to `install()` and `repair()`; `repair()` propagates the flag through to inner `install()` calls (per-target loop).

**`src/supamem/init.py`** — added `skip_models: bool = False` kwarg to `run_init`; pre-fetch block runs after the Qdrant probe and BEFORE collection creation (rationale: if the user is offline at install/init time, they get the actionable error early — before we've created an empty Qdrant collection).

**`src/supamem/cli.py`** — added Typer `Option(False, "--skip-models / --no-skip-models", help=...)` on `cmd_install`, `cmd_init`, `cmd_repair`, propagated to the underlying functions. Plain bool option — NOT `flag_value=` per the pinned Typer warning in `~/.claude/projects/.../memory/MEMORY.md`.

### Task 3 — REFACTOR (skipped — no diff)

- Lazy-import audit (`grep -E "^(from|import) (torch|transformers|mxbai_rerank)" src/supamem/rerankers/*.py`) — 0 matches. heavy imports stay inside `_ensure()` / `rerank()`.
- Cold-import audit: `python -X importtime -c "import supamem.cli" 2>&1 | grep -ciE "torch|transformers|mxbai_rerank"` → 0.
- `from supamem.rerankers import prepare` is INSIDE `_maybe_prepare_models()` and `run_init()`'s eager-fetch block — never at module top of install/init/cli, so cold `supamem --help` does not pull `huggingface_hub` either.
- `uv build && uvx twine check dist/*` — both wheel + sdist `PASSED`.

## Verification Snapshot

| Check | Result |
|-------|--------|
| `uv run pytest -q` (full suite, foreground 12m24s) | 446 passed, 1 skipped |
| `uv run pytest tests/test_eager_fetch.py -q` | 7 passed (incl. real-HF offline test in 3.18s) |
| `uv run pytest tests/test_cli_smoke.py -q` (subset) | cold-CLI no-network + 3 help-flag tests all GREEN |
| `uv run ruff check src tests` | All checks passed |
| `uv run supamem install --help \| grep -F -- "--skip-models"` | `--skip-models / --no-skip-models  Skip eager ML…` |
| `uv run supamem init --help \| grep -F -- "--skip-models"` | `--skip-models / --no-skip-models  Skip ML model pre-fetch…` |
| `uv run supamem repair --help \| grep -F -- "--skip-models"` | `--skip-models / --no-skip-models  Skip ML model re-fetch step…` |
| `uv build && uvx twine check dist/*` | both wheel + sdist PASSED |
| `python -X importtime -c "import supamem.cli" \| grep -ciE "torch\|transformers\|mxbai_rerank"` | 0 |
| `grep -c "def prepare(" src/supamem/rerankers/__init__.py` | 1 |
| `grep -c "def _manifest_matches" src/supamem/rerankers/__init__.py` | 1 |
| `grep -c "def _write_expected_manifest" src/supamem/rerankers/__init__.py` | 1 |
| `grep -c "FileLock" src/supamem/rerankers/__init__.py` | 1 |
| `grep -c "skip_models" src/supamem/cli.py` | 6 (3 Options × declaration + propagation) |
| `grep -c "skip_models" src/supamem/install/__init__.py` | 6 (helper + install + repair signatures + forwards) |
| `grep -c "skip_models" src/supamem/init.py` | 3 (signature + branch + info-log) |

## Threat Model Compliance

- **T-FETCH-01 (Tampering / Spoofing — HIGH)**: TLS on by default in `huggingface_hub`. `_expected_manifest.json` records files+sizes from the returned snapshot for partial-download detection by Plan 08-03's doctor. Pin-by-`revision=<commit-sha>` deferred until upstream publishes a stable SHA.
- **T-FETCH-02 (Tampering / concurrency — MED)**: `FileLock(<cache>/.lock, timeout=3600)` wraps the network roundtrip. Distinct `Timeout` error path with actionable copy ("Another supamem install may be running. Retry shortly or delete the stale lock"). filelock's stale-lock auto-release (>1h, no PID alive) covers crashed installs. Asserted by `test_filelock_held_during_snapshot` — concurrent thread acquire MUST hit `Timeout`.
- **T-FETCH-03 (DoS / air-gapped UX — MED)**: `--skip-models` opt-out exposed on `cmd_install`, `cmd_init`, `cmd_repair`. Failures route through `_maybe_prepare_models` to `warn(...)` — install does NOT abort. `repair()` is idempotent on healthy cache (`_manifest_matches` short-circuit) — asserted by `test_repair_skips_prepare_when_manifest_matches`.
- **T-FETCH-04 (DoS / network flake — LOW)**: 3-attempt exponential backoff (1s, 2s, 4s); final failure surfaces actionable err_console message + `RuntimeError`. Asserted by `test_retry_on_transient_then_succeeds`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `HF_HUB_OFFLINE` env var did not propagate to `snapshot_download`**

- **Found during:** Task 2 GREEN — `test_prepare_offline_raises_actionable_error` initially failed, downloading the real ~1 GB model in 95 seconds despite the `network_blocked` fixture setting `HF_HUB_OFFLINE=1`.
- **Issue:** `huggingface_hub` reads the `HF_HUB_OFFLINE` env var ONCE at module import (cached on `huggingface_hub.constants`); a fixture that monkeypatches the env var post-import does not propagate.
- **Fix:** `prepare()` reads `os.environ.get("HF_HUB_OFFLINE")` at call-time and forwards `local_files_only=offline` to `snapshot_download(...)`. This mirrors the runtime contract users actually want anyway: a setting that flips offline mode mid-process.
- **Files modified:** `src/supamem/rerankers/__init__.py`.
- **Commit:** folded into `a3680a0` GREEN commit.

**2. [Rule 2 — Missing critical functionality] Generic loader-error swallow in `_maybe_prepare_models`**

- **Found during:** Task 2 GREEN — the plan only handled `RuntimeError` but `load_config()` could raise `SystemExit(2)` on unregistered reranker (Plan 08-01's fail-closed gate), and `getattr(cfg, "reranker_name", "off")` would also fail if the field is missing on a stale ResolvedConfig.
- **Fix:** Wrapped the body in `except Exception: log.debug("...")` AFTER the `RuntimeError → warn` branch — install must NEVER abort on model-fetch flake.
- **Files modified:** `src/supamem/install/__init__.py`, `src/supamem/init.py` (mirrored).
- **Commit:** folded into `a3680a0` GREEN commit.

### Bypassed deviation

- The plan instructed `git commit --no-verify` for parallel-execution worktree commits. The repo's `block-no-verify` git hook intercepts the flag (per CLAUDE.md "Never skip hooks unless the user has explicitly asked for it"). I committed without `--no-verify`; pre-commit hooks ran and passed both times.

No CLAUDE.md hard-constraint violations: zero bare-print calls (all output via `console.py` exports), no stdout writes from MCP server (no MCP changes), `config.collection` never hardcoded, license metadata untouched, no `.planning/` artifacts staged outside `.planning/phases/08-code-aware-reranker/`.

## Dual-Memory Disclosure

Per CLAUDE.md: this executor agent's environment did NOT expose `mcp__supamem__*` tools (only context7 / microsoft-learn / serena MCPs were attached per the system reminder). I proceeded from the canonical references already loaded into the plan — `08-CONTEXT.md` (D-FETCH-01..07), `08-RESEARCH.md` (HF snapshot_download API + filelock semantics, Context7-verified), `08-01-SUMMARY.md` (reranker loader + config wiring), and direct reads of every cited source file. **supamem dual-memory search empty — proceeding from code/plan context.**

## TDD Gate Compliance

| Gate | Commit | Status |
|------|--------|--------|
| RED | `9300689` `test(08-02): RED — eager-fetch helper + cold-CLI offline contract + ...` | Present (10 failing tests naming missing symbols) |
| GREEN | `a3680a0` `feat(08-02): GREEN — prepare() + idempotent repair + --skip-models on install/init/repair` | Present (11 newly green: 7 eager_fetch + 4 cli_smoke) |
| REFACTOR | _skipped_ | Lazy-import audit + frozen-copy audit already green at end of GREEN; nothing to clean |

All TDD gates accounted for; sequence verified in `git log f684a26..HEAD --oneline`.

## Self-Check: PASSED

- `src/supamem/rerankers/__init__.py` contains `def prepare(` — FOUND
- `src/supamem/rerankers/__init__.py` contains `def _manifest_matches(` — FOUND
- `src/supamem/rerankers/__init__.py` contains `def _write_expected_manifest(` — FOUND
- `src/supamem/rerankers/__init__.py` contains `from filelock import FileLock` — FOUND
- `src/supamem/install/__init__.py` contains `_maybe_prepare_models` + `skip_models` kwarg on install + repair — FOUND
- `src/supamem/init.py` contains `skip_models: bool = False` on `run_init` — FOUND
- `src/supamem/cli.py` contains `--skip-models / --no-skip-models` on cmd_install AND cmd_init AND cmd_repair — FOUND (verified by 3 GREEN help-flag subprocess tests)
- Commit `9300689` (RED) — FOUND in `git log`
- Commit `a3680a0` (GREEN) — FOUND in `git log`
- `uv run pytest -q` — 446 passed, 1 skipped (matches expected: prior 435 + 11 newly green = 446; the 3 xfailed eager_fetch RED skeletons are now part of the 7 GREEN eager_fetch tests)
- `uv run ruff check src tests` — clean
- `uv build && uvx twine check dist/*` — both PASSED
- Lazy-import audit — 0 matches for torch/transformers/mxbai_rerank in cold `import supamem.cli`
