<div align="center">

# 🧠 supamem

**Qdrant-backed dual-memory for AI coding agents**

*Give Claude Code, Cursor, and OpenCode persistent semantic + structural memory across every project.*

[![PyPI](https://img.shields.io/badge/pypi-coming%20v0.2-blue?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

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

## 🚀 Features

| Feature | Description |
|---------|-------------|
| 🔍 **Hybrid retrieval** | Tuned sparse (BM25) + dense (MiniLM) fusion, locked schema D-25 |
| 📚 **Markdown chunker** | Header-aware, 200-token chunks with 250-token soft max (T-1) |
| 🤖 **MCP server** | `stdio` (default) and `http` transports, official `mcp` SDK |
| 🪝 **Multi-client hooks** | Claude Code session-start, OpenCode session-start, Cursor MDC |
| 🧰 **One-command install** | Atomic config patching with auto-backup and rollback |
| 🩺 **`supamem doctor`** | Probe Qdrant, resolve config chain, surface version drift |
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

> **Note:** `v0.1.0` ships as a git-tag release per [D-44](docs/decisions). PyPI publish lands in
> `v0.2`. To install the pre-release directly from git:
>
> ```bash
> uv tool install git+https://github.com/dzmitrys-dev/supamem@v0.1.0
> ```

---

## 🎯 CLI surface

| Command | Purpose |
|---------|---------|
| `supamem init` | Greenfield bootstrap — probes Qdrant, creates collection, writes `.supamem/config.toml` |
| `supamem install --client <name>` | Patch a client config (`claude-code`, `cursor`, `opencode`) — atomic with backup |
| `supamem index` | Embed dev memories into Qdrant using the locked tuned-hybrid pipeline (D-25) |
| `supamem mcp-server` | Run the MCP server (`--transport stdio` default; `--transport http` for HTTP) |
| `supamem hook <client>` | Per-client session/edit hooks (called by the client itself) |
| `supamem doctor` | 🩺 Probe Qdrant, print resolved config chain, report version drift |
| `supamem stats` | Welford schema-v2 usage counters from `.supamem/state/` |
| `supamem migrate` | Brownfield migration from a pre-existing `dev_memory` collection |
| `supamem eval` | Run the regression harness against the bundled 33-query golden corpus |
| `supamem uninstall --client <name>` | Reverse `supamem install` cleanly |

Every long-running command shows a **live spinner** with elapsed time so you always know it's
working. Use `--help` on any subcommand for details.

---

## 🪛 Wiring into your client

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code
```

Adds an entry to `~/.claude.json` under `mcpServers` and registers a session-start hook under
`~/.claude/hooks/`. Preview without applying with `--dry-run`.

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor
```

Patches `.cursor/mcp.json` and writes `.cursor/rules/dual-memory.mdc`.

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

Updates `~/.config/opencode/opencode.json` and writes a session-start hook to
`~/.config/opencode/hooks/`.

</details>

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
- **MCP server** exposes `qdrant-find` and `qdrant-store` tools, plus context resources
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
