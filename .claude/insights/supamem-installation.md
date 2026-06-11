---
topic: supamem-installation
globs: ["src/supamem/init.py", "src/supamem/indexer/**", ".supamem/**", "src/supamem/install/**"]
updated: 2026-06-11
entries: 3
---
- `supamem init` exits 3 when `.supamem/config.toml` already exists and will NOT create a missing Qdrant collection — after a Qdrant data wipe doctor reports "collection missing" but neither `init` (config gate) nor `repair` (MCP/agents/models only) fixes it; recreate the hybrid collection (`create_collection` from `supamem.init`) or accept `init --force` (overwrites config) before `index`.
- `supamem index` assumes the configured collection already exists: boot-time validity/path-prefix/classifier migrations hit Qdrant first and emit 404 storms with zero upserts if the collection is absent — treat "index ran but doctor still says collection missing" as "collection never created", not "index failed silently".
- `uv tool install -e .` on the supamem dev tree pulls mxbai-rerank → CUDA torch (multi-GB, long download); global PyPI `uv tool install supamem` stays on the published wheel without that path — use `uv run supamem` for v0.3.x dev work and only editable-install globally when you explicitly need the dev version on PATH.
