# AGENTS.md — supamem

Operational guide for AI coding agents working in this repository. Project-agnostic dual-memory CLI for Claude Code, Cursor, and OpenCode.

## Project Snapshot

- **Language:** Python 3.12+
- **Build backend:** hatchling
- **Package manager:** `uv`
- **CLI entry:** `supamem` → `src/supamem/cli.py:main`
- **Tests:** `pytest` (config in `pyproject.toml`)
- **Lint/format:** `ruff` (line length 100, target py312)
- **External deps:** Qdrant 1.10+ (HTTP), MCP 1.13+, fastembed, langchain-text-splitters

## Architecture

```
src/supamem/
├── cli.py              # Typer-based CLI dispatcher
├── console.py          # Shared Rich console + theme (single source of branding)
├── config.py           # Pydantic config schema (D-38)
├── config_io.py        # Discovery: project → user → defaults; merge order locked
├── doctor.py           # Health + drift report (Plan 80.6-11)
├── init.py             # Greenfield bootstrap (Plan 80.6-08)
├── migrate.py          # Brownfield migration paths (Plan 80.6-09)
├── mcp_server.py       # MCP server entrypoint
├── embedders/          # minilm, bm25 — pluggable via entry-points
├── indexer/            # chunker (markdown_header), manifest
├── retrieval/          # tuned_hybrid, dense, bm25 backends
├── eval/               # Bench runner + bundled goldens (Plan 80.6-12)
├── hooks/              # claude_code, cursor — per-client snapshot hooks
├── install/            # claude_code, cursor, opencode — config installers
├── share/              # Canonical artifact templates
└── stats/              # Welford counter for usage telemetry
```

## Plugin Entry Points (D-48)

Three plugin groups in `pyproject.toml`. Third parties add backends without forking:

- `supamem.retrieval` — `tuned_hybrid`, `dense`, `bm25`
- `supamem.embedder` — `minilm`, `bm25`
- `supamem.chunker` — `markdown_header`

## Config Discovery (D-38)

Order: `./.supamem/config.toml` (project) → `~/.config/supamem/config.toml` (user) → `share/default.toml` (shipped). Merge is shallow; project wins.

## Hard Constraints

- NEVER bypass failing tests — fix root cause
- NEVER hardcode collection names; always read from `config.collection`
- NEVER print to stdout in MCP server — JSON-RPC contract requires stdio purity (use `err_console`)
- NEVER write to `~/` outside `~/.cache/supamem/` and `~/.config/supamem/` without explicit user opt-in
- NEVER delete a Qdrant collection without `--force` flag confirmation
- ALWAYS use `console.py` exports for terminal output (no bare `print()`)
- ALWAYS run `pytest` from project root via `uv run pytest`

## Workflow

```bash
# Setup
uv sync --extra dev

# Tests
uv run pytest                      # full suite
uv run pytest tests/test_X.py -v   # single file

# Lint / type
uv run ruff check src tests
uv run ruff format src tests

# Build + verify
uv build
uvx twine check dist/*

# Run locally
uv run supamem --help
uv run python -m supamem doctor
```

## Test Discipline

- Subprocess-based CLI smoke tests (`test_cli_smoke.py`) MUST pin a deterministic env: `NO_COLOR=1`, `TERM=dumb`, `COLUMNS=200` — pop `FORCE_COLOR`. Rich autodetect alone is insufficient on CI runners.
- Use `pytest-asyncio` with `mode=Mode.STRICT`; mark async tests with `@pytest.mark.asyncio`.
- Mock Qdrant via fixtures, not by hitting a live instance — bench/eval suites are the integration boundary.

## Release Process

1. Bump `version` in `pyproject.toml` and add `CHANGELOG.md` entry.
2. Verify license metadata complies with PEP 639 (`license = "MIT"` SPDX, no `License ::` classifier).
3. `uv build && uvx twine check dist/*`
4. Create annotated tag: `git tag -a vX.Y.Z -m "..."`
5. Push tag: `git push origin vX.Y.Z` — release workflow publishes to PyPI via Trusted Publisher OIDC.
6. Verify on PyPI: `pip install supamem==X.Y.Z` in a clean venv.

PyPI tags are immutable: never re-use a published version number.

## Update-check (v0.1.1+)

