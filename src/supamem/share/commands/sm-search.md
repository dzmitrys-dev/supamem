---
name: sm-search
description: Ad-hoc semantic search over the supamem dual-memory collection
---

# /sm-search

Search the project's dual-memory corpus for relevant chunks.

## Usage

```
/sm-search <query>
```

Behind the scenes this calls `supamem hook claude-code` with a synthetic file path
to derive the query, then injects the top-k chunks into the conversation.

## Examples

- `/sm-search billing webhook signature verification`
- `/sm-search how do we handle Yookassa retries`
- `/sm-search auth0 session middleware order`

## When to use

- You want semantic context **without** triggering an Edit (no file path needed)
- You want to verify what supamem will inject before making a change
- You're exploring the codebase's documented patterns

For automatic injection on Edit/Write, the PreToolUse hook installed by
`supamem install --client claude-code` already runs — no manual `/sm-search`
needed.
