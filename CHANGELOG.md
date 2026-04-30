# Changelog

All notable changes to `supamem` will be documented in this file.

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
