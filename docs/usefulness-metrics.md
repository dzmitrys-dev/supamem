# supamem usefulness metrics — what we collect, how to read it, what's missing

This is the operating guide for judging whether supamem's retrieval is *useful*, not just fast
or green. It exists because the 2026-11-14 effectiveness study
(cl-harness `research/research-supamem-effectiveness-2026-11-14.md`) could only measure the
edit hook by mining Claude Code transcripts after the fact — supamem kept no record of what it
injected. Since v0.4.0a4 it does. This doc maps every signal, its baseline, how to interpret
movement, and the gaps that remain.

## Where the data lives

| Source | Written by | Cadence | Contents |
|---|---|---|---|
| `<audit dir>/injections.jsonl` | edit hook (one row per invocation) + MCP server (one row per search, `kind:"mcp"`) | per event | ts, session, target, query, chunks (source/score/exists/lang), empty, deduped, dropped (stale/floor/foreign), ms, shadow, rerank (provider tag, v0.4.0a7) |
| `<audit dir>/audit.jsonl` | `stats.counter.bump` | per search/rerank/error | kind, source (`hook_claude_code` / `mcp`), tokens, latency_ms |
| `<audit dir>/dedup-<session>.json` | edit hook | per session (24 h TTL) | already-injected chunk keys |
| `supamem hook-stats [--days N] [--json]` | analyzer | on demand | all derived metrics below |

`<audit dir>` = `platformdirs.user_cache_dir("supamem")` (macOS: `~/Library/Caches/supamem`);
`SUPAMEM_AUDIT_DIR` overrides.

## The metrics (edit-hook channel)

`supamem hook-stats --days 7 --json`. Baselines are the 2026-11-14 study (all-time ferrohex);
"current" is the first 3 days of the a4 pipeline (2026-11-14→17, n=4014 invocations).

| Metric | Definition | Baseline → a4 | How to read it |
|---|---|---|---|
| `injection_rate` | non-empty injections / invocations | 4.2% → 8.0% | Coverage of the channel. Rising after a coverage fix (worktree, target_globs) is good; falling after a quality gate (floor) can also be good. Never read alone. |
| `use_rate` | non-empty rows with ≥1 chunk used later in the session (Read/Edit/Write/Grep on its source, or basename in assistant text) / non-empty rows | 0.6% → 3.4% | THE quality number. Interpret with n (`used_events`): below ~20 uses, a swing is noise. |
| `useful_per_100_edits` | absolute first-uses per 100 edits | ~0.023 → 0.27 | Goodhart guard: `use_rate` can be gamed by injecting less. This must not fall when you tighten gates. |
| `repeat_rate` / `dedup_suppressed_rate` | emitted-row repeats / candidate rows that suppressed a repeat | 65.6% → suppressed 89.9% | Dedup working = suppressed high. `repeat_rate` on emitted rows is near-blind (all-deduped rows emit nothing). |
| `stale_rate` / `stale_dropped_rate` | emitted chunks with missing source / candidate rows that dropped one | 8.5% → 1.6% dropped | Stale sources are dropped pre-emit, so watch `stale_dropped_rate` — a rising value means the index lags the repo (re-run `supamem index`). |
| `lang_mismatch_rate` | rows with a ru chunk while editing code | 78.8% → 63.7% | Language fit of the corpus for the query. The multilingual embedder (v0.4.0a5) targets <30%; a persistent high value with a multilingual embedder means the corpus itself is the wrong language. |
| `shadow_rows` / `shadow_use_rate` | the shadow task-query channel (journaled, never emitted) | — → 1.6% (n=246) | Decision input for flipping the emit to task-text queries. a4 verdict: shadow finds MORE (chunks in 246/504 where main injected nothing) but what it finds is used LESS than the main path (1.6% vs 3.4%) → no flip; re-evaluate at n≥500. |

MCP channel (`stats["mcp"]`): `searches`, `with_chunks`, `chunks_total`, `ru_chunk_share` —
volume and language-mix only. There is no session id in MCP tool-call payloads, so a use-rate
for MCP cannot be computed from the journal; the study measured it by transcript mining
(6.9% read-through, n=277).

## Interpretation rules

1. **Always read a rate with its denominator.** `use_rate` on 12 non-empty rows is an anecdote;
   on 300 it's a signal. Same for `shadow_use_rate`.
2. **Pairs, not singles**: `use_rate` up + `useful_per_100_edits` down = injecting less, not
   better. `injection_rate` down + `use_rate` flat = floor cutting noise (good).
