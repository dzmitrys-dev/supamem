# Migration

## v0.1.0 (initial release)

First public release. No upgrade path required — install via:

```bash
uv tool install git+https://github.com/dzmitrys-dev/supamem@v0.1.0
```

## SoftChat -> supamem migration

_(Filled in by Phase 80.6 plan 14: SoftChat rip-out.)_

This section will document the exact steps SoftChat took to replace its in-tree
`scripts/embed-dev-memories.py`, `scripts/dual_memory_mcp_server.py`,
`scripts/dual_memory_bootstrap.py`, `scripts/dm_counter_bump.py`, and
`scripts/regen_cursor_dual_memory_rule.py` with the `supamem` binary.
