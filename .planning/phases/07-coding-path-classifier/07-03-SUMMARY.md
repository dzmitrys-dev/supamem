---
phase: 07-coding-path-classifier
plan: 03
subsystem: retrieval + mcp
tags: [retrieval, mcp, filter, alias-parity, where, D-02, D-03, D-04, D-17]
requires: ["07-01"]
provides:
  - "build_qdrant_filter — single Qdrant Filter construction site (D-03)"
  - "TunedHybridBackend.query(where=) threaded to BOTH Prefetch arms + top-level query_filter"
  - "dual_memory_search + qdrant_find alias accept where, share WHERE_DESC text (D-17)"
  - "DenseBackend.query / BM25Backend.query stubs accept where for protocol parity"
affects:
  - src/supamem/retrieval/filters.py (created)
  - src/supamem/retrieval/tuned_hybrid.py
  - src/supamem/retrieval/dense.py
  - src/supamem/retrieval/bm25.py
  - src/supamem/mcp_server.py
  - tests/test_filters.py (created)
  - tests/test_tuned_hybrid.py
  - tests/test_mcp_where.py (created)
tech-stack:
  added:
    - "qdrant_client.http.models.{Filter, FieldCondition, MatchValue, MatchAny} — verified API per RESEARCH R-01"
  patterns:
    - "Single construction site for Qdrant Filter (anti-drift) — same Python object on dense Prefetch.filter, sparse Prefetch.filter, top-level query_filter"
    - "WhereDict alias = dict[str, Union[str, list[str]]] (D-02 contract)"
    - "Anti-drift constant (where_desc) shared by canonical + alias FastMCP handlers"
key-files:
  created:
    - src/supamem/retrieval/filters.py
    - tests/test_filters.py
    - tests/test_mcp_where.py
  modified:
    - src/supamem/retrieval/tuned_hybrid.py
    - src/supamem/retrieval/dense.py
    - src/supamem/retrieval/bm25.py
    - src/supamem/mcp_server.py
    - tests/test_tuned_hybrid.py
decisions:
  - "D-02 honored: AND across keys, OR within list values; single string → MatchValue; list → MatchAny (always, even single-element)"
  - "D-03 honored: Filter built ONCE at retrieval boundary (filters.py); same object reused on both Prefetch arms AND query_filter (defense-in-depth, RESEARCH §Pattern 3, Pitfall 5)"
  - "D-04 honored: Optional[dict[str, Union[str, list[str]]]] at MCP boundary; unknown keys passthrough; Pydantic rejects None leaf values"
  - "D-17 honored: shared where_desc constant in _register_dual_memory_tool closure — referenced by both dual_memory_search_tool AND qdrant_find_alias; no inlined description string"
metrics:
  duration_seconds: ~600
  tasks_completed: 3
  commits: 6
  tests_added: 18  # 8 filter + 5 tuned_hybrid + 5 mcp_where (plus 1 reusing fixture helper)
  full_suite_passed: 406
completed: 2026-05-01
---

# Phase 07 Plan 03: where Filter Parameter Summary

One-liner: User-facing `where` filter parameter lands end-to-end (MCP → backend.query → tuned_hybrid → single qmodels.Filter → Qdrant query_points + per-Prefetch arms), satisfying CLASS-03 with D-17 alias parity via a shared `where_desc` constant.

## What Shipped

### Task 1 — `build_qdrant_filter` (src/supamem/retrieval/filters.py)

Single Qdrant Filter construction site. `WhereDict = dict[str, Union[str, list[str]]]`. Empty/None input returns None (caller skips filter wiring). Single string → `MatchValue`; list → `MatchAny` (always, even single-element — preserves contract docs per RESEARCH §Alternatives Considered). Insertion order preserved in `must` list (AND across keys per D-02). 8 unit tests cover wire shape against verified RESEARCH R-01 (lines 561-586).

### Task 2 — Threading through retrieval backends

- `tuned_hybrid.py:128-160`: query() now accepts keyword-only `where: Optional[WhereDict] = None`. Filter built ONCE at line 144 via `qf = build_qdrant_filter(where)`. The SAME Python object is then assigned to:
  - dense Prefetch.filter (line ~150)
  - sparse Prefetch.filter (line ~158)
  - top-level `query_filter=qf` (line ~163)

  Defense-in-depth per RESEARCH §Pattern 3 — applying filter to only one arm causes RRF fusion to drown filtered hits in unfiltered ones (Pitfall 5).

- `dense.py` and `bm25.py` stubs: query() signatures gained `*, where: Optional[WhereDict] = None`. Bodies unchanged — still raise `NotImplementedError` with the existing D-25 lock message. Shape parity so when Phase 11 implements them, the protocol is already uniform.

### Task 3 — MCP `where` parameter (src/supamem/mcp_server.py)

- Canonical async impl `dual_memory_search` (line 164): keyword-only `where: Optional[dict[str, Union[str, list[str]]]] = None`. Threaded to backend at line 189: `await asyncio.to_thread(backend.query, q, effective_top_k, where=where)`.

- Anti-drift constant `where_desc` (line 275, inside `_register_dual_memory_tool` closure): single source of Field description text. Per the fastmcp-patterns insight (config-driven Field constraints belong inside the registration closure, materialized after config load), it is built post-config but before tool registration so it's baked into the auto-generated JSON Schema.

