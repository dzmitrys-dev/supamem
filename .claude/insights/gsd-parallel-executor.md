---
topic: gsd-parallel-executor
globs: [".planning/**", ".claude/agents/**"]
updated: 2026-05-03
entries: 2
---
- When spawning gsd-executor agents in this repo, drop the `--no-verify` flag from the parallel-execution prompt — the project's PreToolUse hook (`block-no-verify@1.1.2`) rejects it, and Plan 10-01's executor confirmed pre-commit hooks are fast enough that parallel worktree runs do not contend.
- SUMMARY.md cannot be git-committed in this repo because `.planning/` is gitignored AND `commit_docs: false` in `.planning/config.json` (CLAUDE.md hard constraint: supamem ships as a clean Python package). Tell parallel executors to write SUMMARY.md to the plan directory and rely on worktree filesystem-state preservation through `Agent(isolation="worktree")` auto-merge — do NOT use `git add --force` on `.planning/`, that would push planning artifacts to main.
