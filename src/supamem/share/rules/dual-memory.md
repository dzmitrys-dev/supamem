# Dual Memory — supamem

Use **both** memory layers before any non-trivial code change.

| System | Use for | MCP Tool |
|--------|---------|----------|
| **Semantic** (Qdrant tuned_hybrid) | "Why" questions, decisions, patterns, known issues | `mcp__supamem__qdrant_find` or `mcp__supamem__dual_memory_search` |
| **Structural** (graphify or equivalent) | "What uses X" / impact tracing | project-specific (see `supamem doctor`) |

## When to query

Before any of these, query semantic memory via the supamem MCP tools:

1. New feature or refactor
2. Debugging a non-obvious bug
3. Adding/modifying dependencies
4. Architecture decisions
5. Code review of a non-trivial change

## Query patterns (MCP)

The supamem MCP server exposes two tools:

- `mcp__supamem__qdrant_find` — semantic-only retrieval against the Qdrant `tuned_hybrid` collection.
- `mcp__supamem__dual_memory_search` — semantic + structural fan-out (when a graph backend is available).

These are MCP tools, NOT shell commands. Your AI client (Claude Code, Cursor, OpenCode) calls them on your behalf.

If you see older docs referencing a `qdrant-find`-style shell command, that CLI never existed; the wrapper was always called via MCP.

## Subagent reachability (v0.2.5+)

Subagents that ship with restrictive `tools:` whitelists (e.g. GSD's `gsd-executor`, superpowers/* agents, hookify, etc.) cannot reach the supamem MCP tools unless their whitelist includes `mcp__supamem__*`. Without that token, semantic memory lookups silently return empty.

`supamem install` and `supamem repair` automatically scan `~/.claude/agents/*.md` AND `<project>/.claude/agents/*.md` and idempotently append `mcp__supamem__*` to any restrictive `tools:` whitelist that doesn't already cover supamem. Files with a missing or empty `tools:` line inherit all parent tools (per Claude Code semantics) and are left untouched. Symlinked agent files are skipped with a warning to avoid polluting upstream repos.

Pass `--skip-patch-agents` to `install` / `init` / `repair` to opt out.

A backup manifest at `~/.cache/supamem/agent_patches.json` records every modification so you can roll back cleanly:

```bash
# Reverse all supamem-applied patches (skips files you've edited since)
supamem unpatch-agents
```

### Uninstalling supamem

There is no portable `pip uninstall` hook in pip / uv / pipx (2026), so the manual two-step is the supported contract:

```bash
supamem unpatch-agents      # restore agent files first
pip uninstall supamem       # then remove the package
```

`supamem doctor` displays the manifest path and the `unpatch-agents` reminder so you can find this flow without the docs.

## Anti-patterns

- ❌ Querying after coding — check memory **before** implementing
- ❌ Only using semantic for "what calls X" — use structural for that
- ❌ Only using structural for "why" — structural has no decision rationale
- ❌ Unbounded queries — always cap budget when the tool supports it
- ❌ Editing the `agent_patches.json` manifest by hand — let the CLI manage it

## Refresh

When the project's docs/insights change materially:

```bash
supamem index --target tuned --force
```

Re-running is idempotent — only changed docs re-embed.

## Diagnose & Heal

Use **`supamem doctor`** for full environment diagnosis (config + Qdrant + cache + reranker model + managed-block drift + subagent reachability).
Use **`supamem repair`** for full self-heal (re-fetches missing models, re-syncs `share/`, restores client config, re-applies agent patches). Idempotent.