A daemon thread probes GitHub Releases on every CLI invocation, caches result for 24h in `platformdirs.user_cache_dir("supamem")/update_check.json`, and prints a stderr footer on the *next* invocation if a newer version is available. Suppress with `SUPAMEM_NO_UPDATE_CHECK=1`, `CI=1`, or `NO_UPDATE_NOTIFIER=1`. Never blocks; never raises.

## llms.txt is MANDATORY

The repo ships `llms.txt` (https://llmstxt.org/) — a curated, LLM-friendly index of the package's docs, CLI surface, MCP tools, and external links. It is the canonical source LLM consumers use to understand supamem WITHOUT crawling the full README.

**You MUST update `llms.txt` whenever ANY of these change**:

- README.md content reorganized or new sections added
- A CLI subcommand is added/removed/renamed
- An MCP tool is added/removed/renamed (including aliases)
- A new public env var, config key, or major version is shipped
- An external link reference (PyPI, CHANGELOG, MIGRATION, docs site) moves
- Translations are added or removed (the "## Translations" section)

Treat `llms.txt` like a public API doc: drift between code and llms.txt is a documentation bug, not "out of date but harmless". Verify after every release with: `grep -n "v0\." llms.txt` to catch stale version mentions.

## GSD planning artifacts (local-only)

When you use GSD (https://github.com/gsd-build/get-shit-done) workflows to plan supamem work, the resulting `.planning/`, `.gsd/`, `.continue-here.md`, `HANDOFF-*.md` files are LOCAL planning state. They are gitignored and MUST NOT be committed to the package repo — supamem ships as a clean Python package, not as a planning workspace. Same applies to `.supamem/` and `.claude/insights/_agent/` if a developer runs the tool against the supamem repo itself.

If you need durable design records, use `docs/adr/`, `CHANGELOG.md`, or commit messages — these are intended for the public.

## README Translations (v0.1.3+)

The repo ships **5 READMEs**: `README.md` (canonical English) + `README.zh-CN.md`, `README.es.md`, `README.ja.md`, `README.ru.md`. PyPI renders only the English file (`readme = "README.md"` in pyproject); translations are GitHub-only.

**When you edit `README.md`, you MUST**:

1. Apply the same edit to all 4 translations (`README.{zh-CN,es,ja,ru}.md`). Code blocks, badges, file paths, and CLI commands stay in their canonical English form — translate only prose, headings, and explanatory text.
2. Bump the `<!-- synced-with: README.md @ <sha> -->` marker (line 2 of each translation) to the new commit SHA *after* your README commit lands. One-liner:
   ```bash
   SHA=$(git rev-parse --short HEAD)
   sed -i "s|synced-with: README.md @ [a-f0-9]\{6,\}|synced-with: README.md @ $SHA|g" \
     README.zh-CN.md README.es.md README.ja.md README.ru.md
   ```
3. NEVER ship a README change without updating the language switcher line at the top — all 5 files share the identical `[English](README.md) · [简体中文](README.zh-CN.md) · ...` line as their first line.
4. If the English README adds a NEW section, translate it. If a section is removed in English, remove from translations too. Drift is the failure mode — keep them in lockstep.
5. Translations are AI-assisted (disclosure on line 5 of each file). Native-speaker corrections via PR are encouraged; do NOT silently overwrite human-improved phrasing in subsequent updates — preserve any commit-history evidence of native edits.

CI guard candidate: a script that fails when `README.md` changes without bumping the `synced-with` SHA in siblings (deferred — add when drift first bites).

## Reference Links

- README: high-level overview, install, quickstart
- MIGRATION.md: version-to-version upgrade notes
- CHANGELOG.md: release log
- `docs/` (if present): ADRs, design docs

## Decision Guides

- New backend: register via entry-point group, do NOT modify `cli.py` dispatch
- New CLI subcommand: add Typer command in `cli.py`, route to module under `src/supamem/`
- New config field: extend `config.py` Pydantic schema + bump default in `share/default.toml`
- New hook target: add module under `src/supamem/hooks/<client>.py`, register in `cli.py hook` dispatcher
- Failure in network code: blanket `except Exception: pass` is correct for non-essential probes (update_check); for indexing/retrieval, surface error to user via `err_console`

# BEGIN SUPAMEM v0.2.0 MANAGED BLOCK — DO NOT EDIT
@~/.supamem/share/rules/dual-memory.md
# END SUPAMEM v0.2.0 MANAGED BLOCK
