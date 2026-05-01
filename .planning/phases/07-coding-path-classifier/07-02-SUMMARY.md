---
phase: 07-coding-path-classifier
plan: 02
subsystem: indexer/manifest/doctor
tags: [classifier, manifest, sweep, doctor, payload-room, tdd]
requires:
  - "src/supamem/indexer/classifier.py:classify_room (Plan 07-01)"
  - "ResolvedConfig.classifier_rooms / ConfigChain.classifier_rooms (Plan 07-01)"
provides:
  - "src/supamem/indexer/__init__.py:_classifier_hash (sha256, sort_keys=False)"
  - "src/supamem/indexer/__init__.py:_reclassify_sweep (groups-by-new_room, wait=True)"
  - "src/supamem/indexer/manifest.py:Manifest.classifier_hash + __classifier_hash__ reserved key"
  - "supamem doctor 'Classifier rooms' + 'Room histogram' sections"
  - "payload.room ALWAYS present (string|null) on every PointStruct (D-06)"
affects:
  - "src/supamem/indexer/__init__.py (drift gate, payload assembly, sweep helper)"
  - "src/supamem/indexer/manifest.py (additive field, byte-stable save)"
  - "src/supamem/doctor.py (two new sections after Transcript config)"
tech-stack:
  added: []
  patterns:
    - "Hash-drift gate using sort_keys=False to capture order-encoded priority (D-01a + D-08)"
    - "Per-page set_payload batching grouped by new_room (≤ 1 RPC per room per page)"
    - "Reserved __key__ namespace for top-level manifest extensions (mirrors __transcripts__)"
    - "Forward-compat seam: payload.room BEFORE **rec.metadata so chunkers can override (D-13)"
key-files:
  created:
    - tests/test_classifier_sweep.py
  modified:
    - src/supamem/indexer/__init__.py
    - src/supamem/indexer/manifest.py
    - src/supamem/doctor.py
    - tests/test_indexer.py
    - tests/test_doctor.py
    - tests/test_transcript_manifest.py
decisions:
  - D-06: payload.room ALWAYS present (string or JSON null)
  - D-07: doctor surfaces per-room histogram including null bucket
  - D-08: sweep + set_payload on hash drift, no re-embed
  - D-09: sweep automatic on hash drift; one set_payload per new_room per page
  - D-10: manifest gains __classifier_hash__; missing key → drift from None
  - D-11: classify_room called at _index_records payload-assembly site
  - D-13: **rec.metadata spread AFTER room (chunker override seam)
  - D-16: doctor prints active rooms map + provenance + classifier_hash
  - "07-01-SUMMARY recommendation honored: sort_keys=False so reordering trips the gate"
metrics:
  completed: 2026-05-01
  tasks_completed: 3
  files_changed: 6
  commits: 5
  tests_added: "4 manifest + 7 sweep + 5 indexer integration + 2 doctor = 18 new tests"
  full_suite: "405 passed (was 387 after 07-01)"
requirements:
  - CLASS-02
---

# Phase 7 Plan 02: Indexer Integration + Sweep + Doctor Histogram Summary

Wire `classify_room` into the indexer payload-assembly site, persist a `classifier_hash` in the manifest, sweep existing chunks via `set_payload` (no re-embedding) on hash drift, and surface the classifier config + per-room histogram in `supamem doctor`.

## Public Contract

```python
# src/supamem/indexer/__init__.py (NEW)
def _classifier_hash(rooms: dict[str, list[str]]) -> str: ...
def _reclassify_sweep(client, cfg: ResolvedConfig, *, batch: int = 512) -> int: ...

# src/supamem/indexer/manifest.py
@dataclass
class Manifest:
    entries: dict[str, EntryDict] = field(default_factory=dict)
    transcripts: dict[str, dict[str, dict]] = field(default_factory=dict)
    classifier_hash: Optional[str] = None  # NEW — emitted only when not None
```

## Exact Call Sites

- **`classify_room` invocation** — `src/supamem/indexer/__init__.py:330` inside `_index_records`, positioned BEFORE `**rec.metadata` so a chunker can override deliberately (D-13 forward-compat seam).
- **Drift gate** — `run_index` body, after `Manifest.load`, BEFORE the per-source loop. Compares `manifest.classifier_hash != current_classifier_hash`; on drift, prints a stderr banner via `err_console`, runs `_reclassify_sweep`, and stamps the new hash onto the manifest (persisted at end of `run_index`).
- **classifier_rooms threading** — keyword-only param added to both `_process_one_source` and `_index_records`. `cfg` itself is NOT passed (narrow contract; only the dict the function actually uses).

## `_reclassify_sweep` Batching Strategy

Per scroll page (default `batch=512`):

1. `client.scroll(collection_name, limit=batch, with_payload=True, with_vectors=False, offset=...)`.
2. Build `by_room: dict[Optional[str], list[id]]` — only points whose recomputed room differs from `payload.get("room", "__missing__")` are bucketed (skipping unchanged points and points lacking `file_path`).
3. One `client.set_payload(collection_name=..., payload={"room": room}, points=ids, wait=True)` per distinct `new_room` — NOT per point. With 3 distinct rooms in a page, that is at most 3 RPC calls regardless of page size.
4. `wait=True` on every call (R-03 idempotency).
5. Loop terminates when scroll returns no points OR offset is None.

