# Changelog

All notable changes to `supamem` will be documented in this file.

## [0.4.0a2] — 2026-08-20 — Post-release field-report fixes: truthful doctor / config / install surfaces (Phase 19.1)

Phase 19.1 is a correctness-and-truthfulness pass driven by a field
report filed against 0.4.0a1 (findings SM-1…SM-9 plus release note
RN-1). Every fix removes a place where supamem told the user something
that was not true: a `✓` stamped on stale data, a `--dry-run` that
wrote state and mispredicted its own work, a `repair` that crashed on
the exact state it exists to heal, a `⚠` on a declared-optional extra,
a config key silently discarded, a `repair` round-trip that downgraded
a working config or clobbered a file another generator owns, and a
documented install command that resolved *backwards* to a release older
than the features the docs describe. No retrieval-stack code changed in
Phase 19.1 — every lever is in the config / installer / doctor / docs
layers, so recall / MRR / nDCG / latency bench metrics are
definitionally unchanged (coderag floors N/A).

### Fixed

- **SM-1 — unknown keys in `[supamem.eval]` (and every other config
  table) are no longer silently discarded.** A warn-only unknown-key
  diff now runs at the shared `_apply_section` / `_apply_nested` choke
  point, so every load path (`pyproject.toml`, `.supamem/config.toml`,
  `SUPAMEM_CONFIG`) surfaces typos on **stderr** naming the table, the
  unknown keys, and the accepted keys. Never raises — a typo must not
  break MCP stdio startup; stdout stays byte-empty. Cited Plan 19.1-03.
- **SM-2 — `doctor` can no longer stamp `✓` on a stale update-check
  cache.** `doctor_report` gained `stale` + `cache_age_seconds` as the
  single source of truth; explicit `doctor` runs attempt one bounded,
  suppression-honored, offline-safe `refresh_stale_cache` first. `✓` is
  now structurally unreachable from stale data — a stale cache renders
  a neutral info marker naming the cache age and last-seen version.
  Cited Plan 19.1-02.
- **SM-3 — a declared-optional extra is no longer reported at fault
  severity.** The coderag doctor panel forks on `importlib.util.find_spec`:
  optional-absent renders `→` with an install hint, and `⚠` is reserved
  for present-but-broken. Cited Plan 19.1-02.
- **SM-4 — upgrades no longer accumulate duplicate managed blocks.**
  New `config_io.sweep_managed_blocks` primitive merges duplicated
  `BEGIN/END SUPAMEM` fences into one canonical block at the target
  version, preserving user text verbatim and idempotent on re-run
  (byte-identical no-op on healthy input). Wired ahead of
  `extract_managed_block` at every managed-block call site in the
  `claude-code` and `opencode` installers; uninstall paths write a
  `.bak.<time_ns>` sibling before healing a user file. **SM-4d:**
  `doctor` now counts BEGIN markers per target and reports duplicate
  managed-block drift with repair advice. Cited Plans 19.1-01, 19.1-02.
- **SM-6 — duplicate managed blocks no longer crash `repair` and
  `uninstall`.** `extract_managed_block` keeps its strict multi-BEGIN
  raise (locked), but the healing layer above it sweeps first, so the
  recovery tool is no longer disabled by the state it exists to
  recover from. `--dry-run` inspection works on duplicated state too.
  Cited Plan 19.1-01.
- **SM-7 — `--dry-run` is now honored at every write site on the repair
  path, and its accounting is truthful.** `dry_run` is plumbed through
  uninstall (all three clients), the agent patcher, share-dir sync, and
  model pre-fetch; `would_write` counts derive from the same
  `WriteResult.diff` condition the real run uses, so prediction cannot
  diverge from reality (pinned by accounting-invariant tests on both
  the install and repair paths). The patcher still runs its **full
  detection** pass under dry-run and withholds only writes, so the
  would-patch count stays truthful. Console vocabulary fixed: no `✓`
  claims under dry-run. Cited Plan 19.1-04.
- **SM-8 — `repair` no longer replaces a working, explicit MCP config
  with a less robust one.** Client MCP entries (`claude-code` and
  `cursor`) are now emitted with a `shutil.which("supamem")`-resolved
  command (falling back to the bare name when `which` returns nothing)
  and pin `SUPAMEM_CONFIG` alongside `SUPAMEM_PROJECT_ROOT` whenever a
  project config exists. A repair round-trip can no longer downgrade
  config robustness. Cited Plan 19.1-04.
- **SM-9 — `repair` no longer writes into paths another generator
  owns.** Both cursor write sites (the `.mdc` whole-file copy and the
  `hooks.json` deep-merge) detect generator-managed destinations via a
  head-marker heuristic (`generated` / `do-not-edit` / `auto-generated`
  in the first ~10 lines) or a sibling `*.manifest.json` / `.manifest`,
  skip the write, and warn naming the file plus both remedies. Unmarked
  differing targets still update (the legitimate upgrade path is
  intact) but now warn visibly — detection never keys on content
  difference alone. Cited Plan 19.1-05.
- **SM-5 — the documented install command no longer resolves backwards
  to 0.2.0.** All five READMEs and `llms.txt` now teach explicitly
  pinned commands (`uv tool install 'supamem==0.4.0a2'`,
  `pipx install supamem==0.4.0a2`, `pip install supamem==0.4.0a2`).
  Because the newest **stable** release (0.2.0) predates the entire
  `0.3.x` / `0.4.x` pre-release line, an unpinned install resolved to a
  version older than every feature the docs describe — and
  `uv tool upgrade supamem` was a *downgrade*. An exact pre-release pin
  is a first-party requirement carrying a pre-release identifier, so it
  resolves with **no** `--prerelease` flag while dependencies stay
  stable; `--prerelease allow` is deliberately **not** documented
  because it applies to the whole resolution and was reproduced pulling
  `pydantic 2.14.0b1` into the tool environment. A new docs-drift guard
  locks the pin so future edits cannot silently unpin it. Cited Plan
  19.1-06.

### Added

- **`--force-cursor-rules`** on `supamem install` and `supamem repair`
  — escape hatch overriding *only* the SM-9 generated-marker skip. The
  overwrite-warning floor always prints, so force never degrades back
  to a silent clobber. Cited Plan 19.1-05.
- **Flat `regress_baseline_*` aliases accepted as `[supamem.eval]`
  keys** with chain source attribution — the flat names `doctor` prints
  are now valid config keys. Canonical spelling wins when both forms
  are present. Cited Plan 19.1-03.
- **Dual-scope help text** for the subagent patcher across
  `install` / `repair` / `init` and the `unpatch-agents` docstring:
  both `~/.claude/agents/` and `<project>/.claude/agents/` are named as
  patch targets (they were already both scanned; the help text lied by
  omission). Cited Plan 19.1-04.

### Release notes

- **Release-day installs may hit uv's stale index cache (RN-1).** If
  `uv tool install 'supamem==0.4.0a2'` reports that no such version
  exists while PyPI already serves it, add `--refresh`:

  ```bash
  uv tool install 'supamem==0.4.0a2' --refresh
  ```

  This is uv index-cache behavior, not a supamem bug.
- **`TextContent` is still the compact summary card** (0.4.0a1 behavior
  change, restated for anyone upgrading straight from 0.2.x):
  `structuredContent` remains the complete canonical payload.

## [0.4.0a1] — 2026-08-20 — MCP SDK 2.x migration + response token efficiency (Phase 19)

Phase 19 migrates the MCP server onto the official MCP SDK v2 server
class and halves measured tool-response tokens via single-arm
`CallToolResult` returns, with two new `[supamem.mcp]` config keys
(`response_format`, `cache_ttl_ms`) and an offline MCP-response token
instrument backing every number below. No retrieval-stack code changed
in Phase 19 — all levers are serialization/config-layer, so recall /
MRR / nDCG / latency bench metrics are definitionally unchanged
(coderag floors N/A: `git diff aedd10f..HEAD -- src/supamem/retrieval
src/supamem/eval/runner.py` is empty).

### Changed

- **Migration to the official MCP SDK v2 server class (`MCPServer`).**
  The dependency pin moves from the unbounded `mcp>=1.13` (which broke
  fresh installs by resolving mcp 2.x against v1-only import paths) to
  `mcp>=2,<3` (resolved: mcp 2.0.0; transitives httpx2 2.12.0,
  opentelemetry-api 1.44.0, mcp-types 2.0.0). Constructors are
  identity-only (`MCPServer("supamem")`); transport kwargs (`host`,
  `port`) move to `run(transport="streamable-http", ...)`. The pydantic
  floor is raised `>=2.5` → `>=2.12` (resolved 2.13.3). 2025-era
  clients keep working — v2 serves every earlier protocol revision.
  Cited Plans 19-01.
- **Behavior change — tool `TextContent` now carries the compact
  Markdown summary card** (🧠 search summary / write confirmation)
  instead of the full pretty-printed JSON the v1 SDK appended to every
  tool result. `structuredContent` remains the complete canonical
  payload (unchanged shape) — hosts or programmatic consumers relying
  on the old full-JSON `TextContent` arm must read `structuredContent`
  (RESEARCH Open Question 5 disclosure). Applies to all four
  registered tools (`dual_memory_search`, `dual_memory_write`,
  `qdrant_find`, `qdrant_store`). Cited Plan 19-03.
