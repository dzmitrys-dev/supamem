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


@app.command("index")
def cmd_index(
    target: str = typer.Option("tuned", "--target", help="Retrieval target (e.g. tuned, dense, bm25)."),
    force: bool = typer.Option(False, "--force", help="Re-embed even if manifest is current."),
    snapshot: Optional[str] = typer.Option(None, "--snapshot", help="Path to snapshot artifact (e.g. cursor)."),
) -> None:
    """Embed dev memories into Qdrant using the locked tuned-hybrid pipeline."""
    _stub("index")


@app.command("mcp-server")
def cmd_mcp_server(
    transport: Transport = typer.Option(Transport.stdio, "--transport", help="MCP transport: stdio or http."),
    port: int = typer.Option(8765, "--port", help="HTTP port (only used when --transport http)."),
) -> None:
    """Run the dual-memory MCP server."""
    _stub("mcp-server")


@app.command("hook")
def cmd_hook(
    client: str = typer.Argument(..., help="Target client (claude-code, opencode, cursor)."),
    file_path: Optional[str] = typer.Option(None, "--file-path", help="Path being edited (for edit-time hooks)."),
) -> None:
    """Per-client session/edit hooks."""
    _stub("hook")


@app.command("stats")
def cmd_stats() -> None:
    """Render Welford schema-v2 usage counters."""
    _stub("stats")


@app.command("eval")
def cmd_evalbench(
    regress: bool = typer.Option(False, "--regress", help="Run regression suite against bundled goldens."),
    goldens: Optional[str] = typer.Option(None, "--goldens", help="Custom goldens JSONL path."),
) -> None:
    """Run the regression harness against the Phase 80.1 golden corpus."""
    _stub("eval")


@app.command("install")
def cmd_install(
    client: Optional[Client] = typer.Option(None, "--client", help="Target client (claude-code, cursor, opencode)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned config patches without applying."),
) -> None:
    """Patch a client config to point at supamem."""
    _stub("install")


@app.command("uninstall")
def cmd_uninstall(
    client: Optional[Client] = typer.Option(None, "--client", help="Target client (claude-code, cursor, opencode)."),
) -> None:
    """Reverse `supamem install` on a client."""
    _stub("uninstall")


@app.command("doctor")
def cmd_doctor() -> None:
    """Probe Qdrant, print resolved config chain, report version drift."""
    _stub("doctor")


@app.command("init")
def cmd_init() -> None:
    """Greenfield bootstrap on a new project."""
    _stub("init")


@app.command("migrate")
def cmd_migrate(
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive migration paths."),
) -> None:
    """Brownfield migration from a pre-existing dev_memory collection."""
    _stub("migrate")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