3. **Channel discipline**: hook rows and `kind:"mcp"` rows are separate populations — hook-stats
   splits them (v0.4.0a5); never merge them back.
4. **After any ranking/embedder change, compare against the same window length** and expect
   ±1–2 pp wobble on small n; the golden-set eval (`supamem eval`, ADR-0002) is the
   deterministic instrument for flips; `hook-stats` is the field instrument.
5. **Counter-metric hygiene** (Goodhart): the two guards are `useful_per_100_edits` (absolute
   usefulness) and `dropped.floor` volume (how much the gate is suppressing). If you raise
   `min_score`, both must be checked a week apart.

## What we still miss (the research backlog)

1. **MCP use-rate needs a session id** — MCP payloads don't carry one. Options: a client-side
   wrapper that stamps the query with the session (invasive), or periodic transcript mining
   (the study's miner is the tool). Until then: treat `mcp.searches` as volume only.
2. **Correction-after-injection** — the strongest "it was wrong" signal: the owner edits code
   that contradicts an injected chunk right after receiving it. Detectable from transcripts
   (Edit tool_use within N minutes following an injection touching the same area) — needs a
   transcript pass, not journal data.
3. **Coverage / recall proxy** — "an edit where a relevant doc existed but wasn't injected".
   Proxy: for each non-empty injection the score floor dropped nothing AND the session later
   Read a docs file (it had to find it manually). Buildable from journal + transcripts.
4. **Golden set** (study §5.4) — 50–100 real path-derived queries + labeled relevant chunks,
   seeded from the 12-event manual sample + the verified-use events; gates embedder/reranker
   flips deterministically (`recall@5` / MRR) instead of waiting weeks of field data.
5. **lessons.jsonl ingest** — `.claude/lessons.jsonl` is valuable and unindexed; a `.jsonl`
   source routes to the TRANSCRIPT chunker (wrong shape for lessons). Needs a lessons-aware
   ingest before adding it to any `sources` list.
6. **Per-project hook opt-out** — therapy (personal notes) raises a privacy question (study
   Q2): should coding hooks reach it at all? There is no `[supamem.hook] enabled = false`
   switch today; adding one is small but policy-first (owner decision).
7. **Timeout drops** — the number matters again. Journal 2026-11-17: searched hook rows had
   p50 24.6 s and p90 73.6 s. 38% of searches, and 49% of non-empty injections, ran past the
   then-30 s hook timeout. The timeout was raised to 100 s that day, and a6 moved retrieval into
   the shared daemon (ADR-0005), so no edit loads models per call any more. Delivery of those late
   injections could not be confirmed from transcripts (the marker counts were inconsistent), so
   treat it as a hypothesis.
   - Add `timeout_drop_rate` = rows with ms > hook timeout to `hook-stats`.
   - Compare `use_rate` / `useful_per_100_edits` across the a5 → a6 boundary. The latency fix
     should raise both without any change in retrieval quality.
   - Expected new empty-row causes in a6: daemon `busy` / `expired` skips. Read them in
     `supamem daemon status` (`busy_rejected`, `expired`).
8. **Remote-reranker era split (v0.4.0a7)** — rows now carry a `rerank` provider tag
   (`"openrouter/cohere/rerank-5-fast"` / `"mxbai_v2/…"` / null). Remote relevance scores are
   0–1 vs mxbai's raw scale: never compare `score` across providers, and split any
   `injection_rate` / `use_rate` comparison on `rerank` (plus the `min_score` flip that
   ships with it). Remote network failures land in the `rerank:remote_failed` counter and
   degrade to hybrid order without `rerank_score` (floor not applied) — read them with
   `supamem doctor`.

## Baseline table for future comparisons

| Date | Channel | injection | use | useful/100 | repeats suppressed | stale dropped | lang mismatch |
|---|---|---|---|---|---|---|---|
| 2026-11-14 (study, pre-a4) | hook | 4.2% | 0.6% | ~0.023 | 0 (no dedup) | 0 (no drop) | 78.8% |
| 2026-11-17 (a4, 3 d) | hook | 8.0% | 3.4% | 0.27 | 89.9% | 1.6% | 63.7% |
| 2026-11-18 (2 d, a5+a6 mix, mxbai_v2) | hook | 10.0% | 2.9% (n=6, noise) | 0.29 | 92.3% | 0.4% | 47.4% |

Append a row per release or config flip, with the window length.