- **Measured response-token delta (Phase 19 instrument,
  `supamem.eval.mcp_response_tokens` — fixed 10-query workload over
  top_k=5 responses, 3 runs, deterministic corpus, p50 est. tokens):**
  total 3418 → 1712 (0.501×) by default; 1456 (0.426×) with
  `response_format = "concise"`; text arm 1728 → 22 (0.013×);
  structured arm byte-identical at 1690 (default mode). Baseline from
  19-BASELINE.json (pre-lever double-arm shape, sanity-banded per
  RESEARCH §2.2). Cited Plans 19-02, 19-03.

### Added

- **`[supamem.mcp] response_format = "concise" | "detailed"`** (default
  `"detailed"` — byte-identical responses; `"concise"` empties the
  display-only `preview` fields while every `Chunk.text` remains fully
  intact). Fail-closed `load_config` enum gate; ConfigChain mirror;
  shipped defaults block in `share/default.toml`. Cited Plan 19-03.
- **`[supamem.mcp] cache_ttl_ms`** (default `0` = off; negative values
  exit 2 at config load). When > 0, `tools/list` responses carry
  SEP-2549 cache hints (honored by 2026-era clients) via the SDK's
  per-method constructor map — `tools/call` results are never cached
  (the installed mcp 2.0.0 exposes no per-result stamping surface), so
  write-then-read visibility is always preserved. Cited Plan 19-03.
- **MCP-response token instrument** (`supamem.eval.mcp_response_tokens`
  module + `python -m` CLI): measures the serialized `CallToolResult`
  wire shape (both content arms) through the same SDK tool-manager
  layer hosts use, with a locked percentile contract (p50 = median,
  p95 = nearest-rank) and an injectable offline backend. Cited Plan
  19-02.
- **Unconditional `file_path` dedup on search results** — `file_path`
  is set to `null` when it duplicates `source` (the path survives in
  `source`); applies in both response_format modes (~2.6% smaller
  payloads). Cited Plan 19-03.

## [0.3.0a7] — 2026-05-11 — AST chunker + HyDE retrieval (opt-in plugins) + ADR §9 uplift (Phase 17)

Phase 17 ships two **opt-in** retrieval-stack plugins (AST chunker for
Python, HyDE-style query expansion via local Ollama) plus a chunk-level
recall metric, an Ollama warm-pool doctor panel, and ADR-0002 §9 — the
paired-bootstrap uplift comparison vs the Phase 16 baseline-3. **Defaults
are unchanged in the 0.3.x line.** Default-flip is gated on v0.4 per
**D-LAT-01** (the new HyDE retrieval violates the 5000 ms p95 hard
ceiling on 4/5 cells against the live corpus; AST chunker stays inside
the ceiling but the recall lift is modest).

### Added

- **`tree_sitter_code` AST chunker plugin (opt-in, Python only) (Req-02).**
  Registered under the existing `supamem.chunker` entry-point group
  alongside `markdown_header` and `transcript`:
  `tree_sitter_code = "supamem.indexer.chunker_tree_sitter:chunk_tree_sitter_python"`.
  Function-style entry mirrors `chunk_markdown`. Installed only when the
  user opts in via the new optional extra `pip install supamem[ast-chunker]`
  (`tree-sitter>=0.23,<0.26`, `tree-sitter-python>=0.23,<0.26`). Token
  budget enforced via `fastembed.TextEmbedding.token_count` (matches the
  MiniLM tokenizer used downstream). Parse errors fall back to
  `chunk_markdown` with an `err_console` warning per D-AST-03 — never
  raises into the indexer hot loop. When the extra is missing and a user
  has set `chunker = "tree_sitter_code"`, the lazy import raises a
  `RuntimeError` naming the fix command (`pip install supamem[ast-chunker]`)
  per D-PKG-02 — silent fallback would hide the misconfiguration. Cited
  Plan 17-B.
- **`tuned_hybrid_hyde` retrieval plugin (opt-in, Ollama-backed) (Req-03).**
  Registered under `supamem.retrieval` next to `tuned_hybrid` /
  `filtered_dense` / `dense` / `bm25`. Composition-over-inheritance —
  `self._inner = TunedHybridBackend(config=config)` keeps the
  `tuned_hybrid.py` backend byte-identical. Per query: POST to
  `<localhost-ollama>/api/generate` with the locked HyDE prompt
  (`Write a 3-5 sentence decision rationale that would answer this question, as if extracted from an ADR or code comment. Be specific and technical.` per D-HYDE-01),
  `keep_alive=-1` (warm-pool retention per D-HYDE-04, RESEARCH Pitfall 2),
  600 ms timeout with one retry, fall back to original query text on any
  failure (D-HYDE-03) and emit an `err_console` warning. Localhost-only
  guard reused from `supamem.eval.judge._resolve_ollama_host`
  (`SystemExit(2)` on non-localhost — inherits D-07). Latency telemetry
  via `stats.counter.bump("hyde", "hyde_latency_ms", 0, elapsed_ms)`.
  Cited Plan 17-C.
- **Chunk-level recall metric + `payload.chunk_id` envelope key (Req-01).**
  Coderag eval envelopes now carry `recall_at_1_chunk` / `recall_at_5_chunk`
  / `recall_at_10_chunk` / `recall_at_20_chunk` siblings beside the
  existing doc-level keys. `payload.chunk_id` is set ONLY by the coderag
  bench ingest path (`supamem.eval.coderag.ingest`) — deterministic
  `<rel_path>#<sha1(text)[:12]>`. New `_build_run_chunk` sibling does NOT
  dedup on duplicate doc_ids (Pitfall 4 — Phase 16's `_build_run`
  collapsed chunks of the same file to one row, hiding the very signal
  chunk-level recall is supposed to expose). Doc-level path stays
  byte-identical — Phase 16 floors test re-runs green (Req-06). Cited
  Plan 17-A.
- **Ollama warm-pool diagnostic panel in `supamem doctor` (Req-04 mitigation).**
  Fires only when `retrieval.backend = "tuned_hybrid_hyde"` is configured.
  Probes `/api/ps` with a 1s timeout; surfaces loaded model + load
  duration. Read-only — NEVER raises, NEVER flips the doctor exit code
  per D-DOCTOR-04. Same localhost guard as the HyDE retrieval backend.
  Cited Plan 17-D.
- **ADR-0002 §9 — "Phase 17 uplift comparison" (Req-07).** Three sibling
  sub-tables (`### default vs ast_on`, `### default vs hyde_on`,
  `### default vs ast_plus_hyde`) carrying paired-bootstrap deltas of
  the three Wave-3 LIVE envelopes against the Phase 16 baseline-3 LIVE
  envelope, parser-locked by `tests/test_adr_phase17_uplift.py` (mirrors
  the Phase 16 §8 ADR-as-test pattern). Reuses `paired_bootstrap_delta`
  unchanged. Cited Plan 17-H.
- **`--reingest-coderag` CLI flag on `supamem eval --suite coderag`
  (Req-09 / G5 wiring).** Default OFF — Phase 16 baseline byte-identical
  replay path preserved when the flag is absent. When ON: drops the
  `supamem_eval_coderag` collection and rebuilds it via the
  `supamem.chunker` entry-point keyed on `cfg.chunker` (e.g.
  `tree_sitter_code`) BEFORE scoring. The same flag also dispatches the
  retrieval backend by `cfg.retrieval` (e.g. `tuned_hybrid_hyde`) so a
  single CLI invocation drives both interventions end-to-end. Cited
  Plan 17-B2.
- **Three Wave-3 LIVE coderag envelopes (Req-05/06).** `17-E-LIVE.json`
  (AST-only), `17-F-LIVE.json` (HyDE-only), `17-G-LIVE.json` (AST + HyDE
  combined) — produced against the live 21,235-chunk corpus with
  `--reingest-coderag` and re-rank ON. Anchor the ADR §9 deltas.

### Changed

- **HyDE retrieval verdict — opt-in only; defaults UNCHANGED (D-LAT-01).**
  HyDE meets the Track B recall goal exactly at threshold
  (`decision_rationale.supamem_only.recall_at_1` 0.000 → 0.500) but
  **violates the D-LAT-01 hard ceiling on 4/5 cells** (max measured p95
  6069 ms on `decision_rationale.supamem_only`, vs the 5000 ms ceiling
  set in v0.3.0a6) AND the Req-04 per-cell budget on 5/5 cells (max
  delta +2270 ms). HyDE rewrites also produce a **−0.25 MRR regression
  on the `code_fact` axis** that users would feel — the one-size-fits-all
  prompt over-steers code-fact queries. HyDE stays opt-in only in
  v0.3.0a7; no default-flip path. Phase 18 follow-up — selectivity
  gating by axis. Cited Plans 17-F, 17-G, 17-H.
- **AST chunker verdict — opt-in only; defaults UNCHANGED.** AST chunker
  stays under the D-LAT-01 ceiling on all cells; modest recall lift
  (`code_fact.combined.recall_at_10` +0.005, `ndcg_at_10` +0.227;
  `decision_rationale.supamem_only.recall_at_10` +0.500 on small N).
  Default-flip is gated on v0.4 — 0.3.x defaults preserved so the
  Phase 16 byte-identical replay path stays the released-and-locked
  baseline. Cited Plan 17-E.
- **Combined (AST + HyDE) verdict.** Same latency violation as HyDE-only
  (the HyDE leg dominates the p95). Combined rescues `code_fact.combined`
  MRR vs HyDE-alone (back to 0.000 delta, no regression) but does NOT
  fix the hard-ceiling violation. Opt-in only. Cited Plan 17-G.

