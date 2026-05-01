"""Smoke tests for the supamem CLI surface (plan 80.6-01 Task 2)."""
from __future__ import annotations

import os
import subprocess
import sys


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # Force plain output: capture_output makes stdout non-TTY, but CI may also set
    # FORCE_COLOR or COLUMNS; pin a deterministic env so Rich disables ANSI escapes
    # and uses a wide width — keeps "subcommand" / "--flag" tokens unwrapped in --help.
    base_env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    base_env.pop("FORCE_COLOR", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "supamem", *args],
        capture_output=True,
        text=True,
        env=base_env,
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


def test_index_runs_failsoft_with_no_sources() -> None:
    """Test 5: `supamem index` is now wired to run_index — empty sources → exit 0."""
    result = _run("index")
    assert result.returncode == 0, (
        f"expected fail-soft exit 0, got {result.returncode}: stderr={result.stderr!r}"
    )


def test_doctor_shows_caps() -> None:
    """Plan 05-04 Task 01: ``supamem doctor`` surfaces the three cap values + sources.

    Asserts the new "MCP caps" section renders with all three keys and their
    defaults (25 / 250 / 200) plus a ``[source: ...]`` provenance tag. Subprocess
    env is pinned by ``_run`` (NO_COLOR=1, TERM=dumb, COLUMNS=200) so Rich color
    escapes never pollute the assertion strings.
    """
    result = _run("doctor")
    # doctor exits 1 when Qdrant is unreachable (CI / dev without docker up);
    # the caps section runs unconditionally before that exit, so we don't gate
    # on returncode here — only on the rendered surface.
    out = result.stdout
    assert "MCP caps" in out, f"expected 'MCP caps' section header in output, got: {out!r}"
    for key in ("max_top_k", "max_query_chars", "max_preview_chars"):
        assert key in out, f"expected {key!r} in doctor output, got: {out!r}"
    for default in ("25", "250", "200"):
        assert default in out, f"expected default {default!r} in doctor output, got: {out!r}"
    assert "[source:" in out, f"expected provenance tag '[source: ...]' in output, got: {out!r}"


def test_index_transcripts_help_lists_flag() -> None:
    """Plan 06-04: index --help advertises --transcripts and --since (B1, B2)."""
    r = _run("index", "--help")
    assert r.returncode == 0, r.stderr
    assert "--transcripts" in r.stdout
    assert "--since" in r.stdout
    assert "--transcripts-only" in r.stdout


def test_index_transcripts_nonexistent_path_fails(tmp_path) -> None:
    """INGEST-05: bad --transcripts path exits non-zero with actionable error."""
    bogus = tmp_path / "nonexistent"
    r = _run("index", "--transcripts", str(bogus))
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert "does not exist" in combined


def test_index_transcripts_explicit_path(tmp_path) -> None:
    """Explicit --transcripts <dir> resolves and proceeds (Qdrant fail-soft acceptable)."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        '{"type":"user","uuid":"u1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"user","content":"hi"}}\n'
        '{"type":"assistant","uuid":"a1","sessionId":"s1","isSidechain":false,'
        '"message":{"role":"assistant","content":[{"type":"text","text":"hello"}]}}\n'
    )
    r = _run("index", "--transcripts", str(tmp_path), "--transcripts-only")
    # Fail-soft on Qdrant absence is acceptable; we just must not get a Typer parse error (2).
    assert r.returncode in (0, 1)
    # If Typer rejected the args, "Usage:" would appear in stderr.
    assert "Usage:" not in r.stderr or r.returncode == 0


def test_index_transcripts_bare_flag_routes_to_default_root(tmp_path) -> None:
    """B1 / D-10: bare --transcripts (no value) routes to cfg.transcript_default_root.

    The sentinel must NOT be exposed and the bare flag must NOT require a value.
    We override transcript_default_root via SUPAMEM_CONFIG so the test does not
    depend on a real ~/.claude/projects/ tree.
    """
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(
        f'[supamem.transcript]\ndefault_root = "{sessions}"\n',
        encoding="utf-8",
    )
    r = _run("index", "--transcripts", env={"SUPAMEM_CONFIG": str(cfg_path)})
    # Not 2 (Typer parse error) — bare flag must be accepted as a real flag.
    assert r.returncode in (0, 1), f"unexpected exit {r.returncode}: {r.stderr}"
    # default_root resolved cleanly to the existing tmp_path/sessions dir.
    assert "does not exist" not in r.stderr


def test_index_transcripts_bare_flag_does_not_consume_next_arg(tmp_path) -> None:
    """Guard against ``is_flag=False, flag_value=...`` regressions where bare
    ``--transcripts`` might accidentally swallow the next argument as its value.
    """
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(
        f'[supamem.transcript]\ndefault_root = "{sessions}"\n',
        encoding="utf-8",
    )
    # Bare --transcripts followed by --transcripts-only: must parse as TWO flags,
    # NOT as --transcripts="--transcripts-only" (which would yield "does not exist").
    r = _run(
        "index",
        "--transcripts",
        "--transcripts-only",
        env={"SUPAMEM_CONFIG": str(cfg_path)},
    )
    assert r.returncode in (0, 1), f"unexpected exit {r.returncode}: {r.stderr}"
    assert "does not exist" not in r.stderr


def test_transcript_entry_point_loadable() -> None:
    """Plan 06-04 INGEST-02: transcript chunker registered + loadable via importlib.metadata."""
    from importlib.metadata import entry_points

    eps = entry_points(group="supamem.chunker")
    names = {e.name for e in eps}
    assert "transcript" in names, f"expected 'transcript' in {names!r}"
    ep = next(e for e in eps if e.name == "transcript")
    fn = ep.load()
    assert callable(fn)


def test_version_prints_current() -> None:
    """Test 6: --version prints styled banner with current __version__ + credit line."""
    from supamem import __version__

    result = _run("--version")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert __version__ in out, f"expected version in output, got: {out!r}"
    assert "supamem" in out
    # Credit line is part of the banner — verifies the SoftChat / SoftSkillz attribution.
    assert "SoftChat" in out
    assert "SoftSkillz" in out
