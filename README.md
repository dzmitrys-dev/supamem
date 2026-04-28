# supamem

Project-agnostic dual-memory tooling for Claude Code, Cursor, and opencode.

`supamem` packages a battle-tested semantic + structural memory pipeline (Qdrant sparse+dense
hybrid retrieval, MCP server, per-client hooks, evaluation harness) as a single installable Python
distribution. It originated as in-tree scripts in the SoftChat project (Phases 80.1–80.5) and was
extracted in Phase 80.6 so other repos can adopt the same workflow.

## Install (v0.1.0)

```bash
uv tool install git+https://github.com/dzmitrys-dev/supamem@v0.1.0
```

> PyPI publish is deferred to v0.2 per D-44. v0.1.0 is a git-tag-only release.

## CLI surface

| Command | Purpose |
|---------|---------|
| `supamem init` | Greenfield bootstrap on a new project (probe Qdrant, create collection, write `.supamem/config.toml`). |
| `supamem index` | Embed dev memories into Qdrant using the locked tuned-hybrid pipeline (D-25). |
| `supamem mcp-server` | Run the dual-memory MCP server (`--transport stdio` default; `--transport http` for HTTP). |
| `supamem hook <client>` | Per-client hooks (claude-code, opencode, cursor) — invoked on session start / file edit. |
| `supamem stats` | Render Welford schema-v2 usage counters from `.supamem/state/`. |
| `supamem eval` | Run the regression harness against the bundled Phase 80.1 33-query golden corpus. |
| `supamem install` | Patch a target client config to point at supamem (`--client claude-code|cursor|opencode`). |
| `supamem doctor` | Surface resolved config chain, Qdrant probe, and version drift across installed clients. |
| `supamem migrate` | Brownfield migration from a pre-existing `dev_memory` collection (interactive, requires `--yes` for destructive paths). |
| `supamem uninstall` | Reverse `supamem install` cleanly. |

## Migration

If you're coming from an in-tree `dev_memory` setup, see [MIGRATION.md](MIGRATION.md).

## License

MIT.
