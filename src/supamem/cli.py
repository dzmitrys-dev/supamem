"""supamem CLI — Typer app dispatching to subcommands."""
from __future__ import annotations

import re
import time
from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from supamem import __version__
from supamem.console import CREDIT_LINE, console, err_console

# Phase 6 B1 / D-10 — sentinel for TRUE bare-flag UX on `--transcripts`.
# Three distinguishable states for the option:
#   None                          → flag absent; do not index transcripts
#   _TRANSCRIPTS_BARE_SENTINEL    → bare flag; resolve to cfg.transcript_default_root
#   any other str                 → explicit user-provided path
#
# Implementation note: Typer 0.15-0.26 silently drops ``flag_value`` when
# generating the Click option (typer.main.get_click_param ignores it for
# non-bool params), so the Click "optional value" pattern from
# ``@click.option(is_flag=False, flag_value=...)`` is not reachable through
# Typer. We instead pre-process ``sys.argv`` in ``main()`` to substitute the
# sentinel for a bare ``--transcripts`` BEFORE Typer parses, preserving the
# D-10 user-facing UX (``supamem index --transcripts`` is a real bare flag,
# ``--transcripts /path`` keeps explicit-value semantics, and the next arg
# starting with ``-`` is NOT consumed by ``--transcripts``).
# End users never see the sentinel — README/llms.txt examples show only
# ``--transcripts`` (bare) or ``--transcripts <path>``.
_TRANSCRIPTS_BARE_SENTINEL = "__SUPAMEM_DEFAULT__"


