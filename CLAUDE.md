# CLAUDE.md — supamem

Claude Code instructions for the supamem repository.

@AGENTS.md

## Workflow

- Use `/brainstorm` before ambiguous feature work
- Use `/write-plan` before multi-step implementation
- Use `/systematic-debug` before proposing bug fixes
- Verify with `uv run pytest` (project root) and `uv run ruff check src tests` before claiming done

## Hard Constraints

- NEVER use bare `print()` — import from `src/supamem/console.py`
- NEVER print to stdout from `mcp_server.py` — JSON-RPC purity (use `err_console`)
- NEVER hardcode collection names — read `config.collection`
- NEVER skip the PEP 639 license check on release — `license = "MIT"` SPDX, no `License ::` classifier
- NEVER force-move a published git tag — PyPI rejects re-uploads of the same version
- NEVER suppress errors in indexing/retrieval paths — surface via `err_console`
- Update-check is the ONLY code path where blanket `except Exception: pass` is acceptable
- NEVER edit `README.md` without also updating the 4 translations (`README.zh-CN.md`, `README.es.md`, `README.ja.md`, `README.ru.md`) AND bumping the `synced-with` SHA marker on line 2 of each. See AGENTS.md → "README Translations" for the one-liner.

## Definition Of Done

- `uv run pytest` green (full suite)
- `uv run ruff check src tests` clean
- New CLI subcommand: smoke test in `tests/test_cli_smoke.py`
- New backend: registered via entry-point, covered by unit + integration test
- Release: `uv build && uvx twine check dist/*` clean before tagging

## Critical Config

- Config discovery order: `.supamem/config.toml` → `~/.config/supamem/config.toml` → `share/default.toml`
- Cache dir: `platformdirs.user_cache_dir("supamem")` — never `~/.cache/supamem` directly
- CI env: smoke tests pin `NO_COLOR=1`, `TERM=dumb`, `COLUMNS=200`, pop `FORCE_COLOR`

## When Blocked

- Test fails on CI but passes locally: check Rich color escape pollution (see test_cli_smoke env override)
- Import error: confirm `uv sync --extra dev` ran; check entry-points in `pyproject.toml`
- Qdrant connection failure: `docker compose up qdrant` or use mock fixture
- 2+ verification failures: stop and report blocker with command output