### Known caveats (disclosed honestly per supamem discipline)

- **ADR §9 paired-bootstrap CIs collapse to `[delta, delta]`.** The
  v1 LIVE envelope schema records per-cell means only — no
  `per_query.<axis>.<col>.<metric>` arrays. The §9 §8-shaped tables
  therefore call `paired_bootstrap_delta(samples_a, samples_b)` with
  constant-mean arrays of length `n = len(qrels)`, which produces an
  exact `delta` but a degenerate `[delta, delta]` CI. Qualitative tag
  (`win` / `tie` / `loss`) reads from the CI sign. **Delta values are
  exact; CI bounds do NOT reflect query-level uncertainty.** A future
  envelope-schema bump (preserve per-query arrays) unlocks real CIs
  through the same call site with no §9 structural change. Surfaced
  explicitly in §9's intro paragraph and `17-H-SUMMARY.md`.
- **`recall_at_*_chunk` null across 17-E/F/G.** Common gold-chunk
  derivation gap — Plan 17-A wired the envelope keys but the derivation
  either didn't fire or produced empty sets. §9 omits chunk-level rows;
  doc-level recall remains the gated signal per Req-06. Follow-up:
  investigate `coderag/runner.py` chunk-gold path before any future
  §9-style write-up.

### Internal

- **Phase 14 + 15 + 16 byte-identical regression locks preserved.**
  `_run_goldens_legacy` (D-VEND-04) and `src/supamem/retrieval/filters.py`
  (D-QGEN-06; `repo`, `axis`, `session_id` all remain pass-through with
  ZERO new branches) still byte-identical after every Phase 17 commit.
  The Phase 17 §9 append to `docs/adr/0002-coderag-eval-philosophy.md`
  leaves §§1-8 verbatim — the Phase 16 floors test (§7 + §8) still
  parses green.
- **New optional extra `[ast-chunker]`** is opt-in by construction —
  users who never set `chunker = "tree_sitter_code"` and never set
  `retrieval.backend = "tuned_hybrid_hyde"` see zero behavior change and
  pay zero import cost (lazy plugin discovery).
- **5-README lockstep + bumped `synced-with` SHA** per AGENTS.md.
  `tests/test_readme_translations_phase17.py` is the new sibling
  regression mirroring `_phase16.py`.

## [0.3.0a6] — 2026-05-09 — CodeRAG live numbers + mem0 head-to-head (Phase 16)

### Added

- **Auto-queries-from-manifest wiring in `--full` (Req-01).** `_run_coderag`
  in full mode now constructs records from
  `auto_queries.extract_pr_queries()` + `extract_adr_queries()` against
  the populated corpus manifest, NOT from `coderag_smoke.json`. The
  smoke fixture continues to drive the default offline path unchanged.
  Each record carries a `query_origin` field
  (`pr_title` / `adr_problem` / `adr_why`) and a
  `training_leakage_suspected` boolean — the latter flips `true` for
  any query whose source repo's pinned commit-SHA postdates the
  retrieval model's known training cutoff. Cited Plan 16-B.
- **`corpus.ensure_populated_manifest` lazy build-on-call (Req-02).**
  New idempotent orchestrator that reads the bundled placeholder
  manifest, fetches+walks repos at pinned SHAs, and writes the
  realized manifest (with content-SHAs) to
  `platformdirs.user_cache_dir("supamem") / "coderag" / "manifest.json"`.
  The bundled package manifest stays placeholder; the user-cache copy
  holds the realized version. Re-runs on an unchanged corpus are
  byte-identical no-ops. Cited Plan 16-A.
- **`metrics.paired_bootstrap_delta(samples_a, samples_b, n_resamples=10000, seed=42)`
  (Req-04).** Pure-stdlib paired-bootstrap with percentile CI — no
  scipy dependency. Sign convention `<peer>_vs_supamem`: positive
  delta = peer wins (mem0 better than supamem on this cell). 95% CI
  by default. Identical sample arrays produce delta=0 with CI bracketing
  zero; divergent samples produce a delta whose sign matches the mean
  delta and whose CI does not bracket zero at >95% confidence. Cited
  Plan 16-C.
- **Mem0 head-to-head row with paired-bootstrap CI delta (Req-04).**
  `report.py` adds peer-row scoring at the envelope-builder boundary
  — calls `mem0_adapter.query()` per record, scores against the same
  gold IDs via `pytrec_eval`, and writes results under
  `envelope.peers.mem0.scores` AND
  `envelope.comparisons.mem0_vs_supamem` with the
  paired-bootstrap delta + 95% CI per axis × column × metric. Cited
  Plans 16-C, 16-D, 16-F.

### Changed

- **ADR-0002 §7 rewritten with live three-run variance-gated floors
  (Req-03).** Phase 15's offline floors (`recall_at_5 = 1.000` on the
  trivially-recovered 6-question smoke; latency `< 0.005 ms` from
  deterministic dict lookup) are removed. New floors derived from
  `mean(LIVE_1, LIVE_2, LIVE_3) − ε_ranking` and
  `mean(...) + ε_latency` per axis × column cell against the populated
  21,235-chunk corpus. ε per §4 (`ε_ranking = max(stddev, 0.005)`,
  `ε_latency = max(0.05·mean, 5ms)`). The hard latency p95 ceiling
  moves **500 ms → 5000 ms** per **D-LAT-01** as a one-shot
  forward-looking adjustment (max measured live p95 = 4593.35 ms on
  decision_rationale.supamem_only sat at ~92% of 500 ms × 10) — NOT a
  sliding scale; subsequent phases tighten or hold, never relax. Cited
  Plan 16-F.
- **ADR-0002 §8 (NEW) — "Mem0 peer comparison" (Req-04).** Live
  head-to-head against mem0 default-config (`mem0ai==2.0.1`,
  HuggingFace `all-MiniLM-L6-v2`, `infer=False`) — 4 markdown tables
  (code_fact × {supamem_only, fastapi_only, combined} +
  decision_rationale × supamem_only) with
  `metric / supamem / mem0 / delta / ci_lower / ci_upper / qualitative`
  columns. Sign convention: positive delta = mem0 wins. Aggregate
  Phase 16-E tally: 9 wins / 21 ties / 0 losses across 30 cells; mem0
  wins concentrate on the recall@k tail (k ∈ {10, 20}) under the
  chunker-granularity caveat (mem0 ingested 2147 finer-grained
  records; `_build_run` dedups by doc_id, so more chunks ⇒ more shots
  per query — surfaced explicitly in §8 prose). Reproducibility footer
  pins `n_resamples=10000`, `seed=42`, supamem SHA, mem0 SDK version,
  and the ROCm GPU rerank stack used to capture the numbers. Cited
  Plans 16-C, 16-D, 16-F.
- **Schema-compat: `peers` and `comparisons` are always-present dicts
  (D-PEER-03).** Non-`--peer` envelopes emit `peers: {}` AND
  `comparisons: {}` (empty dicts, NOT absent keys) so downstream
  consumers can safely `envelope["peers"].get("mem0")` without a
  KeyError. Backward-compatible with v0.3.0a5 envelopes that simply
  omitted these keys.

### Internal

- **Phase 14 + Phase 15 byte-identical regression locks preserved
  unchanged (Req-05).** `_run_goldens_legacy` (D-VEND-04) and
  `src/supamem/retrieval/filters.py` (D-QGEN-06 — `repo` and `axis`
  remain pass-through keys with ZERO new branches in the filter
  dispatcher) — both still byte-identical after every Phase 16 commit.
- The Phase 16 ADR rewrite ships the public artefact under the
  filename `docs/adr/0002-coderag-eval-philosophy.md` (post-Phase-15
  rename); §1..§7 unchanged in position, §8 is purely additive (no
  tail renumbering required). ADR-0001 cross-link audit confirmed no
  §N references into 0002.

## [0.3.0a5] — 2026-05-07 — coderag eval suite (Phase 15)

### Added

- **coderag eval suite.** New `supamem.eval` plugin entry-point group
  with the first registered suite, `coderag`. Deterministic two-repo
  haystack (`supamem` self + `fastapi` external; both pinned to
  commit-SHAs via `src/supamem/eval/datasets/coderag_corpus_manifest.json`
  — never tag, never track-main). Two query axes: `code_fact`
  (PR-derived queries with file-modification gold) and
  `decision_rationale` (ADR Problem/Why-derived queries; supamem-only
  per A-D-HAY-04 — fastapi has no `docs/adr/` at the v1 corpus pin).
  **Three-column metric reporting** (`supamem_only` / `fastapi_only` /
  `combined`) per axis makes self-reference circularity audit-visible.
  See [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md).
- **`supamem eval --suite coderag [--full] [--out PATH] [--peer mem0]`**
  CLI surface. `--full` runs against the full pinned corpus; `--peer
  mem0` adds a parallel mem0 row in the metric envelope.
- **mem0 peer adapter** (`peers-mem0` extras: `mem0ai>=2.0,<3.0`).
  Single canonical default config; ingests source documents into its
  OWN Qdrant collection (`supamem_eval_coderag_mem0` — separate from
  `supamem_eval_coderag` per A-D-DEF-02 / Pitfall 7: mem0 owns its
  schema). Reported as a parallel row, never a gate.