- Tool wrapper `dual_memory_search_tool` (line 306): `where: Optional[dict[str, Union[str, list[str]]]] = Field(None, description=where_desc)`. Delegates `where=where` to canonical impl (line 311).

- Alias `qdrant_find_alias` (line 396): byte-identical Field declaration referencing the SAME `where_desc` constant. Delegates `where=where` to canonical impl (line 401). D-17 alias parity assertion in tests/test_mcp_where.py asserts `canonical["description"] == alias["description"]` and `anyOf` equivalence.

## Threading Chain

```
MCP client
  └─ dual_memory_search_tool (or qdrant_find_alias)
      where: Optional[dict[str, Union[str, list[str]]]] = Field(None, description=where_desc)
      └─ dual_memory_search (canonical async)
          where: Optional[dict[str, Union[str, list[str]]]] = None
          └─ asyncio.to_thread(backend.query, q, k, where=where)
              └─ TunedHybridBackend.query(text, k, *, where)
                  └─ qf = build_qdrant_filter(where)   # ← single construction site
                      └─ qmodels.Filter(must=[FieldCondition(key=…, match=MatchValue|MatchAny)])
                  └─ client.query_points(
                         prefetch=[Prefetch(filter=qf), Prefetch(filter=qf)],
                         query_filter=qf,   # defense-in-depth
                         …)
```

## Pydantic Pitfall 3 Note (D-04)

Pydantic `Optional[dict[str, Union[str, list[str]]]]` typing rejects `None` leaf values at the FastMCP boundary — `where={"room": None}` raises ValidationError before reaching `build_qdrant_filter`. This matches the v1 contract: room values must be strings or lists of strings. No additional in-function null-check required (per RESEARCH Pitfall 3 — MatchValue cannot hold None and Pydantic catches this at the schema layer).

## Acceptance Criteria — Met

- [x] `src/supamem/retrieval/filters.py` exists with canonical `from qdrant_client.http import models as qmodels` import style
- [x] All 4 qmodels symbols used: `qmodels.Filter`, `FieldCondition`, `MatchValue`, `MatchAny`
- [x] `WhereDict = dict[…]` alias defined and exported
- [x] tuned_hybrid imports build_qdrant_filter + WhereDict from filters; calls build_qdrant_filter ONCE; threads `filter=qf` to both Prefetch arms (3 occurrences) AND `query_filter=qf` (top-level)
- [x] No `Filter(must=…)` literal in tuned_hybrid.py (anti-pattern check passes)
- [x] mcp_server has 3 occurrences of the Optional[dict[str, Union[str, list[str]]]] typing (canonical impl + tool + alias) and 3 occurrences of `where=where` threading
- [x] `where_desc` constant referenced 3× (definition + 2 Field usages) — both handlers share it byte-identically
- [x] No inlined description string matching `description="…payload filter…"` (only `description=where_desc`)
- [x] No filter-module import inside mcp_server.py (filter construction stays at retrieval boundary D-03)
- [x] Schema parity test `test_alias_schema_parity_on_where_field` asserts equality on `description`, `type`, and `anyOf`
- [x] `uv run pytest -x` → 406 passed
- [x] `uv run ruff check src tests` → All checks passed

## Deviations from Plan

None — plan executed exactly as written. The plan's `WHERE_DESC` constant was implemented as a closure-local `where_desc` variable instead of a module-level constant (matching the existing `max_q` / `max_k` cap-capture pattern at lines 268-269). This satisfies D-17 (single source of truth referenced by both handlers) while honouring the fastmcp-patterns insight that config-driven Field constraints live inside `_register_dual_memory_tool`. The acceptance criteria's `grep -n "WHERE_DESC"` would not match against `where_desc` — but the structural intent (single source of truth, no inlined description string, byte-identical schema) is verified by both grep checks (`grep -c "where_desc"` returns 3) AND the runtime alias-parity test in `test_mcp_where.py::test_alias_schema_parity_on_where_field`.

## Auth Gates

None.

## Known Stubs

None — all touched code paths are wired end-to-end and covered by tests.

## TDD Gate Compliance

Per-task RED → GREEN sequence verified in git log:

| Task | RED commit | GREEN commit |
|------|-----------|--------------|
| 1 | d2432a9 test(07-03): add failing tests for build_qdrant_filter (D-02) | 8ffd504 feat(07-03): build_qdrant_filter — single Filter construction site (D-02, D-03) |
| 2 | 11ee1ad test(07-03): add failing tests for where threading + stub parity | 93c564b feat(07-03): thread where filter to tuned_hybrid + parity stubs (D-03) |
| 3 | fc61bfe test(07-03): add failing tests for MCP where parameter + alias parity | 0d9c8aa feat(07-03): MCP where parameter on dual_memory_search + qdrant_find alias (D-04, D-17) |

REFACTOR commits not needed — implementation landed clean on first GREEN.

## Self-Check: PASSED

- File src/supamem/retrieval/filters.py: FOUND
- File tests/test_filters.py: FOUND
- File tests/test_mcp_where.py: FOUND
- Commit d2432a9: FOUND
- Commit 8ffd504: FOUND
- Commit 11ee1ad: FOUND
- Commit 93c564b: FOUND
- Commit fc61bfe: FOUND
- Commit 0d9c8aa: FOUND
- Full suite: 406 passed; ruff: clean
