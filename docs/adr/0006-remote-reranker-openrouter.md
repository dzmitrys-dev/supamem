---
status: accepted
date: 2026-11-18
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["v0.4.0a7", "ADR-0005", "issue #5"]
---

# 0006. Remote reranker via OpenRouter (opt-in per project)

## Context

ADR-0005 left one bottleneck open: warm retrieval latency is dominated by the reranker
(mxbai-rerank-base-v2 upcast to fp32 on CPU — `mxbai_rerank.auto_device()` ignores MPS).
Measured 2026-11-18: warm queries p50 24 s / p90 60 s, rerank off → 0.3 s; the daemon
holds ~2.9 GB, most of it the reranker.

A spike against the OpenRouter rerank API (`POST /api/v1/rerank`, real calls, 2026-11-18):

| Model | Warm latency | Doc limit | $/call (26 docs) | ~$/month at ~900 calls/day |
|---|---|---|---|---|
| **cohere/rerank-5-fast** | **0.6–0.8 s** | ≥30 ok | **$0.0001** | **~$2.7** |
| qwen/qwen3-reranker-0.6b | 0.6–1.2 s | ok | $0.0014 | ~$38 |
| cohere/rerank-v3.5 | 0.75 s | ok | $0.002 | ~$54 |
| nvidia/nemotron-3.5-rerank-8b:free | 2.8–5.6 s | **~20 → HTTP 422** | $0 | — |

The owner approved the design and cohere/rerank-5-fast, and explicitly allowed sending
chunks — including therapy (personal medical notes) — to the remote reranker for his own
projects. For everyone else chunks leaving the machine is a privacy decision we do not
make for them.

## Decision

1. **Remote rerank is a plugin in the existing `supamem.reranker` group** —
   `reranker_name = "openrouter"`, `model_id = "cohere/rerank-5-fast"`. No daemon protocol
   changes: the resolved config already travels to the daemon, whose backend cache is
   keyed by the full-config hash. The reranker model never loads locally.
2. **Opt-in per project** via `.supamem/config.toml`; the default stays `mxbai_v2` local.
   New keys: `remote_base_url` (default `https://openrouter.ai/api/v1`), `remote_api_key_env`
   (default `OPENROUTER_API_KEY`), `remote_timeout_ms` (default 8000, must be > 0).
3. **Failure means skip, never hang, never auto-fallback to a local model load.** One retry
   on 429/5xx/timeout; then `RemoteRerankerError` → one-line warn (no traceback — network
   blips are routine) → the pre-existing off-branch: hybrid RRF order, no `rerank_score`,
   so the score floor does not apply. The `rerank/remote_failed` counter records it.
4. **The key travels only as an env var NAME.** Config, journal and logs never contain the
   value; the Authorization header exists only for the duration of a call. The daemon
   inherits the key from the env of the client that spawned it — `supamem daemon stop`
   respawns with the current env; `doctor` shows key presence and failure counts.
5. **Score-scale guard.** Remote scores are 0–1; the shipped `hook_min_score = 2.5` would
   drop every chunk (silent injection death — observed in the GREEN run: `dropped.floor=9`).
   Plugins declare `score_scale_max`; retrieval warns once per process and `doctor` warns
   when the floor is unreachable. Calibrating a remote floor is golden-set work (follow-up).
6. **Cost cap:** only the first `reranker_top_n` candidates (already RRF-ordered) travel.

## Consequences

Measured on the GREEN run (real hook, real API, 2026-11-18):

| | Local (mxbai fp32 CPU) | Remote (cohere/rerank-5-fast) |
|---|---|---|
| Warm hook latency | p50 ~24 s | **0.9 s** end-to-end (daemon query 854 ms) |
| Daemon footprint | ~2.9 GB | dense+sparse only, reranker never loads |
| Cost | 0 | ~$2.7/month at the owner's volume |

- The hook now depends on the network when the provider is on; a network down degrades to
  hybrid order (no rerank, no floor) rather than to no injections. The local provider
  remains the default and the escape hatch.
- Journal rows carry a `rerank` provider tag (hook + MCP + `empty_row`); score comparisons
  across providers must split on it (`docs/usefulness-metrics.md`).
- Free-tier models are a trap for our shape: nvidia-free caps at ~20 documents (our hook
  sends 30) and runs 4–8× slower than the paid fast tier.
- Quality vs local mxbai is NOT yet measured — the golden set (50–100 real queries) gates
  any decision to make remote the owner's default everywhere and calibrates `min_score`.

## Alternatives Considered

- **MPS reranker** (issue #5): same model+dtype, ~10× faster, but competes for the GPU
  ferrohex measures under, and still pins ~2.4 GB in the daemon. Remote wins every axis
  on this machine; MPS remains the offline fallback if remote is ever off.
- **bf16/int8 reranker**: changes scores → needs golden-set recalibration anyway, keeps
  the memory pinned. Strictly dominated by remote here.
- **LLM-as-reranker via chat completions**: no native rerank endpoint shape, nondeterministic
  scores, prompt overhead. The native `/rerank` API supersedes it.
- **Smaller local cross-encoder (ms-marco-MiniLM)**: English-centric, quality risk on the
  ru/en corpus, still loads a model per process family. Not chosen.
