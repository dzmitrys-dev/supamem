---
status: accepted
date: 2026-11-17
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["v0.4.0a5", "issue #5"]
---

# 0004. Multilingual dense embedder — paraphrase-multilingual-MiniLM-L12-v2, opt-in per project

## Context

The 2026-11-14 effectiveness study measured the corpora at 63–86% Russian while the only dense
embedder (`all-MiniLM-L6-v2`) is English-only: BM25 has zero token overlap for EN-query vs
RU-doc, and the dense arm collapses to noise. After the a4 mechanical fixes the production
`lang_mismatch_rate` still sat at 63.7% — the dominant remaining source of useless injections.
Separately the study found `cfg.embedder` was accepted-but-ignored config (every funnel hardcoded
the default model), so an embedder choice could not even reach the pipeline.

## Decision

1. Add the embedder plugin `multilingual_minilm`
   (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384-dim, 0.22 GB, no
   query/passage prefixes) — **opt-in per project** (`[supamem] embedder = "multilingual_minilm"`),
   default unchanged (`minilm`).
2. Wire `cfg.embedder` through a single resolver `dense_model_id_for(cfg)` at ALL THREE
   production funnels: `TunedHybridBackend._ensure` (query side), `run_index` (index side),
   `memory_writer._index_single_doc` (MCP write side). A fail-closed config gate rejects unknown
   names at `load_config` (mirrors the reranker gate).
3. Stamp every manifest entry with a per-entry index epoch (`<target>_epoch` = `collection|model`,
   transcript messages: `epoch`); a mismatch counts as dirty. Without it, switching collection or
   embedder (and back) silently skipped every unchanged file. The epoch is per entry, not global:
   the manifest is shared by every project, and a single global key would make alternating
   index runs of two projects re-embed everything each time.
4. Converting projects index into a NEW collection (`<name>-ml`): same 384-dim schema, different
   vector space — old collections stay as rollback until deliberately deleted.

## Consequences

- Same dimension (384) means no Qdrant schema change, no query/passage prefix protocol, and a
  0.22 GB model (vs 2.24 GB for e5-large) — safe on a RAM-constrained host where every
  model-loading process already pins ~2.4 GB of reranker weights (issue #5).
- RU recall should jump from near-zero (English-only embedder) to usable; `lang_mismatch_rate`
  target < 30% (measured via `hook-stats`; see docs/usefulness-metrics.md for interpretation).
- Quality ceiling below e5-large/bge-m3 on hard cross-lingual benchmarks; acceptable because the
  reranker (multilingual mxbai-rerank-base-v2) re-scores the prefetch window and the embedder's
  job is recall@prefetch, not final ranking.
- Every project re-embeds fully ONCE on its first a5 index run (pre-a5 entries carry no epoch,
  so their target collection is unknown) — expected, one-time.
- The eval benches stay on the default embedder (byte-identical regression locks preserved).

## Alternatives Considered

- **bge-m3** — best conceptual fit (multilingual dense+sparse, 8k ctx) but **absent from
  fastembed 0.8.0**; not installable without forking the embedding layer.
- **intfloat/multilingual-e5-large** (1024-dim, 2.24 GB) — benchmark king (Mr TyDi ~70 MRR@10)
  but: +2.24 GB per model-loading process on a 95%-swap machine, `DEFAULT_VECTOR_SIZE` is
  hardcoded 384 (schema plumbing), and it requires `query: `/`passage: ` prefixes — the embedder
  interface has no role concept. Revisit AFTER the shared HTTP daemon (issue #5) makes model
  weight a one-time cost.
- **Flip the default** — rejected: non-opt-in users must keep byte-identical behavior (Phase 80.1
  bench locks), and each project should choose based on its corpus language mix.