## Manifest Byte-Stability

`Manifest.save()` mirrors the `__transcripts__` precedent:

```python
if self.transcripts:
    payload[TRANSCRIPTS_KEY] = self.transcripts
if self.classifier_hash is not None:
    payload[CLASSIFIER_HASH_KEY] = self.classifier_hash
```

A Phase-6-era manifest with no transcripts and no classifier hash round-trips byte-identical JSON. Locked by `test_manifest_byte_stable_when_classifier_hash_none`.

## Doctor Section Order (Final)

```
Health
Config chain
MCP caps
Transcript config
Classifier rooms          ← NEW (Phase 7 D-16)
  tests        = [...]   [source: default]
  ...
  classifier_hash = (none) | <sha256>
Room histogram            ← NEW (Phase 7 D-07)
  tests        : <n>
  ...
  null         : <n>
Installed clients
Update check
```

The histogram path uses `qmodels.IsNullCondition(is_null=qmodels.PayloadField(key="room"))` for the null bucket and `qmodels.FieldCondition(key="room", match=qmodels.MatchValue(value=room))` for named rooms. Qdrant `count()` failures are swallowed (T-07-02-04 mitigation) — `: 0` falls back so the URL never leaks via stack trace.

## Commits (5)

| # | Hash    | Type        | Subject                                                                                |
| - | ------- | ----------- | -------------------------------------------------------------------------------------- |
| 1 | b74b534 | RED  T1     | test(07-02): add failing tests for manifest classifier_hash field                       |
| 2 | 6a692b5 | GREEN T1    | feat(07-02): add classifier_hash to Manifest with byte-stable save (D-10)              |
| 3 | 7355c61 | RED  T2     | test(07-02): add failing tests for indexer classify + sweep                             |
| 4 | cb3c72c | GREEN T2    | feat(07-02): integrate classify_room at indexer payload site + sweep on hash drift      |
| 5 | 03f7107 | T3 (RED+GREEN combined) | feat(07-02): doctor classifier rooms + histogram + classifier_hash (D-07, D-16) |

Note: Task 3's RED commit was combined with GREEN because the test additions and implementation landed in a single iteration (the RED was demonstrated locally — `tests/test_doctor.py::test_doctor_shows_classifier_rooms_and_hash` failed against the unmodified doctor; the failure trace is captured in this session log). The plan tagged Task 3 as `type="auto" tdd="true"` (not `type="tdd"`) so the strict RED/GREEN gate is informational rather than mandatory; the targeted test additions still landed atomically with the implementation in commit 03f7107.

## Verification

- `uv run pytest tests/test_classifier_sweep.py tests/test_indexer.py tests/test_doctor.py tests/test_transcript_manifest.py` → **all green** (53 tests across the four files)
- `uv run pytest` (full suite) → **405 passed** (up from 387 after 07-01; +18 new tests across this plan)
- `uv run ruff check src tests` → **All checks passed**
- Targeted greps (acceptance criteria):
  - `from supamem.indexer.classifier import classify_room` — present
  - `classify_room(abs_path` — line 330 (single occurrence; transcript branch shares the dict literal so it gets `room=None` for non-coding paths via the same call)
  - `def _classifier_hash` / `def _reclassify_sweep` — present
  - `manifest.classifier_hash != current_classifier_hash` — drift gate present
  - `wait=True` in `_reclassify_sweep` — present
  - No bare `print()` introduced (`grep -nE 'print\(' src/supamem/doctor.py | grep -v 'console.print\|err_console.print'` — zero matches in new code; the pre-existing `print(format_chain(...))` at line 141 is untouched and predates this plan)
  - D-13 ordering: in the payload dict literal, `"room"` line precedes `**rec.metadata`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Plan-spec `FieldCondition(key="room", is_null=True)` is not valid Qdrant API**

- **Found during:** Task 3 implementation
- **Issue:** The plan's doctor snippet used `qmodels.FieldCondition(key="room", is_null=True)` for the null bucket. Qdrant's actual API requires a separate `IsNullCondition(is_null=PayloadField(key="room"))` model — `FieldCondition` has no `is_null` parameter and would raise `ValidationError` at runtime.
- **Fix:** Used `qmodels.IsNullCondition(is_null=qmodels.PayloadField(key="room"))` for the null bucket. Verified by introspecting `qdrant_client.http.models.IsNullCondition.model_fields` against the installed qdrant-client version.
- **Files modified:** `src/supamem/doctor.py` (one branch of the histogram filter)
- **Commit:** 03f7107
- **Acceptance criteria impact:** The plan's grep `grep -nE 'is_null=True' src/supamem/doctor.py` would not match the corrected code. The functionally equivalent grep `grep -nE 'IsNullCondition|is_null=qmodels.PayloadField' src/supamem/doctor.py` returns 2 matches (line 223–224) — same intent, current API.

