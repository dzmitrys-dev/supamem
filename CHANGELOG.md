# Changelog

All notable changes to `supamem` will be documented in this file.

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