- **`pytrec_eval>=0.5`** added to the `eval` extras for canonical IR
  metric scoring (Recall@k, MRR, nDCG@10).
- **`supamem doctor`** coderag panel (read-only) surfaces
  cache/manifest presence and the resolved bench-collection name.
- **Bundled `coderag_smoke.json`** (≤200 KB; 6 questions across both
  axes) for offline PR-CI — no live Qdrant or network required.
- **Validation invariants INV-01..10 + INV-A1..A3** enforced in
  `tests/test_coderag_invariants.py`. INV-A1 collapses
  `decision_rationale.combined` to `supamem_only` when
  `fastapi_only is null` (single locus at the envelope-builder
  boundary). INV-A3 (this release) verifies the REQUIREMENTS.md edits.
- **Three-run baseline + ε derivation rule** (Plan 15-C):
  `ε_ranking = max(stddev, 0.005)`; `ε_latency = max(0.05 × mean, 5ms)`;
  hard latency p95 ceiling 500 ms. Locked numerical floors live in
  ADR-0002 §7.

### Changed

- **LongMemEval demoted** to on-demand-only for full runs. The
  5-question `longmemeval_scoped_smoke` fixture (Phase 14) stays on
  PR-CI; full LongMemEval_S no longer gates releases. The Phase 13
  ship gate moves to `supamem eval --suite coderag --full`
  no-regression vs measured baseline. See
  [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md). The diagnosis:
  LongMemEval measures conversational long-term memory while supamem
  indexes code chunks for AI coding agents — the gate was workload-
  misaligned, not the tool.
- **REQUIREMENTS.md edits per A-D-DOCS-01.** PUB-05 rewritten to gate
  on `supamem eval --suite coderag --full` no-regression vs measured
  baseline (Recall@k, MRR, nDCG@10 ≥ baseline − ε; latency p95 ≤
  baseline + ε AND ≤ 500ms). EVAL-05 marked DEMOTED with reference to
  ADR-0002. Original wording preserved for traceability.

### Internal

- **Byte-identical regression locks preserved.** Phase 14
  `_run_goldens_legacy` (D-VEND-04) and `src/supamem/retrieval/filters.py`
  (D-QGEN-06 — `repo` and `axis` are pass-through keys; ZERO new
  branches in the filter dispatcher) — both still byte-identical
  after Phase 15 edits.
- New plugin entry-point group `supamem.eval` mirrors the four
  existing groups (retrieval / embedder / chunker / reranker); third
  parties can register additional suites without forking.

## [0.3.0a4] — 2026-05-04 — Bench harness where-filter pass (Phase 14)

### Added

- **Scoped/unscoped bench passes.** `supamem eval --suite longmemeval_s`
  now emits BOTH an unscoped and a scoped retrieval pass per question at
  the single `runner.py:428` call site (`_run_longmemeval` per-record
  loop). The scoped pass derives a per-question `where` filter from
  LongMemEval haystack session ids (`{"session_id": [list]}`),
  exercising Phase 7 / 9 / 11 / 14 indexer-side filter payloads end-to-end.
  Smoke vs full continues to be gated by the existing `smoke_ids` filter
  inside the same loop — no second physical call site.
- **Bench-only LongMemEval ingestion.** New module
  `supamem.eval.longmemeval_ingest` builds an isolated
  `supamem_eval_longmemeval_s` collection, attaches `payload.session_id`
  to each haystack chunk, and creates a `session_id` keyword payload
  index at first ingestion (idempotent). Production indexer paths
  (markdown, transcript) are unchanged. The `session_id` payload field
  is **bench-only** — `supamem index` does NOT set it.
- **Bundled smoke fixture.** New static fixture at
  `src/supamem/eval/datasets/longmemeval_scoped_smoke.json` (≤5 questions,
  ≤200 KB, self-contained — does not trigger the ~3 GB lazy fetch). New
  suite name `longmemeval_scoped_smoke` for the CI fast-path; `suite_loader`
  dispatches to the bundled fixture for that suite.
- **ADR-0001** — `docs/adr/0001-scoped-only-bench-gate.md` records the
  methodology, the v0.1.5 corpus mismatch disclosure (D-GATE-05), and
  the strict isolation from FUTURE-24 (rerank composition rework) per
  D-FUT24-01..03. New `docs/adr/` directory established with a
  convention note (`docs/adr/README.md`).

### Changed

- **Result JSON shape.** `scores` and `by_axis` now carry `unscoped` +
  `scoped` sibling sub-dicts. `_compute_main_score` for the
  `longmemeval_s` suite reads `scores.scoped.tokens_per_correct_answer`
  for the Phase 13 gate decision. Unscoped is reported in the same
  envelope for transparency only — it never gates. Legacy callers
  (goldens etc.) continue to see the flat shape (sibling-key envelope
  contract pinned by `tests/test_build_report.py`).
- **Gate decision is scoped-only.** The Phase 13 publication gate
  (`baseline_delta.tokens_per_correct_answer ≤ -0.30`) now reads
  `scores.scoped.tokens_per_correct_answer` against v0.1.5. Unscoped
  numbers ship in the same envelope but never gate. See ADR-0001.

### Migration

- **v0.1.5 baseline re-captured.** `eval/baselines/v0.1.5.json` carries
  both `unscoped` and `scoped` sibling keys plus a legacy mirror at
  top-level for migration safety. The original devdocs-collection
  number (`1374.59`) is preserved as `legacy_devdocs_unscoped_tpca` but
  does NOT gate; v0.1.5 was re-captured against the new haystack
  collection. **Absolute pre-Phase-14 numbers are not directly
  comparable to post-Phase-14 numbers — the corpus changed.** See
  ADR-0001 for the disclosure.

### Cross-references

- **FUTURE-24** (rerank composition rework) — Phase 14's scoped pass
  runs with rerank-OFF so the measured scoped-vs-unscoped delta
  attributes cleanly to scoping. FUTURE-24 is a SIBLING unblocker
  tracked separately. Public claims about scoping gains do NOT
  extrapolate to assume FUTURE-24 will further close the gap (D-FUT24-03).

### Locks preserved

- `runner.py:157` (`_run_goldens_legacy`, v0.1.x regression infra) is
  **byte-identical** (D-VEND-04 lock). Plan B touched only
  `runner.py:428`.
- `retrieval/filters.py` is **byte-identical**. `session_id` flows
  through Phase 11's existing pass-through path (key-name =
  payload-key-name); not a magic key. Zero new branches.

## [0.3.0a3] — 2026-05-03 — Filtered retrieval backend (FILT-01) + anti-identity-tier lock (FILT-02)

### Added

- New `filtered_dense` retrieval backend (FILT-01) — scoped+capped
  wrapper around `tuned_hybrid` that accepts a `where` filter
  (`room`, `path_prefix`, `valid_to`) and caps each hit's preview at
  a configurable char limit. Registered via the existing
  `supamem.retrieval` entry-point group; existing backends
  (`tuned_hybrid`, `dense`, `bm25`) are unchanged.
- New `path_prefix` magic key in the MCP `where` parameter — string
  or list of strings; left-anchored exact path-segment match against
  the new `payload.path_prefixes: list[str]` payload field. Indexer
  builds the prefix list per chunk
  (`src/supamem/retrieval/filters.py` → `["src", "src/supamem", ...]`)
  and creates a `KeywordIndex` (`on_disk=True`) at collection init,
  mirroring Phase 7 `room`.
- `valid_to: "now"` accepted as a no-op alias for the always-on
  temporal clause from Phase 9; any other value raises `ValueError`
  referencing the always-on lock (time-travel queries are out of
  scope).
- New config: `[retrieval.filtered_dense] preview_chars = 240`
  (default 240; `0` disables truncation entirely so `preview` becomes
  the full document text). Independent of the MCP transport cap
  `mcp.caps.max_preview_chars`, which continues to apply on top.
- New `supamem doctor` panel "Filtered-dense backend" — surfaces
  resolved `preview_chars` with `[source: ...]` provenance. Read-only
  by construction; never flips the doctor exit code.

### Changed

- `mcp_server` retrieval-tool `query` Pydantic `Field` tightened to
  `Field(..., min_length=1, max_length=max_q)` at both sites
  (canonical `dual_memory_search_tool` and `qdrant_find_alias`,
  D-NOID-01.c). The JSON Schema now requires a non-empty `query`
  string at the schema layer — defense-in-depth alongside the
  preserved runtime `.strip()` check.

### Migration

- Legacy chunks lack `path_prefixes`. First post-upgrade
  `supamem index` runs a one-shot eager scroll-and-`set_payload`
  sweep that back-fills `path_prefixes` per chunk — pure metadata
  update, **zero re-embedding cost**, and idempotent on subsequent
  runs. No `--force` reindex required. Mirrors the Phase 7 D-08
  classifier-hash sweep precedent.

### Anti-feature lock (FILT-02)

- supamem does NOT auto-inject identity / wake-up / prelude context
  into agent calls — retrieval is always solicited via an explicit
  query. Locked by `tests/test_no_identity_tier.py`: a CI-enforced
  regression test that fails if a future MCP tool name matches
  `(?i)(wake[_-]?up|identity|prelude|inject)` OR if any retrieval
  tool's JSON Schema drops `query` from `required` / loses
  `minLength >= 1`.

## [0.3.0a2] — 2026-05-03 — Bench harness (LongMemEval + RAGAS)

### Added

