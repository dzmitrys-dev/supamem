**Languages:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<div align="center">

# 🧠 supamem

**Qdrant-backed dual-memory for AI coding agents**

*Give Claude Code, Cursor, and OpenCode persistent semantic + structural memory across every project.*

[![PyPI](https://img.shields.io/pypi/v/supamem?style=flat-square&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

---

> ### 👋 Built by [**Dzmitry Sukhau**](https://www.linkedin.com/in/dzmitrys/) — AI-native Solution / Software Architect / CTO
>
> Available for **consulting** on AI products, **integrating AI into existing products**, and **business-process automation**.
>
> If you're shipping LLM features, evaluating retrieval pipelines, hardening agentic systems, or building an AI-first product from scratch — let's talk.
>
> [![LinkedIn — Dzmitry Sukhau](https://img.shields.io/badge/LinkedIn-Dzmitry%20Sukhau-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)
> &nbsp;&nbsp;
> [![Open to Consulting](https://img.shields.io/badge/Open%20to-Consulting%20%26%20Architecture-22C55E?style=for-the-badge&logo=anthropic&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)

---

## ✨ What is supamem?

`supamem` is a single-binary CLI that wires up a **production-grade memory layer** for any AI coding
assistant. Drop it into a fresh repo, run `supamem init`, and your agents instantly gain:

- 🔍 **Semantic search** over project notes, ADRs, decisions, and past conversations (hybrid sparse+dense retrieval)
- 🤖 **MCP server** that any compatible client (Claude Code, Cursor, OpenCode) can talk to
- 🪝 **Per-client hooks** that auto-load relevant memory at session start and on file edits
- 📊 **Welford usage stats** so you can see what memory is actually being recalled
- 🧪 **Eval harness** with a 33-query golden corpus to detect retrieval regressions

Battle-tested inside [SoftChat](https://app.softchat.ru) (Phases 80.1–80.5) before being extracted
into a standalone package every team can adopt.

---

## 🎯 Why supamem exists

**The problem:** Coding agents have no memory between sessions. Every time you open a new
conversation in Claude Code / Cursor / OpenCode, the model has zero context about your codebase,
past decisions, ADRs, known issues, or conventions. So either:

1. You **re-paste 5–15 KB of context** at the start of every session (slow, error-prone, costly), or
2. You let the agent **flounder** — it grep-walks the repo, asks redundant questions, forgets last
   week's decisions, and rediscovers the same gotchas you already documented six months ago.

**The fix:** A persistent semantic + structural memory layer that automatically retrieves the
*right* 1–2 KB of context for the *current* prompt — no manual pasting, no re-explaining, no
context blow-out.

> **Phase 80.1 bench (33 labeled goldens, real Claude Code sessions):**
> **−78.5% tokens vs naive whole-doc retrieval** at the same recall, **p95 73 ms** end-to-end.
>
> The full evaluation is the same one we ran inside SoftChat to lock the production pipeline.
> Methodology: 33 representative dev queries → 4 retrieval arms compared (baseline_union,
> tuned_current, tuned_hybrid, mem0_vector) → token count + recall CI + latency measured per arm.

### 📊 Token consumption: agent with memory vs without

Numbers below are per **typical 30-turn Claude Code session** assuming a real codebase with
~50 ADRs / insights / rules (≈ what SoftChat ships). YMMV — but the *ratio* between arms holds.

| Approach | Tokens/turn | Tokens/30-turn session | Notes |
|----------|------------:|-----------------------:|-------|
| ❌ No memory layer | **≈ 0** auto-injected, but you paste context manually | **30,000–80,000** (manual paste, repeated) | You spend cognitive load on copying instead of building |
| ⚠️ Naive RAG (whole-doc embed) | ~5,800 / turn | **~174,000** | Bloated, recalls big files when you only needed a paragraph |
| ✅ **supamem `tuned_hybrid`** | **~1,250 / turn** | **~37,500** | Same recall, **−78.5% tokens** vs naive RAG |

### 💰 Approximate inference cost savings

Anthropic API list pricing (Mar 2026):
**Sonnet 4.6 = $3 / Mtok input** · **Opus 4.7 = $15 / Mtok input**.

| Model | Tokens saved/session vs naive RAG | Cost saved/session | Monthly (110 sessions) |
|-------|----------------------------------:|-------------------:|-----------------------:|
| Sonnet 4.6 | **136,500** | **$0.41** | **~$45/dev** |
| Opus 4.7 | **136,500** | **$2.05** | **~$225/dev** |

A 10-engineer team running Opus saves **~$2,250/month** on input tokens alone — without
counting the cost of slower iteration, lost decisions, and time spent re-pasting context.
Output token savings (less hallucination, fewer back-and-forth turns) compound on top.

### 🥊 vs the alternatives

| | No memory | Naive RAG | mem0 / atomic facts | **supamem (tuned_hybrid)** |
|---|:---:|:---:|:---:|:---:|
| Auto-inject on session start | ❌ | ⚠️ | ✅ | ✅ |
| Hybrid sparse+dense retrieval | ❌ | ❌ | ❌ | ✅ |
| Code-identifier preservation | ❌ | ✅ | ❌ (drops names) | ✅ |
| Locked schema + golden eval | ❌ | ❌ | ❌ | ✅ |
| Multi-client (Claude/Cursor/OpenCode) | ❌ | ❌ | ⚠️ | ✅ |
| p95 latency | n/a | ~120 ms | ~80 ms | **73 ms** |
| Token bloat | High (manual) | Highest | Low but lossy | **Lowest with full recall** |

**Why hybrid?** BM25 catches *exact identifiers* (`ChatService.generate`, env-var names,
file paths) that dense embeddings smear. Dense catches *semantic intent* ("how do we
handle billing webhooks?") that BM25 misses. RRF fusion combines both rankings so you
get the best of each.

**Why not mem0?** mem0's atomic-fact extraction loses code identifiers — recall on the
33-query bench was **0.015** (effectively zero). Great for personal CRM-style memory,
not for code-aware retrieval.

---

## ⚡️ 60-second quickstart

```bash
# 1. Install (uv is the fastest path)
uv tool install supamem

# 2. Start Qdrant (one-time, ~30s)
docker run -d -p 6333:6333 -p 6334:6334 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest

# 3. Bootstrap your project
cd your-project
supamem init

# 4. Wire it into your AI client
supamem install --client claude-code   # or cursor, opencode

# 5. Confirm everything is healthy
supamem doctor
```

That's it. Open Claude Code (or your preferred client) inside the project — the memory tool is
already on the menu. ✨

---

## 👀 See it work — `supamem live`

Run `supamem live` in a side terminal to watch every retrieval call as it happens — perfect alongside Claude Code / Cursor / OpenCode for instant visibility into the silent PreToolUse-hook injections (which save tokens by NOT showing UI).

![supamem live dashboard](docs/media/supamem-live.svg)

The **SessionStart banner** (v0.1.4+) also lands a one-line status in your AI client at session open: `🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>` — auto-detects Claude Code / Cursor / OpenCode via env vars.

> 🎬 **Interactive demo:** [`supamem-live.cast`](docs/media/supamem-live.cast) — drop into [asciinema.org/player](https://asciinema.org/) or run locally with `asciinema play docs/media/supamem-live.cast`.

---

## 🚀 Features

| Feature | Description |
|---------|-------------|
| 🔍 **Hybrid retrieval** | Tuned sparse (BM25) + dense (MiniLM) fusion, locked schema D-25 |
| 🎯 **Code-aware reranker** | Cross-encoder `mxbai-rerank-base-v2` (Apache-2.0) rescores `tuned_hybrid` candidates by default. Disable with `retrieval.reranker = "off"` for pre-v0.2.4a1 behavior. (Phase 8, RERANK-01..04) |
| ⏳ **Per-source temporal validity** | Every chunk carries `valid_from`/`valid_to`; re-indexing a changed file supersedes prior chunks atomically and the retrieval-time filter excludes superseded points across every backend. Optional transcript-only recency decay (off by default). Auto-GC past `retention_days = 90` (set to `0` for kept-forever / audit collections). (Phase 9, TEMP-01..03) |
| 📚 **Markdown chunker** | Header-aware, 200-token chunks with 250-token soft max (T-1) |
| 🤖 **MCP server** | `stdio` (default) and `http` transports, official `mcp` SDK |
| 🪝 **Multi-client hooks** | Claude Code session-start, OpenCode session-start, Cursor MDC |
| 🧰 **One-command install** | Atomic config patching with auto-backup and rollback |
| 🩺 **`supamem doctor`** | Probe Qdrant, resolve config chain, surface version drift |
| 👀 **`supamem live`** | Rich-Live terminal dashboard tailing the audit JSONL — real-time visibility into retrieval calls (v0.1.4+) |
| 🎬 **SessionStart banner** | One-line cross-client banner injected at session open (Claude Code / Cursor / OpenCode), v0.1.4+ |
| 📊 **Welford counters** | Track recall rate, latency, query volume per project |
| 🧪 **Eval harness** | 33-query golden corpus + regression detection |
| 🔁 **Brownfield migration** | Detect existing `dev_memory` and migrate non-destructively |
| 🎨 **Stylish CLI** | Rich-powered spinners, panels, and color so you always see progress |

---

## 📋 Prerequisites

You only really need two things: **Python 3.12+** and **Qdrant**. Everything else is optional.

<details>
<summary><b>🐍 Python 3.12+ &nbsp;·&nbsp; click to expand install commands</b></summary>

```bash
# macOS (Homebrew)
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt install python3.12 python3.12-venv

# Windows (PowerShell)
winget install Python.Python.3.12
```

We strongly recommend installing [`uv`](https://docs.astral.sh/uv/) — the fastest Python package manager:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

</details>

<details>
<summary><b>🗄️ Qdrant 1.10+ &nbsp;·&nbsp; vector database (required)</b></summary>

The simplest path is Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $HOME/.qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

Or with `docker compose`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./qdrant_data:/qdrant/storage"]
    restart: unless-stopped
```

Don't have Docker? Run a managed cluster on [Qdrant Cloud](https://cloud.qdrant.io/) (free tier
available) and point `supamem` at the URL via `supamem init`.

</details>

<details>
<summary><b>🤖 An MCP-compatible client &nbsp;·&nbsp; pick at least one</b></summary>

| Client | Install | Notes |
|--------|---------|-------|
| [Claude Code](https://claude.com/claude-code) | `npm install -g @anthropic-ai/claude-code` | First-class MCP support |
| [Cursor](https://cursor.com/) | Download from cursor.com | Uses MDC rules + MCP |
| [OpenCode](https://opencode.ai/) | `curl -fsSL https://opencode.ai/install \| bash` | Open-source TUI, MCP native |

</details>

---

## 📦 Install

```bash
# Recommended: uv (fastest, isolated)
uv tool install supamem

# Alternative: pipx (also isolated)
pipx install supamem

# Plain pip (in a venv)
pip install supamem
```

Verify:

```bash
supamem --version
```

You should see a colorful banner and the credit line. 🎨

> **Latest:** `v0.1.4` is published on [PyPI](https://pypi.org/project/supamem/). Released via Trusted
> Publisher OIDC — every wheel is provenance-attested.

### Models cached at install

`supamem install <client>` and `supamem init` proactively download all ML prerequisites
(MiniLM ~90 MB, BM25 ~10 MB, mxbai-rerank-base-v2 ~1 GB) with a progress bar. Cold
post-install CLI invocations (`supamem --help`, `supamem doctor`, `supamem --version`)
trigger zero network egress. Air-gapped first-run? Pass `--skip-models`, then run
`supamem repair` once network is available.

Models live under `platformdirs.user_cache_dir("supamem")/models/` (override with
`SUPAMEM_CACHE_DIR`).

### Subagent reachability (v0.2.5+)

If you use Claude Code subagents shipped by GSD, superpowers, hookify, or any plugin that
pins a `tools:` whitelist on its agent definitions, those agents cannot reach the supamem
MCP server unless `mcp__supamem__*` is in the whitelist — even when the parent session has
supamem connected. Subagents inherit only the tools their frontmatter lists.

`supamem install` and `supamem repair` patch this for you automatically:

```bash
supamem install --client claude-code   # patches ~/.claude/agents/ + <project>/.claude/agents/
supamem repair                         # re-applies if a plugin overwrites your agents
```

The patcher is idempotent (running twice produces zero changes), preserves your YAML style
(CSV vs list), and skips symlinked agent files with a warning. Files with a missing or empty
`tools:` line have full inheritance per Claude Code semantics and are left untouched.

Backup manifest lives at `~/.cache/supamem/agent_patches.json`. Reverse cleanly with:

```bash
supamem unpatch-agents
```

Pass `--skip-patch-agents` to opt out on any of `install` / `init` / `repair`.

#### Uninstalling supamem

```bash
supamem unpatch-agents      # restore agent whitelists first
pip uninstall supamem
```

There is no portable `pip` / `uv` / `pipx` uninstall hook in 2026, so the two-step is the
supported contract. `supamem doctor` shows the manifest path and reminder so you can
discover this flow naturally.

---

## 🎯 CLI surface

| Command | Purpose |
|---------|---------|
| `supamem init` | Greenfield bootstrap — probes Qdrant, creates collection, writes `.supamem/config.toml` |
| `supamem install --client <name>` | Patch a client config (`claude-code`, `cursor`, `opencode`) — atomic with backup. Defaults to `--scope project` (per-workspace files); pass `--scope user` for legacy global behavior. Pass `--enforce-search` (claude-code only) to wire the opt-in edit-gate hook. v0.2.5+: auto-patches `~/.claude/agents/` and `<project>/.claude/agents/` to add `mcp__supamem__*` to restrictive `tools:` whitelists; opt out with `--skip-patch-agents`. |
| `supamem repair` | 🩹 Migrate from legacy global install to per-workspace files. Strips stale `mcpServers.supamem` from globals and re-installs at project scope from the current cwd. v0.2.5+: re-applies subagent reachability patches. Idempotent. Supports `--skip-patch-agents`. |
| `supamem index` | Embed dev memories into Qdrant using the locked tuned-hybrid pipeline (D-25) |
| `supamem mcp-server` | Run the MCP server (`--transport stdio` default; `--transport http` for HTTP) |
| `supamem hook <client>` | Per-client session/edit hooks (called by the client itself) |
| `supamem doctor` | 🩺 Probe Qdrant, print resolved config chain, report version drift |
| `supamem stats` | Welford schema-v2 usage counters from `.supamem/state/` |
| `supamem live` | 👀 Live dashboard tailing the audit JSONL — pipe-safe (plain JSONL when not a TTY); handles rotation, resize, Ctrl-C |
| `supamem migrate` | Brownfield migration from a pre-existing `dev_memory` collection |
| `supamem eval` | Run the bench harness. `--suite goldens` (default, bundled 33-query regression corpus), `--suite longmemeval_s` (lazy-fetched LongMemEval_S, ~3 GB on first run; **DEMOTED to on-demand-only in v0.3.0a5** per [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) — no longer the Phase 13 ship gate), `--suite longmemeval_scoped_smoke` (bundled, ≤5 questions, no lazy-fetch — stays on PR-CI), or **`supamem eval --suite coderag [--full] [--out PATH] [--peer mem0]`** (v0.3.0a5+, code-shaped retrieval suite — new Phase 13 ship gate). Outputs an MTEB-style JSON envelope to `~/.supamem/eval/<utc-iso>.json`. Default judge is heuristic (offline); pass `--judge ollama:<model>` for a localhost Ollama judge — SaaS endpoints are refused (D-07). Optional extras: `pip install supamem[eval]` for the RAGAS triad + `pytrec_eval`; `pip install supamem[peers-mem0]` for the mem0 peer adapter (v0.3.0a5+). Legacy `--regress` mode preserved. |
| `supamem uninstall --client <name>` | Reverse `supamem install` cleanly. Strips supamem from BOTH project and user scopes. |
| `supamem unpatch-agents` | 🔄 Reverse subagent reachability patches (v0.2.5+). Restores agent files to their pre-patch form per the manifest at `~/.cache/supamem/agent_patches.json`. Skips files you've edited since with a per-file warning. Run BEFORE `pip uninstall supamem` for a clean uninstall. |

### Environment variables

| Var | Purpose |
|-----|---------|
| `SUPAMEM_PROJECT_ROOT` | Absolute path to the workspace. Honored first by `mcp-server` for project resolution; injected automatically by `supamem install --scope project` so MCP hosts that launch the subprocess from the wrong cwd still resolve the right collection. |
| `SUPAMEM_CONFIG` | Explicit TOML path overriding all discovery. Highest precedence. |
| `SUPAMEM_GATE_DISABLE=1` | Bypass the opt-in claude-code edit-gate for the current session (`--enforce-search` users only). |
| `SUPAMEM_ADVISORY_DISABLE=1` | Suppress the Cursor `beforeSubmitPrompt` advisory hook. |
| `SUPAMEM_NO_UPDATE_CHECK=1`, `NO_UPDATE_NOTIFIER=1`, `CI=1` | Suppress the GitHub Releases probe. |
| `SUPAMEM_BANNER_DISABLE=1` | Suppress the SessionStart one-line banner entirely (no context injection, no user-visible status). |
| `SUPAMEM_BANNER_QUIET=1` | Suppress only the **user-visible** terminal status line; keep injecting the banner into Claude Code's `additionalContext` for the model. Use this when you want supamem context loaded but no per-session `SessionStart:supamem says: …` row in your terminal. |

### SessionStart banner format

Every supported client emits a one-line status at session open:

```
🧠 supamem ✓ v0.2.0 · supamem-myproject · 412 chunks · audit /home/me/.cache/supamem/audit.jsonl
          ^── health flag (✓ healthy / ⚠ misconfigured or qdrant unreachable)
```

When a newer release is locally cached by the background update probe, an
`update v0.X.Y available` segment is appended. Healing is never automatic —
the banner only signals; run `supamem repair` to act.

Every long-running command shows a **live spinner** with elapsed time so you always know it's
working. Use `--help` on any subcommand for details.

---

## 📜 Transcript ingestion (v0.2.2a1+)

supamem can index your **Claude Code session history** as Q+A drawer chunks alongside your
project's Markdown corpus, surfacing past decisions and tool-use traces in `dual_memory_search`.
Default-OFF — opt in with `--transcripts`.

```bash
# Index Claude Code transcripts from the default location (~/.claude/projects/)
supamem index --transcripts

# Or point at a specific directory
supamem index --transcripts /path/to/sessions/

# Skip the regular project corpus and only index transcripts
supamem index --transcripts --transcripts-only

# Limit to recent sessions (default: 180 days; --since 0 disables the filter)
supamem index --transcripts --since 30d
```

Configure under `[supamem.transcript]` in `.supamem/config.toml`:

```toml
[supamem.transcript]
default_root           = "~/.claude/projects/"
since_days             = 180
tool_payload_max_chars = 2000
chunk_soft_max_tokens  = 600
include_paths_glob     = []
exclude_paths_glob     = []   # exclude sensitive sessions, e.g. ["**/banking-*.jsonl"]
```

> ⚠ **Transcripts may contain secrets.** API keys, tokens, and other credentials sometimes end up
> pasted into Claude Code sessions. v0.2.2a1 ships **no redaction** — review your
> `~/.cache/supamem` Qdrant collection before sharing it. Hand-exclude sensitive sessions via
> `exclude_paths_glob`. Redaction is tracked for v0.3 via a future `supamem.redactor` plugin group.

Currently supported transcript formats: **Claude Code JSONL** (Cursor SQLite and ChatGPT export
are deferred to follow-on plugins).

---

## 🔎 Scoped retrieval (v0.2.3a1+)

Filter retrieval by coding-shaped category via the `where` parameter on
`dual_memory_search` (and the `qdrant_find` alias):

```python
# Only chunks classified as backend code
dual_memory_search(query="auth flow", where={"room": "backend"})

# OR across rooms (Qdrant MatchAny)
dual_memory_search(query="rate limit", where={"room": ["backend", "tests"]})
```

Every indexed chunk carries `payload.room` — one of `backend`, `frontend`,
`tests`, `docs`, `scripts`, `config`, `migrations`, `types`, or `null`.
Classification is **exact path-component equality** (split on `/`) — a file
at `data/chest_xray/img.png` is NEVER classified as `tests`. Multiple keys
in `where` are AND; list values within a key are OR.

Override the default keyword map in `.supamem/config.toml`:

```toml
[supamem.classifier.rooms]
tests      = ["tests", "test", "__tests__"]
backend    = ["src", "backend", "api"]
frontend   = ["frontend", "web", "client", "components"]
# Priority is encoded by key order — first match wins.
# Putting `tests` before `backend` makes tests/backend/api_test.py classify as `tests`.
```

`supamem doctor` surfaces the active rooms map with `[source: ...]` provenance,
the stored `classifier_hash`, and a per-room histogram (including a `null` bucket).

Changing `[supamem.classifier.rooms]` triggers a one-time **re-classify sweep** on
the next `supamem index` — Qdrant `set_payload` per-room, **zero re-embedding cost**.
Pre-v0.2.3 collections auto-migrate on first post-upgrade index invocation.

Transcript chunks (chunker == `transcript`) classify to `room = null` by construction —
filter them via the existing `payload.chunker` key.

---

## 🎯 Code-aware reranker (v0.2.4a1+)

Every `tuned_hybrid` query now rescores RRF-fused candidates through a cross-encoder
(`mixedbread-ai/mxbai-rerank-base-v2`, Apache-2.0, ~1 GB) by **default**. Sharper
precision on code-shaped queries; the v0.2.0 escape hatch is `retrieval.reranker = "off"`,
which restores pre-Phase-8 byte-identical behavior.

```toml
[supamem.retrieval]
reranker = "mxbai_v2"  # default in v0.2.4a1+; "off" restores pre-Phase-8 behavior

[supamem.retrieval.reranker]
model_id         = "mixedbread-ai/mxbai-rerank-base-v2"
top_n            = 50   # rerank pool size; clamps to fused-candidate count
prefetch_per_arm = 50   # widened from default 20 when reranker is on
batch_size       = 16
```

When the reranker is on, `tuned_hybrid` widens `PREFETCH_LIMIT` to 50 per arm, skips
the T-4 recency multiplier (cross-encoder + recency-prior is anti-aligned for code
retrieval per PROJECT.md), and runs T-5 cosine-dedup + T-8 token-budget AFTER
rerank. `RetrievedChunk.rerank_score` carries the cross-encoder logit when reranker
is on; the primary `score` is replaced by it.

`supamem doctor` adds a **Reranker** panel after the existing Retrieval panel:
active reranker name, model_id, cache path, on-disk size + partial-download
detection, last-load latency, last-100-query rerank p50/p95, and detected device
(cuda/mps/cpu). When the cache is partial or corrupted, run `supamem repair` —
the canonical doctor-driven self-heal entry point that re-fetches missing model
files, re-syncs `share/`, repairs managed CLAUDE.md/AGENTS.md blocks, and
restores client config. Idempotent.

Third parties register custom rerankers via the new `supamem.reranker` plugin
entry-point group (4th group alongside retrieval / embedder / chunker):

```toml
[project.entry-points."supamem.reranker"]
my_reranker = "my_pkg.module:MyReranker"
```

Plugin protocol: `rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]`.
Lazy model-load on first call; eager warm-up runs through the install/init/repair
fetch pipeline.

---

## ⏳ Per-source temporal validity (v0.3.0a1+)

Every indexed chunk carries a binary `valid_to` field:

- `valid_to = null` → live
- `valid_to ≤ now()` → superseded (filtered out of every retrieval)

When a file changes and you re-index, the indexer atomically:

1. Scrolls every existing chunk for that file path.
2. Sets `valid_to = now()` on each (closes the prior validity window).
3. Upserts the new chunks under content-hash-keyed UUIDs with `valid_to = null`.

Old and new chunks coexist in Qdrant; only the new ones are returned by retrieval until
the auto-GC sweep deletes the old ones past `retention_days`. The retrieval-time filter
is constructed at a single site and inherited by every backend (`tuned_hybrid` both
Prefetch arms, `dense`, `bm25`, `qdrant_find`, `dual_memory_search`) — uses Qdrant's
`IsEmptyCondition` on `valid_to` (NOT `IsNullCondition` — see
[Qdrant#5342](https://github.com/qdrant/qdrant/issues/5342): `IsNull` does not match
missing fields).

Configure in `.supamem/config.toml`:

```toml
[supamem.retrieval.temporal]
retention_days = 90          # 0 = kept forever (compliance / audit collections)
```

### Transcript-only recency decay (opt-in, default OFF)

Code, ADRs, and docs do not "go stale". Transcripts often do — older support-chat turns
with deprecated APIs distract the agent from the current dialogue. Phase 9 ships an
opt-in multiplicative-floor decay knob that runs **only** on transcript chunks, after
rerank, never auto-enabled for code / ADR / doc:

```toml
[supamem.retrieval.recency.per_source.transcript]
enabled        = true            # default false
half_life_days = 14.0
alpha          = 0.7             # floor: oldest transcript still gets 0.7x its score
```

Worked example with the locked defaults (`alpha = 0.7`, `half_life_days = 14`):

| Age (days) | Multiplier         |
|------------|--------------------|
| 0          | 1.000              |
| 7          | 0.924              |
| 14         | 0.850              |
| 28         | 0.775              |
| ∞          | 0.700 (floor at α) |

Code / ADR / doc rankings stay byte-identical when the knob is flipped — verified by
an end-to-end byte-identity test (TEMP-03 success criterion).

References: [Customers.ai recency-weighted scoring](https://customers.ai/recency-weighted-scoring),
[Snowflake Cortex Search scoring docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-customize-scoring).

### Doctor surface

`supamem doctor` shows a `Temporal validity` panel (between Reranker and Subagent
reachability) listing live / superseded / awaiting_gc / future-dated counts, per-source
breakdown, oldest + newest `valid_from` across your collection, and `retention_days`
provenance. Read-only by construction; never flips the doctor exit code.

### Migration

First post-upgrade `supamem index` back-fills `valid_to=null` on legacy points (gated
by a manifest reserved key, idempotent on subsequent runs). Defense-in-depth alongside
the `IsEmpty` runtime filter.

> ⚠ **Default retention is destructive** for users upgrading from v0.2.x with audit-mode
> collections older than 90 days. Set `[supamem.retrieval.temporal] retention_days = 0`
> to disable auto-GC entirely.

---

## 🔭 Filtered retrieval backend (v0.3.0a3+)

`filtered_dense` is a scoped+capped retrieval backend that wraps `tuned_hybrid` with a
`where` filter and a per-hit preview cap. Use it when you want backend-level enforcement
of "give me ranked results scoped to *this* path/room, with previews capped at *N* chars
before they ever leave Qdrant".

```toml
[supamem.retrieval]
backend = "filtered_dense"

[supamem.retrieval.filtered_dense]
preview_chars = 240   # default 240; 0 disables truncation entirely
```

Selection mirrors every other backend (`tuned_hybrid`, `dense`, `bm25`) — registered via
the `supamem.retrieval` plugin entry-point group; switching is a config-only change with
no code edits. The MCP transport cap (`mcp.caps.max_preview_chars`) continues to apply on
top of the backend cap; both are independently disable-able by setting to `0`.

### `where` filter — magic keys

`dual_memory_search` (and the `qdrant_find` alias) accept a `where: dict[str, str | list[str]]`
parameter that translates to a Qdrant payload filter. Beyond the Phase 7 `room` key, two
new magic keys are recognized:

```python
# 1. path_prefix — left-anchored exact path-segment match
dual_memory_search(query="auth flow", where={"path_prefix": "src/supamem/retrieval"})

# OR across multiple prefixes (Qdrant MatchAny)
dual_memory_search(
    query="rate limit",
    where={"path_prefix": ["src/supamem", "tests/test_filtered_dense.py"]},
)

# 2. valid_to: "now" — no-op alias for the always-on temporal clause (Phase 9)
dual_memory_search(query="session", where={"valid_to": "now"})
```

Semantics:

- **`path_prefix`** is left-anchored on `/`-segment boundaries. Indexer stores
  `payload.path_prefixes: list[str]` per chunk (e.g. `src/supamem/retrieval/filters.py`
  → `["src", "src/supamem", "src/supamem/retrieval", "src/supamem/retrieval/filters.py"]`).
  `path_prefix="src/supa"` does **not** match `src/supamem/...` because `"src/supa"` is
  not a stored prefix segment — only complete `/`-segment boundaries match (mirrors
  filesystem path semantics).
- **`valid_to: "now"`** is accepted as a no-op alias documenting the always-on Phase 9
  temporal clause. Any other value raises `ValueError` — time-travel queries are out of
  scope. Use `retention_days` to control which historical chunks remain in the
  collection.

Multiple `where` keys are AND'd; list values within a key are OR'd (`MatchAny`).

| Key | Semantics |
|-----|-----------|
| `room` | Phase 7 — coding-path classifier facet (`backend`, `frontend`, `tests`, ...). String or list. Set by `supamem index` per-chunk. |
| `path_prefix` | Phase 11 — left-anchored exact path-segment match against `payload.path_prefixes`. String or list. Set by `supamem index` per-chunk. |
| `valid_to` | Phase 9 — accepts only `"now"` as a no-op alias for the always-on temporal clause. Any other value raises `ValueError`. |
| `session_id` | **Bench-only** — set by LongMemEval ingestion (`supamem.eval.longmemeval_ingest`); pass-through key. **NOT settable by `supamem index`.** Used by the Phase 14 scoped bench pass against the dedicated `supamem_eval_longmemeval_s` collection. See [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md). |
| `repo` | **Bench-only** (v0.3.0a5+) — set by `coderag` ingestion (`supamem.eval.coderag.ingest`); pass-through key. Values: `"supamem"`, `"fastapi"`. **NOT settable by `supamem index`.** Used by the Phase 15 three-column reporting (`supamem_only` / `fastapi_only` / `combined`) against `supamem_eval_coderag`. See [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md). |
| `axis` | **Bench-only** (v0.3.0a5+) — set by `coderag` ingestion; pass-through key. Values: `"code_fact"`, `"decision_rationale"`. **NOT settable by `supamem index`.** Used by the per-axis metric aggregation. See [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md). |

### Migration

Legacy chunks (indexed before v0.3.0a3) lack `path_prefixes`. The first post-upgrade
`supamem index` runs a one-shot eager scroll-and-`set_payload` sweep that back-fills
`path_prefixes` per chunk — pure metadata update, **zero re-embedding cost**, idempotent
on subsequent runs. No `--force` reindex required.

### Doctor surface

`supamem doctor` adds a "Filtered-dense backend" panel surfacing the resolved
`preview_chars` value with `[source: ...]` provenance. Read-only by construction; never
flips the doctor exit code.

---

## 📊 Benchmarks (v0.3.0a4+)

**Methodology change.** `supamem eval --suite longmemeval_s` emits both an
**unscoped** and a **scoped** retrieval pass per question. The scoped pass
uses a per-question `where` filter derived from LongMemEval haystack session
ids (`{"session_id": [...]}`), exercising the indexer-side filter payloads
(`room`, `path_prefix`, `valid_to`, `session_id`) added across Phases
7 / 9 / 11 / 14. The published gate decision (`tokens_per_correct_answer`
delta vs the v0.1.5 baseline) reads the **scoped** pass; unscoped is reported
in the same envelope for transparency only — it never gates. See
[ADR-0001](docs/adr/0001-scoped-only-bench-gate.md) for the full rationale.

**Reproducibility caveat.** Scoped numbers may not reproduce in default
unscoped invocations of `dual_memory_search` / `qdrant_find`. Users who want
comparable numbers must pass an explicit `where={...}` filter against a
collection whose chunks carry the matching payload — this is a methodology
disclosure, not a defect.

**Baseline corpus.** The v0.1.5 baseline was re-captured against a dedicated
bench collection (`supamem_eval_longmemeval_s`). Pre-Phase-14 absolute
numbers are not directly comparable to post-Phase-14 numbers — the corpus
changed. The original devdocs-collection number is preserved as
`legacy_devdocs_unscoped_tpca` in `eval/baselines/v0.1.5.json` for
historical reference but does NOT gate.

**FUTURE-24 (rerank composition rework)** is a sibling unblocker tracked
separately. Phase 14's scoped pass runs with rerank-OFF so the measured
scoped-vs-unscoped delta attributes cleanly to scoping. Public claims about
scoping gains do **not** extrapolate to assume FUTURE-24 will further close
the gap.

**Smoke fixture.** A bundled fixture at
`src/supamem/eval/datasets/longmemeval_scoped_smoke.json` (≤5 questions,
≤200 KB, self-contained) is exposed as the new suite
`longmemeval_scoped_smoke` — runs in CI without triggering the ~3 GB lazy
fetch.

### coderag (code-retrieval; Phase 15 — new Phase 13 ship gate, v0.3.0a5+)

`supamem eval --suite coderag [--full] [--out PATH] [--peer mem0]` runs
a deterministic two-repo code-retrieval haystack (supamem self +
[fastapi](https://github.com/fastapi/fastapi) external, both pinned to
commit-SHAs) with pure-auto queries derived from PR history (the
`code_fact` axis) and ADR Problem/Why sections (the
`decision_rationale` axis; **supamem-only** at the v1 corpus pin —
fastapi has no `docs/adr/` directory, so the three-column reporting
collapses on this axis).

Reports `Recall@k` (k ∈ {1, 5, 10, 20}), `MRR`, `nDCG@10`, and latency
p50/p95 in **three-column form** — `supamem_only` / `fastapi_only` /
`combined` — per axis. The three-column shape makes self-reference
circularity audit-visible: a reader can see whether a published
"Recall@5 = 1.000 on decision_rationale" came from supamem retrieving
its own ADRs (high self-reference; expected) or from a generalisable
signal that also holds on fastapi (it doesn't — fastapi has no ADRs at
the v1 corpus pin).

**Live three-run baseline (v0.3.0a6+).** ADR-0002 §7 floors are now
derived from the live 21,235-chunk corpus across 3 successive
variance-gated runs — Phase 15's offline `< 0.005 ms` latencies and
`1.000` recall cells (artefacts of the trivially-recovered 6-question
smoke fixture) are gone. The hard latency p95 ceiling moves
**500 ms → 5000 ms** as a **one-shot** forward-looking adjustment per
**D-LAT-01** — NOT a sliding scale; subsequent phases tighten or
hold, never relax. See
[ADR-0002 §7](docs/adr/0002-coderag-eval-philosophy.md#7-locked-numerical-floors-live-three-run-baseline-phase-16-e)
for the live floor tables and the explicit reasoning paragraph.

**Auto-queries-from-manifest in `--full` (v0.3.0a6+).** Full-mode
records come from `auto_queries.extract_pr_queries()` +
`extract_adr_queries()` against the populated corpus manifest (lazy
build-on-call via `corpus.ensure_populated_manifest`), NOT from the
6-question smoke fixture. Each record carries a `query_origin` field
(`pr_title` / `adr_problem` / `adr_why`) and a
`training_leakage_suspected` boolean. The smoke fixture continues to
drive the default offline path unchanged.

**Ship gate.** Phase 13 ships when `supamem eval --suite coderag --full`
reports no-regression vs the live baseline (Recall@k, MRR, nDCG@10
≥ baseline − ε; latency p95 ≤ baseline + ε **AND** ≤ 5000 ms hard
ceiling). ε is derived per metric: `ε_ranking = max(stddev, 0.005)`,
`ε_latency = max(0.05 × mean, 5ms)`.

**mem0 peer baseline + head-to-head (v0.3.0a6+).**
[mem0](https://github.com/mem0ai/mem0) runs as a parallel row with a
single canonical default config (no tuning matrix). It ingests source
documents into its OWN Qdrant collection (`supamem_eval_coderag_mem0`,
separate from `supamem_eval_coderag` — mem0 owns its schema; sharing
a collection would corrupt). Reported as a parallel row in the metric
envelope; never gates. v0.3.0a6 adds a paired-bootstrap delta + 95% CI
per axis × column × metric (sign convention `mem0_vs_supamem` — positive
delta = peer wins) at
[ADR-0002 §8](docs/adr/0002-coderag-eval-philosophy.md#8-mem0-peer-comparison-d-peer-04-live-phase-16-e-head-to-head).
Install with `pip install supamem[peers-mem0]`.

**LongMemEval demoted.** Full LongMemEval_S becomes on-demand-only as
of v0.3.0a5; the existing 5-question `longmemeval_scoped_smoke`
fixture stays on PR-CI. The diagnosis: LongMemEval measures
conversational long-term memory ("what car did I buy?"), while supamem
indexes code chunks consumed by AI coding agents — the gate was
**workload-misaligned**, not the tool. See
[ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) for the full
rationale.

**Smoke fixture.** A bundled
`src/supamem/eval/datasets/coderag_smoke.json` (6 questions across both
axes, ≤200 KB) drives offline PR-CI without live Qdrant.

---

## 🚫 What supamem does NOT do

`supamem` does **NOT** auto-inject identity / wake-up / prelude context into agent calls
— retrieval is always solicited via an explicit query. There is no hidden "agent
identity" tier, no SessionStart-time wake-up payload that pushes ambient context into
the model, no MCP tool that fires retrieval when the `query` is empty.

This is locked from two sides:

1. **Schema-level (v0.3.0a3+):** Every retrieval tool's `query` parameter is
   `Field(..., min_length=1, max_length=...)` — required, non-empty, schema-enforced at
   tool registration time. An empty `query` is rejected with a structured MCP
   validation error, not silently substituted with default context.
2. **Test-level (FILT-02):** `tests/test_no_identity_tier.py` is a CI-enforced
   regression test that fails the build if any registered MCP tool name matches
   `(?i)(wake[_-]?up|identity|prelude|inject)` OR if any retrieval tool's JSON Schema
   drops `query` from `required` / loses `minLength >= 1`.

If you want supamem context loaded at session-open, the existing SessionStart banner
hook is the supported surface — it injects a one-line status (collection, chunk count,
audit-log path), never silently pulls retrieval results into the model. The model still
has to `dual_memory_search` to read the corpus.

---

## 🪛 Wiring into your client

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code              # default: --scope project (per-workspace .mcp.json)
supamem install --client claude-code --scope user  # legacy global install in ~/.claude.json
supamem install --client claude-code --enforce-search  # also register the opt-in edit-gate
```

Default writes `<repo>/.mcp.json` (project-scope, committable; takes precedence over user-scope
per Anthropic MCP docs). Always registers the SessionStart banner + injection hook in
`~/.claude/settings.json`. With `--enforce-search`, also registers a PreToolUse gate that
DENIES `Edit|Write|MultiEdit` when no `mcp__supamem__dual_memory_search` is found in the
session transcript since the last user turn — override per-session with
`SUPAMEM_GATE_DISABLE=1`. Preview any command with `--dry-run`.

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor              # default: --scope project (<repo>/.cursor/mcp.json)
supamem install --client cursor --scope user  # legacy global install in ~/.cursor/mcp.json
```

Default writes `<repo>/.cursor/mcp.json` (per-workspace; project-level wins on conflict per
Cursor docs). Always writes `<repo>/.cursor/rules/dual-memory.mdc` and registers a
sessionStart snapshot hook + a `beforeSubmitPrompt` advisory in `<repo>/.cursor/hooks.json`.
The advisory injects an `agentMessage` reminder when the user's prompt looks edit-bound;
suppress with `SUPAMEM_ADVISORY_DISABLE=1`. (Cursor's hooks API doesn't yet support a
fail-closed pre-edit event — the advisory is the strongest available nudge.)

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

Updates `~/.config/opencode/opencode.json` and writes a session-start hook to
`~/.config/opencode/hooks/`.

</details>

> 🛟 **MCP launched from the wrong cwd?** Hosts (Cursor, some IDE wrappers) sometimes spawn the MCP subprocess from `$HOME` instead of the workspace, causing supamem to fall back to the default collection (`dev_memory_tuned_hybrid`) and return Qdrant 404s.
> Set `SUPAMEM_PROJECT_ROOT=/abs/path/to/workspace` in the host's MCP config (e.g. `~/.cursor/mcp.json` `env` block, or `~/.claude.json` under `mcpServers.supamem.env`).
> If unset, supamem will walk parents looking for `.supamem/config.toml` or `pyproject.toml` `[tool.supamem]` — and emit a one-line stderr warning when it can't find either.
> Verify with `supamem doctor` from the repo root: the resolved collection should match what your MCP client returns from `dual_memory_search`.

---

## 🧠 How it works

```text
┌─────────────────┐    MCP/stdio     ┌─────────────────┐    REST    ┌─────────────┐
│ Claude / Cursor │ ───────────────► │  supamem MCP    │ ─────────► │   Qdrant    │
│   / OpenCode    │ ◄─────────────── │     server      │ ◄───────── │  (vectors)  │
└─────────────────┘                  └─────────────────┘            └─────────────┘
        │                                    ▲
        │ session-start hook                 │ tuned-hybrid retrieval
        ▼                                    │ (BM25 + MiniLM fusion)
┌─────────────────┐                          │
│ supamem hook    │ ─────────────────────────┘
│  (auto-recall)  │
└─────────────────┘
```

- **Indexer** chunks Markdown by header (T-1 chunker, 200-token target / 250 soft max)
- **Embedders** produce sparse (BM25) and dense (MiniLM-L6) vectors
- **Retrieval** runs both arms in parallel, fuses with reciprocal rank fusion, returns top-k
- **MCP server** exposes `dual_memory_search` (read) and `dual_memory_write` (write/idempotent agent-memory persistence) — plus `qdrant_find` and `qdrant_store` as drop-in aliases for users coming from upstream `mcp-server-qdrant` (disable with `SUPAMEM_QDRANT_ALIASES=0`)
- **Hooks** call `supamem hook <client>` at the right moment, so memory loads transparently

---

## 🤝 Contributing

We welcome PRs! Quick start:

```bash
git clone https://github.com/dzmitrys-dev/supamem.git
cd supamem
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

Coming from an in-tree `dev_memory` setup? See [MIGRATION.md](MIGRATION.md).

---

## 📜 License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

### 💜 Delivered with care by

<a href="https://app.softchat.ru"><b>SoftChat</b></a> &nbsp;·&nbsp; <a href="https://softskillz.ai"><b>SoftSkillz</b></a>

*Russian-language AI chat platform &nbsp;·&nbsp; AI-first product engineering*

`supamem` was extracted from SoftChat's production memory stack so every team can run on the same
battle-tested pipeline. If it makes your agents smarter, give us a ⭐ — and check out what we
build with it.

<sub>Made with care in Belarus &nbsp;🇧🇾&nbsp; · &nbsp;<a href="https://app.softchat.ru">app.softchat.ru</a> &nbsp;·&nbsp; <a href="https://softskillz.ai">softskillz.ai</a></sub>

</div>