def _rewrite_bare_transcripts_argv(argv: list[str]) -> list[str]:
    """Rewrite a bare ``--transcripts`` to ``--transcripts <sentinel>`` (B1).

    A bare flag is detected when ``--transcripts`` appears with NO following
    token, OR with a following token that starts with ``-`` (i.e. another
    flag). In both cases we inject the sentinel so Typer's standard
    ``Optional[str]`` parsing receives a real value and does not fail with
    "Option '--transcripts' requires an argument."

    Pure function over a copy of argv — mutation is contained to ``main()``.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--transcripts":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or nxt.startswith("-"):
                out.append("--transcripts")
                out.append(_TRANSCRIPTS_BARE_SENTINEL)
                i += 1
                continue
        # Also handle ``--transcripts=`` (empty explicit value) → sentinel.
        if tok == "--transcripts=":
            out.append("--transcripts")
            out.append(_TRANSCRIPTS_BARE_SENTINEL)
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _parse_since(value: str | None, *, default_days: int) -> float | None:
    """Parse ``--since`` into a seconds-window or None to disable (B2, D-21).

    - ``None`` → ``default_days * 86400`` (config default).
    - ``"0"`` / ``"0d"`` / ``"0h"`` → None (filter disabled — index all sessions).
    - ``"180d"`` → ``180 * 86400`` seconds.
    - ``"24h"``  → ``24 * 3600`` seconds.
    - Anything else → ``typer.BadParameter`` (T-06-x10 mitigation).
    """
    if value is None:
        return float(default_days) * 86400.0
    v = value.strip().lower()
    if v in ("0", "0d", "0h"):
        return None
    m = re.fullmatch(r"(\d+)([dh])", v)
    if not m:
        raise typer.BadParameter(f"--since must be Nd or Nh (got {value!r})")
    n, unit = int(m.group(1)), m.group(2)
    return float(n) * (86400.0 if unit == "d" else 3600.0)


def _filter_jsonl_by_since(
    paths: list[Path], window_seconds: float | None
) -> list[Path]:
    """Drop JSONL paths whose mtime is older than the recency window (B2)."""
    if window_seconds is None:
        return list(paths)
    cutoff = time.time() - window_seconds
    keep = [p for p in paths if p.stat().st_mtime >= cutoff]
    skipped = len(paths) - len(keep)
    if skipped:
        err_console.print(
            f"Filtered {skipped} sessions older than the --since window "
            f"(use --since=0 to disable)."
        )
    return keep

app = typer.Typer(
    name="supamem",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    help="Project-agnostic dual-memory tooling for Claude Code, Cursor, and opencode.",
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(
            f"[supamem.brand]supamem[/supamem.brand] "
            f"[supamem.accent]v{__version__}[/supamem.accent]"
        )
        console.print(CREDIT_LINE)
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """supamem root command."""
    return None


def _stub(name: str) -> None:
    err_console.print(
        f"[supamem.warn]⚠[/supamem.warn] [supamem.accent]supamem {name}[/supamem.accent] "
        f"[supamem.muted]is not yet implemented (lands in a later 80.6 plan)[/supamem.muted]"
    )
    raise typer.Exit(2)


class Transport(str, Enum):
    stdio = "stdio"
    http = "http"


class Client(str, Enum):
    claude_code = "claude-code"
    cursor = "cursor"
    opencode = "opencode"


class InstallScope(str, Enum):
    project = "project"
    user = "user"


@app.command("index")
def cmd_index(
    target: str = typer.Option("tuned", "--target", help="Retrieval target (e.g. tuned, dense, bm25)."),
    force: bool = typer.Option(False, "--force", help="Re-embed even if manifest is current."),
    snapshot: Optional[str] = typer.Option(None, "--snapshot", help="Path to snapshot artifact (e.g. cursor)."),
    transcripts: Optional[str] = typer.Option(
        None,
        "--transcripts",
        help=(
            "Index Claude Code session JSONL. Bare flag → cfg.transcript_default_root "
            "(default ~/.claude/projects/). Pass an explicit directory to override."
        ),
        # B1 / D-10: bare-flag UX is implemented at the argv layer
        # (_rewrite_bare_transcripts_argv) because Typer 0.15-0.26 silently
        # drops Click's ``is_flag=False, flag_value=...`` pattern. The
        # sentinel ``_TRANSCRIPTS_BARE_SENTINEL`` flows through Typer as a
        # plain string value and is recognized in the function body below.
    ),
    transcripts_only: bool = typer.Option(
        False,
        "--transcripts-only",
        help="Skip the default project corpus; index transcripts only.",
    ),
    since: Optional[str] = typer.Option(
        None,
        "--since",
        help=(
            "Recency window for transcript ingestion (e.g. 180d, 24h, 0 to disable). "
            "Default: cfg.transcript_since_days."
        ),
    ),
) -> None:
    """Embed dev memories into Qdrant using the locked tuned-hybrid pipeline."""
    from supamem.config import load_config
    from supamem.console import info
    from supamem.indexer import run_index

    cfg, _chain = load_config()
    if snapshot == "cursor":
        from supamem.hooks.cursor import run_snapshot

        info(f"snapshot → cursor ({cfg.collection})")
        raise typer.Exit(run_snapshot(config=cfg))

    # ── Phase 6 B1 / D-10 — resolve --transcripts three-state value ──────
    transcript_root: Optional[Path] = None
    jsonl_paths: list[Path] = []
    if transcripts is not None:
        if transcripts == _TRANSCRIPTS_BARE_SENTINEL:
            root_str = cfg.transcript_default_root  # bare flag → config default
        else:
            root_str = transcripts  # explicit path
        transcript_root = Path(root_str).expanduser().resolve()
        if not transcript_root.exists() or not transcript_root.is_dir():
            err_console.print(
                f"[supamem.error]error[/]: --transcripts path does not exist or "
                f"is not a directory: {transcript_root}\n"
                f"  hint: pass an existing directory containing Claude Code "
                f"session JSONL, or omit --transcripts to skip transcript ingestion."
            )
            raise typer.Exit(2)  # actionable, non-zero per INGEST-05
        # D-24 first-run UX warning
        err_console.print(
            "[supamem.warn]⚠[/] First-run transcript indexing may take several minutes "
            "for users with large session histories. Tune with --since=<N>d "
            "or [supamem.transcript] since_days in config.toml."
        )
        # B2 — apply --since mtime filter before reaching run_index.
        window = _parse_since(since, default_days=cfg.transcript_since_days)
        jsonl_paths = sorted(transcript_root.rglob("*.jsonl"))
        jsonl_paths = _filter_jsonl_by_since(jsonl_paths, window)

    sources = list(cfg.sources)
    if transcripts_only:
        sources = []
    if transcript_root is not None:
        sources.extend(str(p) for p in jsonl_paths)

    info(f"indexing → {cfg.collection} (target={target}, force={force})")
    raise typer.Exit(run_index(target=target, force=force, sources=sources, config=cfg))


@app.command("mcp-server")
def cmd_mcp_server(
    transport: Transport = typer.Option(Transport.stdio, "--transport", help="MCP transport: stdio or http."),
    port: int = typer.Option(8765, "--port", help="HTTP port (only used when --transport http)."),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind host (only used when --transport http)."),
) -> None:
    """Run the dual-memory MCP server."""
    import os

    from pathlib import Path

    from supamem.config import ResolvedConfig, find_project_root, load_config
    from supamem.mcp_server import run_http, run_stdio

    root = os.environ.get("SUPAMEM_PROJECT_ROOT", "").strip()
    env_root_set = bool(root)
    cfg_root: Path | None = Path(root) if root else None
    discovered_root: Path | None = None
    if cfg_root is None:
        discovered_root = find_project_root()
        if discovered_root is not None:
            cfg_root = discovered_root
    cfg, chain = load_config(cfg_root)

    # Fail loud on stderr (stdout stays JSON-RPC clean) when MCP fell through
    # to defaults — common when Cursor / Claude Code launch the subprocess from
    # a cwd that is not the workspace and SUPAMEM_PROJECT_ROOT is unset.
    if (
        transport is Transport.stdio
        and not env_root_set
        and discovered_root is None
        and chain.collection == "default"
        and cfg.collection == ResolvedConfig().collection
    ):
        cwd_inspected = Path.cwd()
        err_console.print(
            f"[supamem.warn]⚠ supamem mcp-server[/supamem.warn] "
            f"[supamem.muted]using default collection "
            f"[supamem.accent]{cfg.collection}[/supamem.accent] — "
            f"no project config found.[/supamem.muted]"
        )
        err_console.print(
            f"[supamem.muted]  cwd:[/supamem.muted] {cwd_inspected}  "
            f"[supamem.muted]SUPAMEM_PROJECT_ROOT:[/supamem.muted] unset  "
            f"[supamem.muted]SUPAMEM_CONFIG:[/supamem.muted] "
            f"{'set' if os.environ.get('SUPAMEM_CONFIG') else 'unset'}"
        )
        err_console.print(
            "[supamem.muted]  fix: set "
            "[supamem.accent]SUPAMEM_PROJECT_ROOT=/path/to/workspace[/supamem.accent] "
            "in the MCP host config (e.g. ~/.cursor/mcp.json) and restart the host.[/supamem.muted]"
        )

    if transport is Transport.stdio:
        run_stdio(cfg)
    elif transport is Transport.http:
        run_http(cfg, port=port, host=host)
    else:
        err_console.print(f"[supamem.err]✗[/supamem.err] unknown transport: {transport}")
        raise typer.Exit(2)


@app.command("hook")
def cmd_hook(
    client: str = typer.Argument(..., help="Target client (claude-code, opencode, cursor)."),
    file_path: Optional[str] = typer.Option(None, "--file-path", help="Path being edited (for edit-time hooks)."),
) -> None:
    """Per-client session/edit hooks."""
    from pathlib import Path

    from supamem.config import load_config
    from supamem.hooks import dispatch

    cfg, _chain = load_config()
    fp = Path(file_path) if file_path else None
    raise typer.Exit(dispatch(client=client, file_path=fp, config=cfg))


class StatsWindow(str, Enum):
    today = "today"
    week = "week"
    all_ = "all"


class StatsFormat(str, Enum):
    table = "table"
    json = "json"


@app.command("live")
def cmd_live(
    audit_path: Optional[str] = typer.Option(
        None,
        "--audit-path",
        help="Override audit JSONL path (default: $XDG_CACHE_HOME/supamem/audit.jsonl).",
    ),
) -> None:
    """🧠 Live dashboard tailing the audit JSONL — watch every retrieval call.

    Run this in a side terminal alongside Claude Code / Cursor / OpenCode
    for real-time visibility into the silent PreToolUse-hook injections.
    Pipe-safe: prints plain JSONL when stdout is not a TTY.
    """
    from pathlib import Path

    from supamem.live import run_live

    raise typer.Exit(run_live(Path(audit_path) if audit_path else None))


@app.command("stats")
def cmd_stats(
    show: StatsWindow = typer.Option(StatsWindow.today, "--show", help="Time window."),
    fmt: StatsFormat = typer.Option(StatsFormat.table, "--format", help="Output format."),
) -> None:
    """Render Welford schema-v2 usage counters."""
    from supamem.stats import render

    out = render(show=show.value, format=fmt.value)
    if fmt is StatsFormat.table:
        console.print(f"[supamem.brand]{out.splitlines()[0]}[/supamem.brand]")
        for line in out.splitlines()[1:]:
            console.print(line)
    else:
        console.print_json(out)


@app.command("eval")
def cmd_evalbench(
    suite: str = typer.Option(
        "goldens", "--suite",
        help="Bench suite: goldens | longmemeval_s | coderag.",
    ),
    full: bool = typer.Option(
        False, "--full",
        help=(
            "Run full LongMemEval_S (~500 QA, ~3 GB cache). "
            "Default: 10-question CI subset."
        ),
    ),
    judge: Optional[str] = typer.Option(
        None, "--judge",
        help=(
            "Judge spec: heuristic (default) or ollama:<model>. "
            "SaaS prefixes refused (D-07)."
        ),
    ),
    report: str = typer.Option(
        "json", "--report",
        help="Report format. Currently only 'json' is supported.",
    ),
    out: Optional[Path] = typer.Option(
        None, "--out",
        help="Output path. Default: ~/.supamem/eval/<utc-iso>.json.",
    ),
    baseline: str = typer.Option(
        "v0.1.5", "--baseline",
        help="Baseline version for delta computation.",
    ),
    dataset_path: Optional[Path] = typer.Option(
        None, "--dataset-path",
        help="Local LongMemEval mirror path (skips HF fetch). D-VEND-03.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose",
        help="Include per_question array in the JSON report.",
    ),
    list_suites: bool = typer.Option(
        False, "--list-suites",
        help="List registered suites + default judge tier and exit.",
    ),
    # Backward-compat (v0.1.x):
    regress: bool = typer.Option(
        False, "--regress",
        help="Legacy v0.1.x regression mode (goldens suite, threshold gates).",
    ),
    goldens: Optional[str] = typer.Option(
        None, "--goldens",
        help="Legacy v0.1.x custom goldens JSONL path.",
    ),
    # Phase 15 Plan D Task D2 — coderag peer-row flag.
    peer: Optional[str] = typer.Option(
        None, "--peer",
        help=(
            "Optional peer adapter for the coderag suite. Currently the "
            "only supported value is 'mem0' (requires the peers-mem0 "
            "extras + a running Qdrant on localhost:6333). The peer's "
            "metrics ride alongside the supamem column without replacing it."
        ),
    ),
) -> None:
    """Run the supamem bench harness (Phase 10).

    Default: ``supamem eval`` -> --suite goldens --report json
    -> ~/.supamem/eval/<iso>.json

    Milestone gate: ``supamem eval --suite longmemeval_s --full``.
    """
    # --list-suites short-circuit (D-CLI-02)
    if list_suites:
        console.print("Suites:")
        console.print(
            "  goldens         (default judge: heuristic) — "
            "bundled v0.1.x regression baseline"
        )
        console.print(
            "  longmemeval_s   (default judge: heuristic) — "
            "LongMemEval_S, lazy-fetched from HF"
        )
        console.print(
            "  coderag         (default judge: heuristic) — "
            "agentic-coding three-column-axis suite (Phase 15)"
        )
        raise typer.Exit(0)

    if report not in ("json",):
        err_console.print(
            f"[supamem.error]unknown --report format: {report!r} "
            "(only 'json' is supported)[/]"
        )
        raise typer.Exit(2)

    from supamem.config import load_config
    from supamem.eval.runner import run_bench

    cfg, _chain = load_config()
    raise typer.Exit(run_bench(
        suite=suite,
        full=full,
        judge=judge,
        out=out,
        baseline_version=baseline,
        dataset_path=dataset_path,
        verbose=verbose,
        regress=regress,
        goldens_path=goldens,
        config=cfg,
        peer=peer,
    ))


@app.command("install")
def cmd_install(
    client: Optional[Client] = typer.Option(None, "--client", help="Target client (claude-code, cursor, opencode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned config patches without applying."),
    scope: InstallScope = typer.Option(
        InstallScope.project,
        "--scope",
        help=(
            "Where to write the MCP entry. "
            "'project' (default): per-workspace (.mcp.json or .cursor/mcp.json) — "
            "required for multi-project machines. "
            "'user': global (~/.claude.json or ~/.cursor/mcp.json) — last install wins."
        ),
    ),
    skip_models: bool = typer.Option(
        False,
        "--skip-models / --no-skip-models",
        help=(
            "Skip eager ML model download (air-gapped first-run; backfill "
            "via `supamem repair` once network is available)."
        ),
    ),
    skip_patch_agents: bool = typer.Option(
        False,
        "--skip-patch-agents / --no-skip-patch-agents",
        help=(
            "Skip auto-patching ~/.claude/agents/ tools whitelists for "
            "supamem MCP reachability (D-LOCK-06)."
        ),
    ),
    enforce_search: bool = typer.Option(
        False,
        "--enforce-search",
        help=(
            "OPT-IN (claude-code only): register a PreToolUse gate that DENIES "
            "Edit/Write/MultiEdit when no recent dual_memory_search has been "
            "logged in the session. Forces 'eat your own dog food' compliance. "
            "Override per-session with SUPAMEM_GATE_DISABLE=1."
        ),
    ),
) -> None:
    """Patch a client config to point at supamem."""
    from supamem.install import install as do_install

    raise typer.Exit(
        do_install(
            client=client.value if client else None,
            dry_run=dry_run,
            scope=scope.value,
            enforce_search=enforce_search,
            skip_models=skip_models,
            skip_patch_agents=skip_patch_agents,
        )
    )


@app.command("uninstall")
def cmd_uninstall(
    client: Optional[Client] = typer.Option(None, "--client", help="Target client (claude-code, cursor, opencode)."),
) -> None:
    """Reverse `supamem install` on a client."""
    from supamem.install import uninstall as do_uninstall

    raise typer.Exit(do_uninstall(client=client.value if client else None))


@app.command("unpatch-agents")
def cmd_unpatch_agents() -> None:
    """Restore agent files patched by supamem install/repair (D-UNDO-01).

    Run this BEFORE `pip uninstall supamem` to cleanly remove supamem's
    additions to ~/.claude/agents/ tools whitelists. Files edited since
    they were patched are left alone with a warning.
    """
    from supamem.console import info, ok, warn  # noqa: PLC0415
    from supamem.install.agent_patcher import (  # noqa: PLC0415
        manifest_path,
        unpatch_all,
    )

    mp = manifest_path()
    if not mp.exists():
        info("no agent_patches.json manifest found — nothing to restore")
        raise typer.Exit(code=0)
    summary = unpatch_all()
    if summary.restored:
        ok(f"restored {len(summary.restored)} agent file(s)")
    if summary.skipped_user_edited:
        for path in summary.skipped_user_edited:
            warn(f"skipped (user-edited since patch): {path}")
    if summary.skipped_missing:
        for path in summary.skipped_missing:
            info(f"skipped (file no longer exists): {path}")
    raise typer.Exit(code=0)


@app.command("repair")
def cmd_repair(
    client: Optional[Client] = typer.Option(
        None, "--client", help="Target client (default: re-install all detected installs)."
    ),
    enforce_search: bool = typer.Option(
        False,
        "--enforce-search",
        help="Re-register the claude-code edit-gate (opt-in, mirrors `supamem install`).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without applying."
    ),
    skip_models: bool = typer.Option(
        False,
        "--skip-models / --no-skip-models",
        help="Skip ML model re-fetch step (air-gapped repair).",
    ),
    skip_patch_agents: bool = typer.Option(
        False,
        "--skip-patch-agents / --no-skip-patch-agents",
        help=(
            "Skip auto-patching ~/.claude/agents/ tools whitelists for "
            "supamem MCP reachability (D-LOCK-06)."
        ),
    ),
) -> None:
    """Re-run install in project scope and strip stale legacy global entries.

    The current per-workspace install model (`<repo>/.mcp.json`,
    `<repo>/.cursor/mcp.json`) supersedes the legacy global write to
    `~/.claude.json` / `~/.cursor/mcp.json`. ``supamem repair`` is the
    user-explicit migration verb: it re-installs at project scope from the
    current cwd and removes any stale ``mcpServers.supamem`` entries from
    the global files so they can't shadow per-workspace installs in OTHER
    repos.

    Idempotent: running on a healthy install is a no-op (nothing to write,
    nothing to strip).
    """
    from supamem.install import repair as do_repair

    raise typer.Exit(
        do_repair(
            client=client.value if client else None,
            enforce_search=enforce_search,
            dry_run=dry_run,
            skip_models=skip_models,
            skip_patch_agents=skip_patch_agents,
        )
    )


@app.command("doctor")
def cmd_doctor(
    show_secrets: bool = typer.Option(
        False, "--show-secrets", help="Show qdrant_api_key in plain text (DANGEROUS)."
    ),
) -> None:
    """Probe Qdrant, print resolved config chain, report version drift."""
    from supamem.doctor import run_doctor

    raise typer.Exit(run_doctor(redact_secrets=not show_secrets))


@app.command("init")
def cmd_init(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config / collection."),
    qdrant_url: Optional[str] = typer.Option(None, "--qdrant-url", help="Qdrant URL (defaults to QDRANT_URL env or http://localhost:6333)."),
    skip_models: bool = typer.Option(
        False,
        "--skip-models / --no-skip-models",
        help="Skip ML model pre-fetch (air-gapped first-run; backfill via `supamem repair`).",
    ),
    skip_patch_agents: bool = typer.Option(
        False,
        "--skip-patch-agents / --no-skip-patch-agents",
        help=(
            "Skip auto-patching ~/.claude/agents/ tools whitelists for "
            "supamem MCP reachability (D-LOCK-06)."
        ),
    ),
) -> None:
    """Greenfield bootstrap on a new project."""
    from pathlib import Path

    from supamem.init import run_init

    raise typer.Exit(run_init(
        cwd=Path.cwd(), yes=yes, qdrant_url=qdrant_url, force=force,
        skip_models=skip_models,
        skip_patch_agents=skip_patch_agents,
    ))


class MigratePath(str, Enum):
    coexist = "coexist"
    migrate = "migrate"
    adopt_as_is = "adopt-as-is"


@app.command("migrate")
def cmd_migrate(
    source: str = typer.Option(..., "--source", help="Existing collection name to migrate from."),
    target: Optional[str] = typer.Option(None, "--target", help="Target collection (defaults to supamem-<cwd-slug>)."),
    path: MigratePath = typer.Option(MigratePath.coexist, "--path", help="Migration strategy."),
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive migration paths."),
) -> None:
    """Brownfield migration from a pre-existing dev_memory collection."""
    from pathlib import Path

    from supamem.config import load_config
    from supamem.console import info
    from supamem.init import _slugify
    from supamem.migrate import run_migrate

    cfg, _chain = load_config()
    tgt = target or f"supamem-{_slugify(Path.cwd().name)}"
    info(f"migrate {source!r} → {tgt!r} (path={path.value}, yes={yes})")

    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=cfg.qdrant_url,
        api_key=cfg.qdrant_api_key or None,
        check_compatibility=False,
        timeout=60,
    )
    raise typer.Exit(run_migrate(client, source, tgt, path=path.value, yes=yes))


def main() -> None:
    # Fire-and-forget update probe — writes cache for *next* invocation. Skipped
    # when env vars opt out, when stderr is non-TTY (piped/redirected output),
    # or when the in-process probe fails for any reason. Never blocks.
    import sys

    from supamem.update_check import get_pending_notification, start_background_check

    start_background_check(__version__)
    # B1 / D-10 — preprocess argv so a bare ``--transcripts`` carries the
    # sentinel value (Typer cannot natively express Click's optional-value
    # pattern; see _rewrite_bare_transcripts_argv docstring).
    sys.argv = [sys.argv[0], *_rewrite_bare_transcripts_argv(sys.argv[1:])]
    try:
        app()
    finally:
        # Print any update notice queued from the *previous* invocation. Goes
        # to stderr so JSON consumers piping stdout are unaffected.
        notice = get_pending_notification(__version__)
        if notice:
            try:
                err_console.print(notice, end="")
            except Exception:
                pass


if __name__ == "__main__":
    main()