- `supamem eval --suite longmemeval_s` — lazy-fetches LongMemEval_S
  from a pinned HF revision and runs the supamem retrieval pipeline
  through a heuristic/Ollama judge, emitting an MTEB-style JSON
  envelope to `~/.supamem/eval/<utc-iso>.json`.
- `supamem eval --suite goldens` — extends the existing v0.1.x bundled
  regression baseline to the new envelope shape. Backward-compat:
  `supamem eval --regress` continues to behave exactly as v0.1.5.
- New optional extra: `pip install supamem[eval]` brings in
  `ragas==0.4.*`, `datasets`, and pins `huggingface_hub>=0.24` for the
  RAGAS triad metrics. Core install stays lean — RAGAS is fail-soft on
  missing extra (heuristic-only metrics + `err_console` install hint).
- Two-tier judge: heuristic (default, offline, fastembed-backed) or
  `EVAL_JUDGE_MODEL=ollama:<model>` / `--judge ollama:<model>` for
  localhost Ollama. SaaS endpoints (openai/anthropic/cohere/mistral)
  are explicitly refused per the D-07 invariant
  (`assert_no_saas_llm_env()`).
- CI fast-path: 10-question axis-stratified seeded subset frozen at
  `tests/eval/smoke_ids.json`. Full ~500 QA run gated behind `--full`.
- `supamem doctor` gains an "Eval bench" panel showing dataset SHA
  drift vs the pinned revision, cache size, last-run timestamp, RAGAS
  extra availability, and active baseline file. Read-only — never
  flips the doctor exit code.
- `supamem eval --list-suites` for discoverability.

### Notes

- Phase 13 (Publish & Compare) is gated on `--full` validation
  reporting `tokens_per_correct_answer` ≥30% reduction vs the v0.1.5
  baseline (`src/supamem/eval/baselines/v0.1.5.json`, ships with
  `_baseline_pending: true`). **No measured numbers are claimed in
  this release** — this release ships the harness only. The PyPI tag
  `v0.3.0a2` is held until either the gate clears or the user
  explicitly chooses to ship the harness without measured claims.

## [0.3.0a1] — 2026-05-02 (Phase 9: Per-Source Temporal Validity)

