---
phase: 07-coding-path-classifier
plan: 01
subsystem: indexer/config
tags: [classifier, config, path-equality, tdd]
requires: []
provides:
  - "src/supamem/indexer/classifier.py:classify_room"
  - "ResolvedConfig.classifier_rooms (D-14 defaults, D-01a order)"
  - "ConfigChain.classifier_rooms provenance tracking"
  - "[supamem.classifier.rooms] TOML table loading via _NESTED_TABLES"
affects:
  - "src/supamem/config.py (3 additive edits)"
tech-stack:
  added: []
  patterns:
    - "Pure path-component classifier: set(Path.parts) ∩ set(keywords)"
    - "First-match-wins via dict insertion order (PEP 468)"
    - "Dict-leaf TOML flatten through existing _NESTED_TABLES"
key-files:
  created:
    - src/supamem/indexer/classifier.py
    - tests/test_classifier.py
  modified:
    - src/supamem/config.py
    - tests/test_config.py
decisions:
  - D-01: payload.room single string, first-match-wins by config order
  - D-01a: default order tests, types, migrations, config, scripts, docs, frontend, backend
  - D-11: classify_room helper at src/supamem/indexer/classifier.py
  - D-12: NOT a plugin entry-point in v1
  - D-14: defaults shipped via ResolvedConfig field_factory (NOT share/default.toml)
  - D-15: ResolvedConfig.classifier_rooms + _NESTED_TABLES "classifier" parent
metrics:
  completed: 2026-05-01
  tasks_completed: 3
  files_changed: 4
  commits: 4
---

# Phase 7 Plan 01: Coding-Path Classifier Foundation Summary

Pure path-component `classify_room` helper with first-match-wins semantics by config order, plus `[supamem.classifier.rooms]` TOML plumbing through the existing `_NESTED_TABLES` mechanism — the foundation Plans 07-02 (indexer integration) and 07-03 (MCP `where` filter) build on.

## Public Contract

```python
# src/supamem/indexer/classifier.py
def classify_room(
    file_path: str | Path,
    rooms: dict[str, list[str]],
) -> Optional[str]:
    """First-match-wins path-component classifier (D-01 / D-01a)."""
```

**Behavior:** `set(Path(file_path).parts) ∩ set(keywords)` per room, in `rooms` insertion order. Returns the first matching room name, or `None`. No I/O, no logging, no exceptions on valid str/Path input.

## D-01a Priority Verified on 4 Monorepo Layouts

| Path | DEFAULT_ROOMS classification | Why |
|------|------------------------------|-----|
| `tests/backend/api_test.py` | `tests` | `tests` precedes `backend` in default order |
| `src/myapp/foo.py` (Python) | `backend` | `src` is a backend keyword; no frontend match |
| `src/components/Button.tsx` (JS monorepo) | `frontend` | both `src` and `components` present; `frontend` precedes `backend`, `components` is in frontend keywords |
| `src/main.rs` (Rust) | `backend` | `src` keyword |
| `cmd/myapp/main.go` (Go) | `None` | known gap: `cmd` is intentionally NOT a default keyword; Go projects need user-configured rooms |
| `data/chest_xray/img.png` (CLASS-02 negative) | `None` | path-component equality — `"test"` does NOT substring-match `"chest_xray"` |
| `/abs/tests/foo.py` (absolute) | `tests` | RESEARCH Pitfall 6: leading `/` is harmless via set intersection |

## Config Plumbing — Three Additive Edits to `src/supamem/config.py`

1. **`ResolvedConfig` dataclass** (after the transcript_* block):
   ```python
   classifier_rooms: dict[str, list[str]] = field(default_factory=lambda: {
       "tests":      ["tests", "test", "__tests__", "spec", "specs"],
       "types":      ["types", "@types", "typings"],
       "migrations": ["migrations", "alembic", "schema"],
       "config":     ["config", "configs", ".github", "ci"],
       "scripts":    ["scripts", "bin", "tools"],
       "docs":       ["docs", "documentation"],
       "frontend":   ["frontend", "web", "client", "ui", "components", "pages"],
       "backend":    ["src", "backend", "api", "server", "lib"],
   })
   ```

2. **`ConfigChain` dataclass** (mirror placement):
   ```python
   classifier_rooms: Source = "default"
   ```

3. **`_NESTED_TABLES` tuple** (appended):
   ```python
   ("classifier", {"rooms": "classifier_rooms"}),
   ```

User TOML at `[supamem.classifier.rooms]` REPLACES the defaults dict (matches `transcript_include_paths_glob` precedent — leaf-replace, not merge). Verified R-06: `_apply_nested` is type-agnostic; the dict-leaf passes through `setattr(cfg, dst_field, sub[src_key])` unchanged.