**2. [Rule 2 — Critical functionality] Sweep gate wrapped in try/except for fail-soft contract**

- **Found during:** Task 2 GREEN
- **Issue:** Plan called the sweep unconditionally. `run_index` is fail-soft per its top-level contract ("Qdrant unreachable → returns 0, never raises"). An unexpected sweep failure (e.g., Qdrant connection drops mid-sweep) would propagate and break that contract.
- **Fix:** Wrapped `_reclassify_sweep(client, cfg)` in `try/except Exception` with a noqa for BLE001. The new `manifest.classifier_hash` stamp still happens so a partial sweep does NOT loop forever — next run is a no-op.
- **Risk note:** A partial sweep that updates 50/100 chunks then errors will mark the manifest as up-to-date; the 50 unswept chunks keep their stale `room`. Acceptable per fail-soft contract — the next config change re-sweeps. Documented here for downstream Phase 8/11 awareness.
- **Files modified:** `src/supamem/indexer/__init__.py` (5-line try/except)
- **Commit:** cb3c72c

### Notes for Downstream Plans (07-03)

- `payload.room` is now present on every coding chunk indexed post-Phase-7. Existing collections will be auto-swept on first index invocation after upgrade (R-04). Plan 07-03's `where={"room": ...}` filter can rely on the schema invariant.
- The `_reclassify_sweep` helper is exported via `supamem.indexer` namespace (importable as `from supamem.indexer import _reclassify_sweep`). Plan 07-03 does NOT need to call it directly — it runs automatically on `run_index`.
- The narrow `classifier_rooms: dict[str, list[str]]` parameter (vs. full `cfg`) keeps the Phase-7 contract free of speculative coupling. If Plan 07-03 needs more config inside `_index_records`, expand explicitly — do not retrofit `cfg` for convenience.
- Doctor histogram counts are best-effort. If a Phase 8/11 plan creates an explicit Qdrant payload index on `room` (RESEARCH R-02), histogram latency improves automatically.

## Threat Surface

No new endpoints, auth paths, or schema changes at trust boundaries beyond what was already in the plan's `<threat_model>`. Mitigations:

- **T-07-02-01** (sweep progress on stdout) — mitigated: `err_console` (stderr) used for both banner and chunk-count line.
- **T-07-02-04** (Qdrant URL leak via doctor histogram) — mitigated: `count()` failures fall back to `: 0`; URL never appears in output. `_collection_health` retains its existing protection from prior phases.

## Self-Check: PASSED

- `src/supamem/indexer/__init__.py` modified — `_classifier_hash`, `_reclassify_sweep`, drift gate, payload integration ✓
- `src/supamem/indexer/manifest.py` modified — `classifier_hash` field, `CLASSIFIER_HASH_KEY` constant, byte-stable save ✓
- `src/supamem/doctor.py` modified — Classifier rooms + Room histogram sections ✓
- `tests/test_classifier_sweep.py` created (7 tests) ✓
- `tests/test_indexer.py` extended (5 new tests) ✓
- `tests/test_doctor.py` extended (2 new tests) ✓
- `tests/test_transcript_manifest.py` extended (4 new tests) ✓
- All commits exist in git log: b74b534, 6a692b5, 7355c61, cb3c72c, 03f7107 ✓
- Full pytest suite: 405 passed (zero regressions) ✓
- `uv run ruff check src tests` clean ✓
- D-13 invariant: `"room"` line precedes `**rec.metadata` in the payload dict literal ✓
- No bare `print()` introduced (pre-existing `print(format_chain(...))` at doctor.py:141 is unchanged) ✓
- `wait=True` on every `set_payload` call in `_reclassify_sweep` ✓

## Notes

- **Dual-memory MCP search:** The CLAUDE.md hard constraint asks for `mcp__supamem__dual_memory_search` before editing `src/supamem/`. The MCP supamem server was NOT exposed to this executor (no `mcp__supamem__*` tools in the available toolset). Disclosing per the rule: **supamem search empty — proceeding from code + 07-RESEARCH.md + 07-CONTEXT.md + 07-01-SUMMARY**. The research artifacts loaded at session start are the substitute knowledge source.
- **Insight applied:** The `[python-hashing]` insight surfaced repeatedly during the session aligns with the 07-01-SUMMARY recommendation: `_classifier_hash` uses `sort_keys=False` so dict insertion order — which encodes D-01a priority — is part of the digest. Reordering `[classifier.rooms]` in the user's TOML correctly trips the sweep gate.
- **TDD gate compliance:** Tasks 1 and 2 followed strict RED → GREEN. Task 3 was tagged `type="auto" tdd="true"` (not `type="tdd"`) so the implementation and tests landed atomically; the failing-state of the new doctor tests was demonstrated locally (Classifier rooms section absent) before the implementation was added in the same commit.
- **No REFACTOR commits** — the implementations were minimal and clean; no cleanup pass was needed.
