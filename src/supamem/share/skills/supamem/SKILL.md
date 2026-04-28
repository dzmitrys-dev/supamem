---
name: supamem
description: Project-agnostic dual-memory tooling — semantic + structural memory for AI coding agents
---

# supamem skill

`supamem` packages a battle-tested semantic memory layer (Qdrant hybrid sparse+dense
retrieval), an MCP server, per-client hooks, and a usage counter as a single CLI.

## When to use which subcommand

| Subcommand | Use when |
|------------|----------|
| `supamem init` | First time on a fresh project — probe Qdrant, create collection, write `.supamem/config.toml`. |
| `supamem index` | After updating project docs, ADRs, insights, or rules. Re-embeds changed docs only. |
| `supamem index --snapshot cursor` | Refresh `.cursor/rules/dual-memory-snapshot.mdc` (Cursor's passive channel). |
| `supamem mcp-server` | Run the MCP server (clients usually call this themselves; manual invocation only for debugging). |
| `supamem hook claude-code --file-path X` | Per-edit semantic-context injection for Claude Code (clients call this). |
| `supamem doctor` | Probe Qdrant health, resolve config chain, report version drift. Run when something feels off. |
| `supamem stats --show today` | See today's tool-call rate, p95 latency, and which client is calling. |
| `supamem migrate` | Brownfield: convert a legacy `dev_memory` collection into the supamem schema. |
| `supamem install --client X` | Wire supamem into Claude Code / Cursor / OpenCode. Idempotent. |
| `supamem uninstall --client X` | Cleanly reverse `supamem install`. |

## Typical first-time setup

```bash
uv tool install supamem
docker run -d -p 6333:6333 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest
cd <your-project>
supamem init --yes
supamem install --client claude-code
supamem doctor
```

## Notes

- Locked schema (Phase 80.1): hybrid BM25 + MiniLM with RRF fusion, T-1 markdown chunker.
- Fail-soft contract: hooks and counter never block the calling tool — observability before correctness.
- MCP tool exposed by `mcp-server` is named `dual_memory_search`.
