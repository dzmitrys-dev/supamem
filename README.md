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

---

## 🎯 CLI surface

| Command | Purpose |
|---------|---------|
| `supamem init` | Greenfield bootstrap — probes Qdrant, creates collection, writes `.supamem/config.toml` |
| `supamem install --client <name>` | Patch a client config (`claude-code`, `cursor`, `opencode`) — atomic with backup. Defaults to `--scope project` (per-workspace files); pass `--scope user` for legacy global behavior. Pass `--enforce-search` (claude-code only) to wire the opt-in edit-gate hook. |
| `supamem repair` | 🩹 Migrate from legacy global install to per-workspace files. Strips stale `mcpServers.supamem` from globals and re-installs at project scope from the current cwd. Idempotent. |
| `supamem index` | Embed dev memories into Qdrant using the locked tuned-hybrid pipeline (D-25) |
| `supamem mcp-server` | Run the MCP server (`--transport stdio` default; `--transport http` for HTTP) |
| `supamem hook <client>` | Per-client session/edit hooks (called by the client itself) |
| `supamem doctor` | 🩺 Probe Qdrant, print resolved config chain, report version drift |
| `supamem stats` | Welford schema-v2 usage counters from `.supamem/state/` |
| `supamem live` | 👀 Live dashboard tailing the audit JSONL — pipe-safe (plain JSONL when not a TTY); handles rotation, resize, Ctrl-C |
| `supamem migrate` | Brownfield migration from a pre-existing `dev_memory` collection |
| `supamem eval` | Run the regression harness against the bundled 33-query golden corpus |
| `supamem uninstall --client <name>` | Reverse `supamem install` cleanly. Strips supamem from BOTH project and user scopes. |

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
