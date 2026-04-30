"""supamem CLI — Typer app dispatching to subcommands."""
from __future__ import annotations

from enum import Enum
from typing import Optional

import typer

from supamem import __version__
from supamem.console import CREDIT_LINE, console, err_console

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

    info(f"indexing → {cfg.collection} (target={target}, force={force})")
    raise typer.Exit(run_index(target=target, force=force, sources=cfg.sources, config=cfg))


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
    regress: bool = typer.Option(False, "--regress", help="Run regression suite against bundled goldens."),
    goldens: Optional[str] = typer.Option(None, "--goldens", help="Custom goldens JSONL path."),
) -> None:
    """Run the regression harness against the Phase 80.1 golden corpus."""
    from supamem.config import load_config
    from supamem.eval.runner import run_bench

    cfg, _chain = load_config()
    raise typer.Exit(run_bench(regress=regress, goldens_path=goldens, config=cfg))


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
        )
    )


@app.command("uninstall")
def cmd_uninstall(
    client: Optional[Client] = typer.Option(None, "--client", help="Target client (claude-code, cursor, opencode)."),
) -> None:
    """Reverse `supamem install` on a client."""
    from supamem.install import uninstall as do_uninstall

    raise typer.Exit(do_uninstall(client=client.value if client else None))


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
) -> None:
    """Greenfield bootstrap on a new project."""
    from pathlib import Path

    from supamem.init import run_init

    raise typer.Exit(run_init(cwd=Path.cwd(), yes=yes, qdrant_url=qdrant_url, force=force))


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
    from supamem.update_check import get_pending_notification, start_background_check

    start_background_check(__version__)
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