## Commits (4)

| # | Hash    | Type              | Subject                                                              |
| - | ------- | ----------------- | -------------------------------------------------------------------- |
| 1 | ffdf172 | RED (Task 1)      | test(07-01): add failing tests for classify_room (CLASS-01, CLASS-02) |
| 2 | 86bb26c | GREEN (Task 2)    | feat(07-01): implement classify_room path-component helper (D-11)    |
| 3 | 9660eea | RED (Task 3)      | test(07-01): add failing tests for classifier_rooms config plumbing  |
| 4 | 6b8d566 | GREEN (Task 3)    | feat(07-01): add classifier_rooms config field + _NESTED_TABLES (D-14, D-15) |

## Verification

- `uv run pytest tests/test_classifier.py tests/test_config.py` → **22 passed** (13 classifier + 9 config)
- `uv run pytest` (full suite) → **387 passed** (no regressions on transcript_* / mcp_caps_*)
- `uv run ruff check src/supamem/config.py src/supamem/indexer/classifier.py tests/test_classifier.py tests/test_config.py` → **All checks passed**
- Manual sanity: `classify_room('src/components/Button.tsx', {'frontend':['components'],'backend':['src']})` → `'frontend'` ✓

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Test Bug] `test_go_cmd_layout_returns_none` plan-spec was incorrect**
- **Found during:** Task 2 (GREEN run revealed the test failed)
- **Issue:** Plan specified `classify_room("cmd/server/main.go", DEFAULT_ROOMS) is None`. But `server` IS in default backend keywords (`backend = ["src", "backend", "api", "server", "lib"]`), so the path correctly matches `backend`. The test mis-specified expected behavior.
- **Fix:** Changed test path to `cmd/myapp/main.go` — neither `cmd` nor `myapp` is a default keyword, so the test honestly demonstrates the Go-layout gap.
- **Files modified:** `tests/test_classifier.py` (one assertion + comment update)
- **Commit:** 86bb26c (combined with GREEN to keep TDD gate clean)

### Notes for Downstream Plans

**Filed for Plan 07-02:** A relevant insight was surfaced during execution about `json.dumps(obj, sort_keys=True)` for change-detection digests. The plan's pattern for `classifier_hash` (RESEARCH Pattern 4) currently specifies `sort_keys=True`. **However:** since `[classifier.rooms]` order itself encodes meaning (D-01a priority — first-match-wins), `sort_keys=True` decouples the hash from observable behavior — a user reordering rooms to flip priority would NOT trigger the re-classify sweep. **Recommendation for 07-02:** use `sort_keys=False` for `classifier_hash` so reordering invalidates the cache. This was NOT in scope for 07-01 (no manifest hash here yet) but is a critical correctness concern for 07-02.

## Notes

- **No `share/default.toml` modifications** — per RESEARCH surprise #1, the file does not exist in the repo; defaults live in `ResolvedConfig.field(default_factory=...)`. CONTEXT.md D-14's reference to `share/default.toml` is aspirational; the implementation correctly mirrors how Phase 5 (`mcp_caps_*`) and Phase 6 (`transcript_*`) shipped their defaults.
- **TDD gate compliance:** Both Task 2 and Task 3 followed RED → GREEN explicitly. Task 2's RED commit (ffdf172) failed with `ModuleNotFoundError`; Task 3's RED commit (9660eea) failed with `AttributeError` on `classifier_rooms`. GREEN commits (86bb26c, 6b8d566) made all tests pass.
- **No REFACTOR commits** — implementations were minimal and clean as written; no cleanup pass needed.
- **No CLAUDE.md drift** — no bare `print()`, no stdout writes from the classifier (pure function, no I/O), no hardcoded collection names.
- **Threat surface:** No new network endpoints, auth paths, or schema changes at trust boundaries beyond what was already in the plan's threat_model. Mitigations T-07-01-01..03 are satisfied by construction (pure function with set-intersection semantics).

## Self-Check: PASSED

- File `src/supamem/indexer/classifier.py` exists ✓
- File `tests/test_classifier.py` exists ✓
- File `src/supamem/config.py` modified (classifier_rooms field + ConfigChain + _NESTED_TABLES) ✓
- File `tests/test_config.py` modified (3 new tests) ✓
- Commit ffdf172 (RED1) exists ✓
- Commit 86bb26c (GREEN1) exists ✓
- Commit 9660eea (RED2) exists ✓
- Commit 6b8d566 (GREEN2) exists ✓
- All 13 classifier tests + 3 new config tests pass ✓
- Full pytest suite (387 tests) green ✓
- ruff clean on all touched files ✓
