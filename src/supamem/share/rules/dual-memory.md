# Dual Memory — supamem

Use **both** memory layers before any non-trivial code change.

| System | Use for | Tool |
|--------|---------|------|
| **Semantic** (Qdrant tuned_hybrid) | "Why" questions, decisions, patterns, known issues | `qdrant-find "<topic>"` |
| **Structural** (graphify or equivalent) | "What uses X" / impact tracing | project-specific (see `supamem doctor`) |

## When to query

Before any of these, query semantic memory:

1. New feature or refactor
2. Debugging a non-obvious bug
3. Adding/modifying dependencies
4. Architecture decisions
5. Code review of a non-trivial change

## Query patterns

```bash
# Decisions and ADRs
qdrant-find "auth flow decisions"
qdrant-find "billing best practices"

# Known issues
qdrant-find "<feature> known issues"

# Patterns / conventions
qdrant-find "<area> patterns"
```

## Anti-patterns

- ❌ Querying after coding — check memory **before** implementing
- ❌ Only using semantic for "what calls X" — use structural for that
- ❌ Only using structural for "why" — structural has no decision rationale
- ❌ Unbounded queries — always cap budget when the tool supports it

## Refresh

When the project's docs/insights change materially:

```bash
supamem index --target tuned --force
```

Re-running is idempotent — only changed docs re-embed.

## Diagnose & Heal

Use **`supamem doctor`** for full environment diagnosis (config + Qdrant + cache + reranker model + managed-block drift).
Use **`supamem repair`** for full self-heal (re-fetches missing models, re-syncs `share/`, restores client config). Idempotent.
