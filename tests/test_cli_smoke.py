"""Smoke tests for the supamem CLI surface (plan 80.6-01 Task 2)."""
from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "supamem", *args],
        capture_output=True,
        text=True,
    )


SUBCOMMANDS = ["index", "mcp-server", "hook", "stats", "eval", "install", "doctor"]


def test_help_lists_all_subcommands() -> None:
    """Test 1: --help exits 0 and stdout contains all subcommand names."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    for cmd in SUBCOMMANDS:
        assert cmd in result.stdout, f"expected {cmd!r} in --help, got: {result.stdout!r}"


def test_index_help_lists_flags() -> None:
    """Test 2: index --help shows --target, --force, --snapshot."""
    result = _run("index", "--help")
    assert result.returncode == 0, result.stderr
    for flag in ("--target", "--force", "--snapshot"):
        assert flag in result.stdout, f"expected {flag} in index --help"


def test_mcp_server_help_lists_transport_choices() -> None:
    """Test 3: mcp-server --help shows --transport with choices stdio/http."""
    result = _run("mcp-server", "--help")
    assert result.returncode == 0, result.stderr
    assert "--transport" in result.stdout
    assert "stdio" in result.stdout
    assert "http" in result.stdout


def test_install_help_lists_client_and_dry_run() -> None:
    """Test 4: install --help shows --client (claude-code/cursor/opencode) and --dry-run."""
    result = _run("install", "--help")
    assert result.returncode == 0, result.stderr
    assert "--client" in result.stdout
    assert "--dry-run" in result.stdout
    for client in ("claude-code", "cursor", "opencode"):
        assert client in result.stdout, f"expected client {client!r} in install --help"


def test_index_stub_exits_2_with_stderr_message() -> None:
    """Test 5: invoking `supamem index` prints stub message on stderr and exits 2."""
    result = _run("index")
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}: {result.stderr!r}"
    assert "not yet implemented" in result.stderr


def test_version_prints_010() -> None:
    """Test 6: --version prints styled banner containing 0.1.0 and credit line."""
    result = _run("--version")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "0.1.0" in out, f"expected version in output, got: {out!r}"
    assert "supamem" in out
    # Credit line is part of the banner — verifies the SoftChat / SoftSkillz attribution.
    assert "SoftChat" in out
    assert "SoftSkillz" in out