First alpha of the v0.3 line. Ships **per-source temporal validity** —
every indexed chunk now carries `payload.valid_from` (= source mtime)
and `payload.valid_to = null`; re-indexing a CHANGED file scrolls and
`set_payload(valid_to=now())`s prior chunks atomically BEFORE upserting
new content-hash-keyed chunks (old + new coexist in Qdrant per TEMP-01).
A single always-on retrieval-time filter (`IsEmptyCondition` on
`valid_to`, NOT `IsNullCondition` — see Qdrant#5342) removes superseded
chunks from every backend uniformly.

### Added

- **Per-source temporal validity** (TEMP-01, TEMP-02): every indexed
  chunk carries `payload.valid_from` (source mtime) and
  `payload.valid_to = null` by default. Re-indexing a CHANGED file
  atomically scrolls + `set_payload(valid_to=now())`s the prior chunks
  BEFORE upserting new content-hash-keyed chunks (`_chunk_id` extended
  with `content_hash` per D-CID-01). Old + new chunks coexist in
  Qdrant — TEMP-01 literal compliance. Auto-GC at end of
  `supamem index` deletes superseded chunks past
  `[retrieval.temporal] retention_days = 90` (default). Set
  `retention_days = 0` for kept-forever (compliance / audit)
  collections.
- **Always-on retrieval temporal filter** (TEMP-02):
  `retrieval/filters.py:build_qdrant_filter` always emits a
  `valid_to IS missing/null OR valid_to > now()` clause. Single
  construction site (Phase 7 D-03) — all backends (`tuned_hybrid` both
  Prefetch arms, `dense`, `bm25`, `qdrant_find`,
  `dual_memory_search`) inherit it. Uses `IsEmptyCondition` (NOT
  `IsNullCondition` — Qdrant#5342: `IsNull` does not match missing
  fields).
- **Transcript-only opt-in recency decay** (TEMP-03):
  `[retrieval.recency.per_source.transcript]` table with
  `enabled = false` default, `half_life_days = 14.0`, `alpha = 0.7`.
  When enabled, transcripts get a post-rerank multiplicative-floor
  decay `score *= alpha + (1 - alpha) * 0.5 ** (age_days / half_life_days)`.
  Code / ADR / doc / null-room rankings remain byte-identical when
  the knob is flipped — orthogonal pass after rerank-or-RRF, before
  T-5 dedup.
- **Doctor Temporal-validity panel**: `supamem doctor` between
  Reranker and Subagent reachability panels — live / superseded /
  awaiting_gc / future_dated counts, per-source breakdown, oldest +
  newest `valid_from`, `retention_days` provenance, validity-migration
  status. Read-only — never flips exit code.
- **Eager validity migration** (D-NULL-03): first post-upgrade
  `supamem index` back-fills `valid_to=null` on legacy points (gated
  by manifest `__validity_migration__` reserved key, idempotent on
  subsequent runs). Defense-in-depth alongside the IsEmpty runtime
  filter.
- **Payload indexes**: idempotent `create_payload_index` on
  `valid_to` (DATETIME) and `chunker` (KEYWORD) at `run_index` boot —
  sub-ms range queries on large collections (D-INDEX-01, D-INDEX-02).

### Changed

- **`_chunk_id`** signature now takes `content_hash` — unchanged
  content is idempotent under re-index; changed content gets a fresh
  uuid so old + new coexist in Qdrant.
- **Default retention is destructive** for users upgrading from v0.2.x
  with audit-mode collections older than 90 days. Set
  `[retrieval.temporal] retention_days = 0` to disable auto-GC
  entirely (kept-forever escape hatch).

### Notes on public claims

Per the v0.2.1 milestone gate, the "−30% tokens-per-correct-answer"
claim is BLOCKED until Phase 10 (LongMemEval_S + RAGAS bench) validates
the number. Phase 9 ships the temporal-validity infrastructure feeding
into that measurement; no public benchmark claim accompanies this
release.

### References

- Decay-shape rationale: Customers.ai recency-weighted scoring +
  Snowflake Cortex Search docs (multiplicative-floor decay for
  uncalibrated cross-encoder scores).
- Qdrant API behavior: filtering docs (IsEmpty vs IsNull semantics,
  DatetimeRange RFC 3339), payload index (DATETIME schema), point
  delete (PointIdsList scroll-then-batch).

## [0.2.5a1] — 2026-05-02

First alpha of the v0.2.5 line. Ships the **subagent reachability
auto-patcher** (Phase 8.1) — closes the silent dogfooding gap where
subagents shipping with restrictive `tools:` whitelists (GSD's
`gsd-executor`, superpowers/*, hookify, etc.) could not reach the
supamem MCP server, so dual-memory lookups inside subagent sessions
silently returned empty.

### Added

- Subagent reachability auto-patcher: `supamem install` and
  `supamem repair` now scan `~/.claude/agents/` AND
  `<project>/.claude/agents/` and idempotently append
  `mcp__supamem__*` to any restrictive `tools:` whitelist that doesn't
  already cover supamem. Files with a missing or empty `tools:` line
  inherit all parent tools (Claude Code semantics) and are left
  untouched. Symlinked agent files are skipped with a warning to avoid
  polluting upstream repos. (REACH-01..03)
- `supamem unpatch-agents` subcommand restores patched agent files
  cleanly. Run BEFORE `pip uninstall supamem` for a clean uninstall.
  Skips files the user has edited since the patch (frontmatter SHA
  match) and emits a per-file warning naming them. (REACH-05)
- `--skip-patch-agents` flag on `install` / `init` / `repair` for
  users who manage agent whitelists by hand. (REACH-07)
- `supamem doctor` `Subagent reachability` panel: per-agent listing
  (patched / OK already-covered / OK full-inheritance / skipped /
  needs-patching), grouped by `[global]` and `[project]` scope.
  Renders the manifest path + an `unpatch-agents` reminder when a
  manifest exists, or a `supamem repair` hint when patchable agents
  are detected without a manifest. Read-only by construction; never
  flips the doctor exit code. (REACH-08)
- Backup manifest at
  `platformdirs.user_cache_dir("supamem")/agent_patches.json` —
  single rolling JSON, FileLock-protected, atomic temp+rename writes.
  Per-entry: file path (relative to scope root), original frontmatter
  SHA-256 (newline-normalized), patched frontmatter SHA, original
  `tools:` value (verbatim), timestamp, supamem version. (REACH-06)

### Changed

- `src/supamem/share/rules/dual-memory.md` rewritten to reference the
  real MCP tool names (`mcp__supamem__qdrant_find`,
  `mcp__supamem__dual_memory_search`) instead of the non-existent
  `qdrant-find` shell command the rule used to advertise (D-LOCK-07).
  Adds a `Subagent reachability` section explaining the auto-patcher,
  the `--skip-patch-agents` opt-out, the `supamem unpatch-agents`
  reverse path, and the two-step uninstall contract.

### Dependencies

- Added: `ruamel.yaml>=0.18,<0.20` (~112 kB pure-Python wheel on
  Py3.12+) for round-trip-preserving YAML mutations on agent
  frontmatter — preserves user comments, indentation, and CSV vs
  list-style formatting. (REACH-04)

### Notes — Uninstalling

There is no portable `pip uninstall` hook in pip / uv / pipx
(verified 2026-05-02), so reversibility is a documented two-step
contract:

```bash
supamem unpatch-agents      # restore agent whitelists first
pip uninstall supamem       # then remove the package
```

`supamem doctor` displays the manifest path and the reminder so users
discover this flow naturally without consulting docs.

## [0.2.4a1] — 2026-05-01

First alpha of the v0.2.4 line. Ships the **code-aware reranker**
(Phase 8 of the v0.2.0 milestone train) — every `tuned_hybrid` query
now rescores RRF-fused candidates through a cross-encoder by default.

### Added

- Code-aware cross-encoder reranker: `mxbai-rerank-base-v2` (Apache-2.0)
  plugged into `tuned_hybrid` retrieval as the new default
  (`retrieval.reranker = "mxbai_v2"`). Setting
  `retrieval.reranker = "off"` restores pre-Phase-8 byte-identical
  behavior. (Phase 8, RERANK-01..04)
- New `supamem.reranker` plugin entry-point group — third parties
  register custom rerankers without forking. Registered default:
  `mxbai_v2 = supamem.rerankers.mxbai_v2:MxbaiV2Reranker`. (RERANK-03)
- `supamem install` and `supamem init` proactively download all ML
  prerequisites (MiniLM ~90 MB + BM25 ~10 MB + mxbai-rerank-base-v2
  ~1 GB) with `rich.progress`. Cold post-install CLI invocations
  (`supamem --help`, `supamem doctor`, `supamem --version`) trigger
  zero network egress. (RERANK-02)
- `supamem install --skip-models` opt-out flag for air-gapped
  first-run; backfill via `supamem repair`. (D-FETCH-07)
- `supamem repair` extended to doctor-driven self-heal: re-fetches
  missing/partial reranker model, re-syncs `share/`, repairs managed
  CLAUDE.md/AGENTS.md blocks, restores client config. Idempotent.
  (D-FETCH-03)
- `supamem doctor` Reranker panel: name, model_id, cache path,
  on-disk size + partial-download detection, last-load latency,
  last-100-query rerank p50/p95, detected device (cuda/mps/cpu).
  (RERANK-04, D-DOCTOR-01)
- New env vars: `SUPAMEM_CACHE_DIR` (override platformdirs cache root),
  `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` (respected by
  `prepare()`), `SUPAMEM_INTEGRATION_RERANKER=1` (opt-in integration
  test gate).

### Changed

- `RetrievedChunk` gains optional `rerank_score: float | None` field
  for telemetry; primary `score` carries the rerank score when
  reranker is on. (D-CONTRACT-05)
- When reranker is on: PREFETCH_LIMIT widens to 50 per arm; T-4
  recency multiplier is skipped; T-5 dedup + T-8 token budget run
  AFTER rerank. (D-COMPOSE-01..03, D-POOL-01..04)

### Dependencies

- Added: `mxbai-rerank>=0.1.6,<0.2`, `huggingface_hub>=0.24`,
  `filelock>=3.13`. Pulls `transformers>=4.49`, `torch>=2.0`,
  `accelerate>=1.5` transitively.

## [0.2.3a1] — 2026-05-01

First alpha of the v0.2.3 line. Ships the **coding-path classifier**
(Phase 7 of the v0.2.0 milestone train) — every indexed chunk now
carries a `payload.room` facet that `dual_memory_search` and
`qdrant_find` can filter on via the new `where` parameter.

### Added

- Coding-path classifier: every indexed chunk gains `payload.room` via
  exact path-component equality (`set(Path.parts) ∩ set(keywords)`),
  never substring matching. `data/chest_xray/img.png` is NEVER
  classified as `tests`. Defaults cover backend, frontend, tests, docs,
  scripts, config, migrations, types in priority order
  (CLASS-01, CLASS-02).
- `[supamem.classifier.rooms]` TOML config table — override the default
  keyword map per-project; priority is encoded by config order
  (first-match-wins, D-01a). User TOML REPLACES the defaults dict
  (leaf-replace, mirrors `transcript_*` precedent).
- `where` parameter on `dual_memory_search` and `qdrant_find` MCP tools
  (D-17 alias parity): `where={"room": "backend"}` filters retrieval to
  that scope; `where={"room": ["backend", "tests"]}` is OR-within-key
  (Qdrant `MatchAny`); multiple keys are AND. Single Qdrant `Filter`
  built once at the retrieval boundary and threaded to BOTH dense and
  sparse Prefetch arms PLUS the top-level `query_filter`
  (defense-in-depth, D-03). v1 documents `room` as the only key;
  unknown keys pass through to Qdrant for forward-compat with Phase
  9/11 (CLASS-03).
- `payload.room` is ALWAYS present (string or JSON `null`) on every
  point — uniform schema (D-06). Transcript chunks classify to
  `room = null` by construction (filter via existing `payload.chunker`).
- `supamem doctor` surfaces the active classifier rooms map with
  `[source: ...]` provenance, the stored `classifier_hash`, and a
  per-room histogram (including a `null` bucket for unmatched chunks).
- Hash-drift sweep: `manifest.classifier_hash = sha256(json.dumps(rooms,
  sort_keys=False))` captures both content AND priority order. On every
  `supamem index` run, if the stored hash differs from the current
  config hash, supamem scrolls the collection in batches and
  `client.set_payload({"room": new_room}, points=[ids], wait=True)`
  per-room — pure metadata update, **zero re-embedding cost** (D-08).

### Changed

- Manifest gains `__classifier_hash__` reserved top-level key
  (additive; emitted only when not None, byte-stable round-trip when
  unset — mirrors `__transcripts__` precedent from Phase 6).
- `tuned_hybrid` retrieval threads the `where`-derived `qmodels.Filter`
  to BOTH dense and sparse Prefetch arms via a single construction
  site (`src/supamem/retrieval/filters.py`) — anti-drift, no duplicated
  filter logic across arms.

### Notes

- Pre-0.2.3 collections auto-migrate on first post-upgrade
  `supamem index` invocation: missing `__classifier_hash__` is treated
  as drift from null, triggering a one-time sweep that stamps every
  existing chunk with a `room` value.
- No new dependencies. `qdrant-client`, `mcp`, `pydantic` versions
  unchanged.

## [0.2.2a1] — 2026-05-01

First alpha of the v0.2.2 line. Ships the **transcript chunker plugin**
(Phase 6 of the v0.2.0 milestone train) — supamem can now index Claude
Code session JSONL as Q+A drawer chunks alongside the existing Markdown
corpus. Default-OFF: opt in with `--transcripts`.

### Added

- `supamem index --transcripts` (bare flag) ingests Claude Code session
  JSONL from `~/.claude/projects/` (or `[supamem.transcript] default_root`)
  as Q+A drawer chunks via the new `supamem.chunker = transcript`
  entry-point. Pass an explicit path with
  `supamem index --transcripts /path/to/sessions/`. Mixed corpora dispatch
  per-suffix: `*.md` → `markdown_header`, `*.jsonl` → `transcript`
  (INGEST-01..INGEST-05).
- `--transcripts-only` skips the default project corpus and indexes only
  transcripts in the same run.
- `--since 30d` (or `Nh`) filters transcript JSONL by mtime; `--since 0`
  disables. Defaults to `[supamem.transcript] since_days = 180`.
- `[supamem.transcript]` config table with six keys: `default_root`,
  `since_days` (180), `tool_payload_max_chars` (2000),
  `chunk_soft_max_tokens` (600), `include_paths_glob`,
  `exclude_paths_glob`. All surfaced by `supamem doctor` with
  `[source: default|user|project]` provenance.
- Per-message-uuid dedupe in `manifest.py` under the `__transcripts__`
  key — re-running on an unchanged corpus reports `0 new, 0 changed`.
  Editing one message purges-then-reinserts only that message's chunk
  (append-only on the rest).
- Tool-use payloads above 2000 chars are elided to a synthesis stub;
  `tool_uses` metadata always lists `{id, tool_name, status}` regardless.
  Status correlates with `tool_result.is_error` from the next pair when
  observable.
- `rich.progress` indexing bar shows session/chunk/elapsed counts
  (auto-disabled under `NO_COLOR=1` or non-tty).
- `supamem doctor` gains a **Transcript config** section between MCP caps
  and Installed clients, surfacing the six `[supamem.transcript]` keys
  with config-source attribution.

### Security

- ⚠ **Transcripts may contain secrets** (API keys, tokens from
  copy-pasted env files, credentials in tool payloads). v0.2.2a1 ships
  **no redaction** — review your `~/.cache/supamem` Qdrant collection
  before sharing it. Hand-exclude sensitive sessions via
  `[supamem.transcript] exclude_paths_glob`. Redaction is tracked for
  v0.3 via a future `supamem.redactor` plugin group.

### Deferred

- Cursor SQLite + ChatGPT export ingestion → follow-on plugins
  (third-party or supamem-shipped after v1 stabilizes).

## v0.2.1 — unreleased

Patch fixing two v0.2.0 banner gaps that turned out to matter:

### Added

- **User-visible SessionStart banner** — the v0.2.0 banner reached the
  model via `additionalContext` but was invisible to the user. Adds a
  `systemMessage` field to the SessionStart hook payload, which Claude
  Code renders as the `SessionStart:startup says: <line>` row in the
  terminal (officially documented dual-channel pattern). Cursor
  `user_message` is included for forward-compat (per Cursor docs the
  field is "accepted but not enforced" today; will surface once Cursor
  ships UI for it). Suppress only the user-visible row with
  `SUPAMEM_BANNER_QUIET=1` (keeps context injection alive for the
  model). `SUPAMEM_BANNER_DISABLE=1` still kills both channels.
- **`supamem doctor` install-drift surfaced in the banner** — the
  health flag now flips to `⚠` when any installed client's managed-
  block version differs from the running CLI (i.e. you upgraded
  supamem but a client's CLAUDE.md/.cursor rules still reference the
  old version). Prompts running `supamem repair` to resync. The drift
  probe is cheap (small text reads, never raises); banner failures
  fall back to `✓` rather than blocking session-start.

## v0.2.0 — 2026-05-01

First milestone of the v0.2.0 token-economy line. Ships server-side hard
caps on every MCP retrieval response (Phase 5), the multi-project install
fix and `supamem repair` migration verb, agent-discipline hooks
(claude-code edit-gate + Cursor advisory), and the SessionStart banner
enrichment. See the **Behavior change** notes below — review before upgrading.

### Added

- New `[supamem.mcp.caps]` TOML config table with three keys:
  - `max_top_k` (default: **25**) — silently clamps requested `top_k` on every
    retrieval call; the response carries `SearchResult.clamped_to` so callers
    can detect it.
  - `max_query_chars` (default: **250**) — enforced via Pydantic
    `Field(max_length=...)` baked into the MCP tool schema at registration
    time; over-cap queries fail at the schema boundary as a structured MCP
    validation error (no silent truncation, no stdout pollution).
  - `max_preview_chars` (default: **200**) — display preview cap applied to
    `Chunk.preview` on each hit. The full canonical payload in `Chunk.text`
    is **never** truncated.
- New `Chunk.preview: str` field on MCP search responses — display-only
  excerpt of `Chunk.text`, capped at `max_preview_chars`. Existing
  `Chunk.text` consumers see byte-identical full payloads (backward-compat).
- New top-level `SearchResult.clamped_to: Optional[int]` field — set to the
  effective cap when the server clamped requested `top_k`; `None` otherwise.
- `summary_md` rendering now includes a `⚠️` warning line on clamp events
  (D-14): `⚠️ Clamped \`top_k\`: {requested} → {N} (raise mcp.caps.max_top_k)`.
- `supamem doctor` surfaces all three cap values in a dedicated **MCP caps**
  section with config-source attribution (`[source: default|user|project]`).
- `qdrant_find` alias inherits identical caps and response shape via shared
  closure-captured locals — alias drift is impossible by construction (D-17).

### Changed

- Query-length enforcement is now **config-driven** at the MCP schema
  boundary. The previous internal `MAX_QUERY_LEN = 4096` constant in
  `src/supamem/mcp_server.py` has been removed; the cap lives at
  `cfg.mcp_caps_max_query_chars` and is baked into the tool's JSON Schema
  at registration time so MCP clients (Cursor, Claude Code) see the limit
  at tool-discovery time.

### ⚠️ Behavior change — review before upgrading

The default `max_query_chars` is **250**, dramatically lower than the previous
internal `MAX_QUERY_LEN = 4096`. Agents (or callers) submitting queries longer
than 250 characters now receive a structured MCP validation error instead of
the request silently working. If your workflow legitimately needs longer
queries — long natural-language prompts, embedded code excerpts, paragraph
seeds — raise the cap explicitly in your project config:

```toml
# .supamem/config.toml
[supamem.mcp.caps]
max_query_chars = 4096  # restore v0.1.x behavior
```

The new default is calibrated for token economy on small focused queries,
which is the intended retrieval-key shape. Long contexts belong in the
ingestion path (write to `dual_memory_write`), not in retrieval queries.

### Notes

- This is one phase of the v0.2.0 milestone; `pyproject.toml` is not bumped
  here. The version bump lands at the milestone Definition-of-Done point.
- README.md and the four translations (`README.{zh-CN,es,ja,ru}.md`) are
  intentionally untouched in this entry per PUB-05: README updates are
  gated on Phase 13 bench validation so the user-facing narrative ships
  with measured numbers, not pre-bench claims.

---

### Added — multi-project install + agent-discipline hooks (2026-05-01)

Closes the silent wrong-collection bug on multi-project machines and lands
the `--enforce-search` opt-in gate that turns the project's "search BEFORE
choosing an approach" rule from advisory into mechanical.

- **Per-workspace install is now the default.** `supamem install` writes
  to `<repo>/.mcp.json` (Claude Code project scope, per Anthropic docs)
  and `<repo>/.cursor/mcp.json` (Cursor per-workspace path). Each
  per-workspace file carries an explicit `SUPAMEM_PROJECT_ROOT` env
  pointing to the install-time cwd. Pass `--scope user` to keep the
  legacy global write to `~/.claude.json` / `~/.cursor/mcp.json`.
- **Defense-in-depth project-root resolution** in `cmd_mcp_server`:
  honor `SUPAMEM_PROJECT_ROOT` first, then walk parents from `Path.cwd()`
  for `.supamem/config.toml` or `pyproject.toml [tool.supamem]` (stops
  at `$HOME` / filesystem root). Both miss + collection still default →
  one-line stderr warning naming cwd, env-var presence (never values),
  and the fix command. Stdout stays JSON-RPC clean.
- **`supamem repair` verb** — migrates a user from legacy global install
  to per-workspace files in one command. Strips stale supamem entries
  from globals, re-installs at project scope. Auto-detects clients;
  idempotent on a healthy install. Forwards `--enforce-search`.
- **Claude Code edit-gate hook** (`--enforce-search` on install).
  Registers a PreToolUse `Edit|Write|MultiEdit` matcher that DENIES the
  tool call when no `mcp__supamem__dual_memory_search` (or `qdrant_find`
  alias) is found in the session transcript since the last user turn
  (strategy A — strict per-turn). Emits Anthropic's
  `permissionDecision: deny` JSON contract on stdout; reverse-scans the
  transcript with a 256 KB byte cap. Override per-session with
  `SUPAMEM_GATE_DISABLE=1`.
- **Cursor `beforeSubmitPrompt` advisory hook** — Cursor 1.7's hooks API
  has no fail-closed pre-edit event, so this is advisory-only: when the
  user's prompt looks edit-bound (regex over fix/refactor/rename/...),
  inject an `agentMessage` reminding the agent to call
  `dual_memory_search` first. Override with `SUPAMEM_ADVISORY_DISABLE=1`.
- **SessionStart banner enrichment** — banner now leads with a 1-char
  health flag (`✓` healthy / `⚠` qdrant unreachable or default
  collection still in effect) and appends `update v0.X.Y available`
  when the existing `update_check` daemon has cached a newer release.
  No auto-heal — surfacing only.

### Changed — multi-project install (2026-05-01)

- `supamem install --client claude-code` no longer writes to
  `~/.claude.json` `mcpServers.supamem` by default. The new default is
  `<repo>/.mcp.json` (project scope). Behavioral migration path:
  `supamem repair` (recommended) or pass `--scope user` to keep legacy.
- `supamem install --client cursor` no longer writes the MCP entry to
  `~/.cursor/mcp.json` by default. New default: `<repo>/.cursor/mcp.json`.
- `supamem uninstall --client {claude-code,cursor}` is now defensive:
  strips supamem from BOTH project and user scopes regardless of which
  scope the user originally installed with.

### ⚠️ Behavior change — review before upgrading (2026-05-01)

If you previously ran `supamem install` from inside a workspace, you
likely have a `mcpServers.supamem` entry in `~/.claude.json` and
possibly `~/.cursor/mcp.json`. After upgrading, the **per-workspace
files take precedence** in their respective workspaces, but the stale
global entry will be used by hosts opened in a directory that has no
per-workspace file — and that entry has no `SUPAMEM_PROJECT_ROOT`, so
it'll silently fall through to the default collection
(`dev_memory_tuned_hybrid`).

Recommended: run `supamem repair` from each of your supamem-enabled
workspaces. It strips the stale globals and re-installs per-workspace.

## v0.1.5 — 2026-04-29

`supamem install --client claude-code` now wires the **SessionStart banner**
hook automatically — closes the v0.1.4 gap where the new `supamem hook
session-start` command shipped but no installer registered it.

### Changed

- `supamem.install.claude_code` adds a `SessionStart` hook entry to
  `~/.claude/settings.json` that runs `supamem hook session-start` on
  every Claude Code session open. Idempotent — reinstalling on top of a
  v0.1.4 user-home that already has the entry is a no-op.

### Notes

- Cursor SessionStart already wires `supamem index --snapshot cursor` via
  `.cursor/hooks.json` — the v0.1.4 banner is not appended there because
  Cursor's `sessionStart` hook fires shell commands (not MCP-context
  injection); the `supamem live` dashboard remains the visibility surface
  for Cursor users.
- OpenCode SessionStart hook contract is still upstream-pending, so the
  OpenCode installer leaves SessionStart wiring as a no-op until the
  feature lands. Tracked at github.com/anomalyco/opencode#5409.

## v0.1.4 — 2026-04-29

Visibility round: gives users observable evidence that supamem is alive
and working. Three additions; no behavior change to retrieval.

### Added

- **SessionStart banner** (`supamem hook session-start`): one-line plain-text
  status injected at session start in Claude Code / Cursor / OpenCode via
  `additionalContext`. Format:
  `🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>`
  Cross-client portability via dual JSON keys (`hookSpecificOutput.additionalContext`
  + snake-case `additional_context`). Auto-detects calling client from env
  vars (`CLAUDECODE`, `OPENCODE`, `CURSOR_AGENT`) when `--client` is omitted.
  Fail-soft per hook discipline — never raises, never blocks session start.
- **`supamem live` CLI**: Rich-Live terminal dashboard tailing the audit
  JSONL in real time. Run in a side terminal alongside Claude Code /
  Cursor / OpenCode for instant visibility into the silent PreToolUse-hook
  injections (which save tokens by NOT showing UI). Uses
  `watchfiles.awatch` for OS-native file change notifications; falls back
  to polling if `watchfiles` isn't available. Pipe-safe: prints plain JSONL
  when stdout isn't a TTY. Handles file rotation, terminal resize, and
  Ctrl-C cleanly.

### Added (deps)

- `watchfiles>=0.24` — Rust-backed async file watcher for `supamem live`.
  Has manylinux + macOS arm64 wheels, no source build required at install.

### Design notes

- A per-injection footer in PreToolUse `additionalContext` was considered
  but **dropped**: chat hosts don't render Markdown `<details>`, so the
  "collapsible" pattern is fiction. A footer would just add ~80 tokens to
  every Edit (+20%) for no UI benefit. The banner + dashboard provide
  visibility without the per-Edit token tax.

## v0.1.3 — 2026-04-29

Adds the missing **write path** so supamem can serve as the *only* memory
layer per project (no need to keep upstream `mcp-server-qdrant` alongside
for the `qdrant-store` workflow).

### Added

- New `dual_memory_write` MCP tool: agents persist insights/research
  findings mid-session. Writes a deterministic Markdown file with YAML
  frontmatter to `<project>/.claude/insights/_agent/<slug>.md` and
  immediately upserts it into the project's tuned-hybrid Qdrant collection
  with `wait=True` so the very next `dual_memory_search` sees it.
- Idempotent on `topic`: same topic → same `slugify()` slug → same on-disk
  path → same `UUIDv5(NAMESPACE_AGENT_WRITE, slug)` Qdrant point id.
  Re-saving overwrites in place.
- Backward-compat aliases for upstream `mcp-server-qdrant` users:
  `qdrant_find` (alias of `dual_memory_search`) and `qdrant_store` (alias
  of `dual_memory_write`). Default-on; disable with
  `SUPAMEM_QDRANT_ALIASES=0`. Lets existing prose / agent instructions
  ("save with qdrant-store", "query qdrant-find") keep working without
  rewrites.
- New `supamem.memory_writer` public module: `write_memory()` for non-MCP
  callers (CLI plugins, scripts).

### Fixed

- Partial-failure semantics: if Qdrant indexing fails after the on-disk
  write succeeded, return `indexed: false` with the error message instead
  of leaving the file unwritten — the file is still valuable and the next
  `supamem index --target tuned` run will pick it up.

### Security

- Path-traversal hardened: target path resolution refuses anything that
  doesn't `is_relative_to(project_root)`.
- Size limits enforced: `topic <= 120`, `content <= 64K`, `description
  <= 300`, `tags <= 10` items × 32 chars each.

### Added (deps)

- `PyYAML>=6.0` promoted from transitive to direct dependency
  (`memory_writer` writes YAML frontmatter; explicit dep avoids surprise
  removal if a transitive provider drops it).

## v0.1.2 — 2026-04-29

Project-tunable regress baselines and config-resolved goldens path. Unblocks
brownfield projects (e.g. SoftChat, Plan 80.6-14) where the bundled Phase
80.1 thresholds — calibrated against the supamem-internal corpus — don't fit
the project's corpus size.

### Added

- `[supamem.eval]` config block accepts `baseline_recall_at_5`,
  `baseline_total_tokens`, `baseline_p95_latency_ms` to override the bundled
  D-19 defaults per project.
- Env-var overrides (highest precedence): `SUPAMEM_BASELINE_RECALL_AT_5`,
  `SUPAMEM_BASELINE_TOTAL_TOKENS`, `SUPAMEM_BASELINE_P95_LATENCY_MS`.
- `cfg.goldens_path` now used as fallback when `--goldens` flag is omitted —
  previously the config field existed but was ignored by the eval runner.

### Fixed

- `supamem eval --regress` no longer fails projects with healthy retrieval
  but corpus sizes outside Phase 80.1's calibration window. Default behavior
  is unchanged for callers that don't set overrides.

## v0.1.1 — 2026-04-29

First PyPI release. Hardens v0.1.0 with CI fixes, agent guides, an update-check
notifier, and a published wheel/sdist on PyPI so downstream consumers can pin a
version range instead of a git tag.

### Added

- `supamem.update_check` — pip-style fire-and-forget GitHub Releases probe.
  Daemon thread writes `platformdirs.user_cache_dir("supamem")/update_check.json`;
  the *next* invocation prints a stderr footer if a newer release is cached.
  24h TTL, 6h backoff on 403/429, ETag-aware. Suppress with
  `SUPAMEM_NO_UPDATE_CHECK=1`, `NO_UPDATE_NOTIFIER=1`, or `CI=1`. Skipped when
  stderr is non-TTY. Surfaced in `supamem doctor` (new "Update check" section).
- `AGENTS.md` + `CLAUDE.md` — agent-facing project guides per the Apr 2026
  cross-tool convention.

### Changed

- License metadata migrated to PEP 639 SPDX expression (`license = "MIT"`),
  removing legacy `{ text = "MIT" }` form.
- Distribution channel: PyPI (was git-tag-only). Install via
  `pip install supamem` or `uv tool install supamem`.

### Fixed

- `tests/test_cli_smoke.py` subprocess env now pins `NO_COLOR=1`, `TERM=dumb`,
  `COLUMNS=200`, and pops `FORCE_COLOR` — eliminates Rich color escapes that
  broke CI assertions on GitHub Actions runners.

## v0.1.0 — 2026-04-29

The initial public release. Extracted from the SoftChat dual-memory stack
(Phases 80.1–80.5) and shipped as a project-agnostic Python package under MIT.

### CLI surface (10 commands)

- `supamem init` — greenfield bootstrap (probes Qdrant, creates per-project
  hybrid collection, writes `.supamem/config.toml`).
- `supamem install --client {claude-code|cursor|opencode}` — patches the
  client config atomically; idempotent; `--dry-run` supported.
- `supamem uninstall --client X` — reverses install cleanly.
- `supamem index [--target tuned] [--force] [--snapshot cursor]` — embeds
  Markdown sources into Qdrant or refreshes the Cursor `.mdc` snapshot.
- `supamem mcp-server [--transport stdio|http] [--port N] [--host H]` —
  runs the MCP server. Streamable HTTP per Nov 2025 MCP spec (D-45).
- `supamem hook {claude-code|opencode} [--file-path X]` — per-client
  PreToolUse hook delegate. Fail-soft: never blocks the calling tool.
- `supamem doctor [--show-secrets]` — health probe + resolved config chain
  + version-drift advisory across detected clients.
- `supamem stats [--show today|week|all] [--format table|json]` — Welford
  schema-v2 usage counters from `~/.cache/supamem/audit.jsonl`.
- `supamem migrate --source X --target Y --path {coexist|migrate|adopt-as-is}`
  — brownfield migration with snapshot-before-destructive guard.
- `supamem eval [--regress] [--goldens path]` — recall@5 + p95 latency +
  total tokens against the bundled 33-query corpus. Phase 80.1 baselines:
  `mean_recall_at_5 >= 0.60`, `total_tokens <= 4000`, `p95_latency_ms <= 500`.

### Bundled

- `~/.supamem/share/{rules,skills,commands,hooks,cursor-rules}/` — canonical
  artifacts referenced by every client config (SC-3 reference-not-copy
  contract; Cursor `.mdc` is the documented copy exception).
- 33-query golden corpus at `supamem.eval.goldens.phase_80_1_tuned_hybrid`
  for `--regress` (per D-46).
- Plugin entry-point groups for retrieval, embedder, and chunker so
  third-party packages can extend supamem without forking (per D-48).

### Locked architecture (Phase 80.1 D-19 / D-25)

- Hybrid retrieval: BM25 (`Qdrant/bm25`) + dense (`sentence-transformers/
  all-MiniLM-L6-v2`) with Qdrant native `FusionQuery(RRF)`, prefetch=20.
- T-1 markdown header chunker, soft-max 250 tokens, fallback 200/20.
- T-4 recency boost, T-5 cosine dedup (threshold 0.97), T-8 token-budget
  truncation (1500).

### Install

```bash
uv tool install git+https://github.com/dzmitrys-dev/supamem@v0.1.0
```

PyPI publish is deferred to v0.2 per D-44. v0.1.0 is git-tag-only so
SoftChat's rip-out commit (Plan 80.6-14) can pin a verifiable artifact
before the broader package distribution surface lands.

### Notes

- Forward-compat: tool `title` field set on every MCP tool registration;
  Cursor / Claude.ai web honor it today; Claude Code TUI ignores but will
  pick up automatically when the upstream lands.
- Stylish `rich`-powered CLI output (banner, panels, spinners, status
  tables) on every long-running command.
- Made with care by [SoftChat](https://app.softchat.ru) and
  [SoftSkillz](https://softskillz.ai).
