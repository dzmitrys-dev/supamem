# Architecture Decision Records (ADRs)

Durable design records for supamem. Each ADR captures a single architectural
decision, its context, consequences, and the alternatives considered.

## Convention

- **Path:** `docs/adr/<NNNN>-<kebab-case-title>.md`
- **Numbering:** Zero-padded 4-digit sequential (`0001`, `0002`, ...).
  Numbers are immutable once assigned. Superseded ADRs are marked
  `status: superseded by NNNN` rather than renumbered.
- **Frontmatter (YAML):**

  ```yaml
  ---
  status: accepted | proposed | deprecated | superseded by NNNN
  date: YYYY-MM-DD
  deciders: [<github-handles>]
  consulted: []
  informed: []
  related: ["FUTURE-NN", "Phase NN", ...]
  ---
  ```

- **Sections:** `# NNNN. <title>` / `## Context` / `## Decision` /
  `## Consequences` / `## Alternatives Considered`.

## Cross-linking

- Each ADR is linked from the relevant phase SUMMARY in `.planning/`
  (planning state is local-only and gitignored — links remain stable
  inside the maintainer workspace).
- Each ADR is linked from the CHANGELOG entry that ships its decision.
- User-visible methodology changes also link from `README.md` and `llms.txt`.

## Index

| #    | Title                                                         | Status   | Date       |
|------|---------------------------------------------------------------|----------|------------|
| [0001](0001-scoped-only-bench-gate.md) | Scoped-only bench gate           | accepted | 2026-05-04 |
