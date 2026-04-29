# Migration

## v0.1.0 (initial release)

First public release. No upgrade path required — install via:

```bash
uv tool install git+https://github.com/dzmitrys-dev/supamem@v0.1.0
```

### Plugin entry-point contract (D-48)

`supamem` exposes three plugin groups via `pyproject.toml`
`[project.entry-points]`. Third-party packages can register additional
backends without forking:

```toml
[project.entry-points."supamem.retrieval"]
my_backend = "my_pkg.backend:MyBackend"

[project.entry-points."supamem.embedder"]
my_dense = "my_pkg.embedders:MyDenseEmbedder"

[project.entry-points."supamem.chunker"]
my_chunker = "my_pkg.chunker:my_chunk_fn"
```

**Compatibility caveat (v0):** the plugin contract is _unstable_ until v1.
A `0.x.y` minor bump may break plugins. Pin against `supamem==0.1.*` in
your plugin's `requirements`. The shape of `RetrievedChunk`, the
`Backend.query(text, k)` signature, and entry-point group names are the
load-bearing surface; everything else may move.

The built-in registrations (`tuned_hybrid`, `dense`, `bm25` for retrieval;
`minilm`, `bm25` for embedder; `markdown_header` for chunker) are part of
the wheel and resolve automatically via `importlib.metadata.entry_points`.

## SoftChat -> supamem migration

_(Filled in by Phase 80.6 plan 14: SoftChat rip-out.)_

This section will document the exact steps SoftChat took to replace its in-tree
`scripts/embed-dev-memories.py`, `scripts/dual_memory_mcp_server.py`,
`scripts/dual_memory_bootstrap.py`, `scripts/dm_counter_bump.py`, and
`scripts/regen_cursor_dual_memory_rule.py` with the `supamem` binary.
