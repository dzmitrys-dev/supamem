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


def test_doctor_shows_temporal_validity_panel() -> None:
    """Plan 09-05 D-DOCTOR-01: ``supamem doctor`` surfaces the Temporal-validity panel.

    Renders unconditionally (qdrant up or down) — count probes fall back
    to 0 when Qdrant unreachable. Subprocess env is pinned by ``_run``
    (NO_COLOR=1, TERM=dumb, COLUMNS=200) per AGENTS.md Test Discipline.
    """
    result = _run("doctor")
    out = result.stdout
    # Header.
    assert "Temporal validity" in out, (
        f"expected 'Temporal validity' panel header in output, got: {out!r}"
    )
    # All four buckets must label.
    for label in ("live", "superseded", "awaiting_gc", "future_dated"):
        assert label in out, f"expected bucket {label!r} in output, got: {out!r}"
    # retention_days provenance (mirrors reranker [source: ...] convention).
    assert "retention_days" in out
    # No traceback under any qdrant connectivity scenario.
    assert "Traceback" not in (result.stdout + result.stderr)


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


def test_cold_cli_no_network(tmp_path) -> None:
    """Plan 08-02 success criterion: cold post-install CLI invocations trigger
    ZERO HF network egress (D-FETCH-01 cold-help purity)."""
    env = {
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "200",
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "SUPAMEM_NO_UPDATE_CHECK": "1",
    }
    env.pop("FORCE_COLOR", None)
    for cmd in (["--help"], ["--version"]):
        r = subprocess.run(
            [sys.executable, "-m", "supamem", *cmd],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
        low = (r.stdout + r.stderr).lower()
        assert "snapshot_download" not in low
        assert "downloading" not in low


def test_cmd_init_help_shows_skip_models() -> None:
    """B1 fix: cmd_init Typer command MUST expose --skip-models, otherwise
    the truth-statement 'supamem init calls prepare() unless --skip-models'
    is unverifiable (running `supamem init --skip-models` would error
    'no such option')."""
    r = _run("init", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "--skip-models" in (r.stdout + r.stderr)


def test_cmd_install_help_shows_skip_models() -> None:
    """Mirror for cmd_install (D-FETCH-07)."""
    r = _run("install", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "--skip-models" in (r.stdout + r.stderr)


def test_cmd_repair_help_shows_skip_models() -> None:
    """Mirror for cmd_repair (D-FETCH-07 air-gapped repair symmetry)."""
    r = _run("repair", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "--skip-models" in (r.stdout + r.stderr)


def test_install_help_lists_skip_patch_agents_flag() -> None:
    """Phase 08.1 D-LOCK-06: install --help advertises --skip-patch-agents.

    Asserts on a stable short prefix + the description's unique tag rather than
    the full flag name: Rich/Typer truncates long flag tokens with U+2026 at
    width-dependent boundaries (COLUMNS=200 in _run is honored locally but not
    reliably on GitHub Actions runners under pytest stdout capture).
    """
    r = _run("install", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout + r.stderr
    assert "skip-patch-ag" in out, out
    assert "D-LOCK-06" in out, out


def test_repair_help_lists_skip_patch_agents_flag() -> None:
    """Phase 08.1 D-LOCK-06: repair --help advertises --skip-patch-agents."""
    r = _run("repair", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout + r.stderr
    assert "skip-patch-ag" in out, out
    assert "D-LOCK-06" in out, out


def test_init_help_lists_skip_patch_agents_flag() -> None:
    """Phase 08.1 D-LOCK-06: init --help advertises --skip-patch-agents."""
    r = _run("init", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout + r.stderr
    assert "skip-patch-ag" in out, out
    assert "D-LOCK-06" in out, out


def test_install_with_skip_patch_agents_emits_skip_message(tmp_path) -> None:
    """`supamem install --skip-patch-agents` logs the skip message and never
    invokes the patcher (D-LOCK-06 opt-out)."""
    home = tmp_path / "home"
    home.mkdir()
    # No .claude.json => auto-detect fails (exit 2), but the skip message
    # is gated only on the flag being passed; we don't actually need
    # --client to appear because cmd_install hits autodetect first.
    # Use --client claude-code with --dry-run so the install path runs.
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    env = {"HOME": str(home), "SUPAMEM_NO_UPDATE_CHECK": "1"}
    r = _run(
        "install",
        "--client", "claude-code",
        "--dry-run",
        "--skip-patch-agents",
        "--skip-models",
        env=env,
    )
    combined = r.stdout + r.stderr
    assert "--skip-patch-agents" in combined or "skipping subagent" in combined, (
        f"expected skip message in output, got: stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_repair_with_skip_patch_agents_does_not_traceback(tmp_path) -> None:
    """`supamem repair --skip-patch-agents` exits cleanly (no traceback)
    even when no clients are detected (exit 2 is fine)."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "SUPAMEM_NO_UPDATE_CHECK": "1"}
    r = _run(
        "repair",
        "--skip-patch-agents",
        "--skip-models",
        env=env,
    )
    # Exit 2 ("no installed clients") OR 0 are both acceptable here — we
    # only assert that the flag is accepted (not exit 2 == 2 from Typer
    # parse error) and no Python traceback leaked to stderr.
    assert "Traceback" not in r.stderr, r.stderr
    assert "No such option" not in r.stderr, r.stderr


def test_init_with_skip_patch_agents_accepted(tmp_path) -> None:
    """`supamem init --skip-patch-agents` is accepted by the parser
    (no Typer parse error), regardless of Qdrant probe outcome."""
    cwd = tmp_path / "proj"
    cwd.mkdir()
    env = {"HOME": str(tmp_path), "SUPAMEM_NO_UPDATE_CHECK": "1"}
    r = subprocess.run(
        [sys.executable, "-m", "supamem", "init", "--skip-patch-agents", "--skip-models"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200", **env},
    )
    # Qdrant unreachable → exit 2 expected; flag must NOT cause "No such option".
    assert "No such option" not in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


# ───── Plan 08.1-05 — `supamem unpatch-agents` subcommand (D-UNDO-01 REVISED) ─


def test_unpatch_agents_help_runs() -> None:
    """`supamem unpatch-agents --help` exits 0 and surfaces the docstring."""
    r = _run("unpatch-agents", "--help")
    assert r.returncode == 0, (r.stdout, r.stderr)
    combined = r.stdout + r.stderr
    assert "Restore agent files patched by supamem" in combined, combined


def test_unpatch_agents_no_manifest_exits_zero_with_message(tmp_path) -> None:
    """With no manifest on disk, `unpatch-agents` is a friendly no-op (exit 0)."""
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    env = {
        "HOME": str(home),
        "SUPAMEM_CACHE_DIR": str(cache),
        "SUPAMEM_NO_UPDATE_CHECK": "1",
    }
    r = _run("unpatch-agents", env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    combined = r.stdout + r.stderr
    assert "no agent_patches.json manifest found" in combined, combined


CSV_PATCHABLE_FIXTURE = (
    "---\n"
    "name: csv-patchable\n"
    "description: restrictive whitelist, no supamem coverage\n"
    "tools: Read, Bash, Grep, mcp__context7__*\n"
    "---\n"
    "\n"
    "body\n"
)


def _seed_patchable_agent_for_smoke(home, name: str = "csv-patchable.md"):
    agents_dir = home / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agents_dir / name
    agent_file.write_text(CSV_PATCHABLE_FIXTURE, encoding="utf-8")
    return agent_file


def test_unpatch_agents_restores_after_install_repair_loop(tmp_path) -> None:
    """End-to-end smoke: install patches the seeded agent, then
    `unpatch-agents` restores it byte-identical to the original fixture."""
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    agent_file = _seed_patchable_agent_for_smoke(home)
    original_bytes = agent_file.read_bytes()
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    env = {
        "HOME": str(home),
        "SUPAMEM_CACHE_DIR": str(cache),
        "SUPAMEM_NO_UPDATE_CHECK": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    r1 = _run(
        "install",
        "--client", "claude-code",
        "--dry-run",
        "--skip-models",
        env=env,
    )
    # Install must succeed (or at least not traceback) and patch the agent.
    assert "Traceback" not in r1.stderr, r1.stderr
    patched_text = agent_file.read_text(encoding="utf-8")
    assert "mcp__supamem__*" in patched_text, (
        f"install did not patch the agent file: {patched_text!r}"
    )

    r2 = _run("unpatch-agents", env=env)
    assert r2.returncode == 0, (r2.stdout, r2.stderr)
    assert agent_file.read_bytes() == original_bytes, (
        "agent file should be byte-identical after unpatch-agents"
    )


def test_unpatch_agents_warns_on_user_edited_file(tmp_path) -> None:
    """User-edited frontmatter post-patch → unpatch-agents warns + leaves file alone."""
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    agent_file = _seed_patchable_agent_for_smoke(home)
    (home / ".claude.json").write_text("{}", encoding="utf-8")

    env = {
        "HOME": str(home),
        "SUPAMEM_CACHE_DIR": str(cache),
        "SUPAMEM_NO_UPDATE_CHECK": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    _run(
        "install",
        "--client", "claude-code",
        "--dry-run",
        "--skip-models",
        env=env,
    )
    patched_text = agent_file.read_text(encoding="utf-8")
    assert "mcp__supamem__*" in patched_text, "precondition: file should be patched"

    # Mutate the frontmatter (append a key) so the SHA drifts.
    mutated = patched_text.replace(
        "---\nname: csv-patchable\n",
        "---\nname: csv-patchable\nmodel: opus\n",
        1,
    )
    agent_file.write_text(mutated, encoding="utf-8")
    mutated_bytes = agent_file.read_bytes()

    r = _run("unpatch-agents", env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    combined = r.stdout + r.stderr
    assert "user-edited" in combined or "edited since" in combined, combined
    # File unchanged from the mutated state.
    assert agent_file.read_bytes() == mutated_bytes, (
        "user-edited file must not be mutated by unpatch-agents"
    )


# ───── Phase 10 Plan 10-01 — `supamem eval` surface (D-CLI-01, D-CLI-02) ─


def test_eval_help_smoke() -> None:
    """Plan 10-01: ``supamem eval --help`` exits 0 and surfaces the public
    flag set from D-CLI-01 (``--suite`` and ``--judge`` minimum)."""
    r = _run("eval", "--help")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "--suite" in out, f"expected --suite in eval --help, got: {out!r}"
    assert "--judge" in out, f"expected --judge in eval --help, got: {out!r}"


def test_eval_list_suites() -> None:
    """D-CLI-02: ``supamem eval --list-suites`` exits 0 and prints both
    registered suites for discoverability without nesting subcommands."""
    r = _run("eval", "--list-suites")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "goldens" in out, f"expected 'goldens' suite in --list-suites, got: {out!r}"
    assert "longmemeval_s" in out, (
        f"expected 'longmemeval_s' suite in --list-suites, got: {out!r}"
    )


def test_eval_coderag_smoke(tmp_path) -> None:
    """Plan 15-D Task D2: ``supamem eval --suite coderag`` runs offline.

    Default invocation (no ``--full``) loads the bundled
    ``coderag_smoke.json`` fixture and emits a coderag.v1 envelope —
    no network, no Qdrant, no live corpus walk.
    """
    import json as _json
    out = tmp_path / "coderag_smoke_out.json"
    result = _run(
        "eval", "--suite", "coderag", "--out", str(out),
        env={"SUPAMEM_NO_UPDATE_CHECK": "1"},
    )
    assert result.returncode == 0, (
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )
    assert out.exists(), (
        f"expected envelope at {out}, dir contents: "
        f"{list(tmp_path.iterdir())}"
    )
    envelope = _json.loads(out.read_text(encoding="utf-8"))
    assert envelope["report_schema_version"] == "coderag.v1"
    assert "code_fact" in envelope["scores"]
    assert "decision_rationale" in envelope["scores"]


def test_eval_coderag_help_lists_full_and_out_and_peer() -> None:
    """Plan 15-D D2: --suite coderag exposes --full, --out, --peer."""
    r = _run("eval", "--help")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    for flag in ("--full", "--out", "--peer"):
        assert flag in out, f"expected {flag!r} in eval --help, got: {out!r}"


def test_eval_help_lists_autotune_flags() -> None:
    """Plan 18-I: eval --help surfaces --autotune, --apply, --dry-run."""
    r = _run("eval", "--help")
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    for flag in ("--autotune", "--apply", "--dry-run"):
        assert flag in out, f"expected {flag!r} in eval --help, got: {out!r}"


def test_eval_coderag_autotune_dry_run_smoke() -> None:
    """Plan 18-I: ``eval --suite coderag --autotune --dry-run`` exits 0 offline."""
    r = _run(
        "eval",
        "--suite",
        "coderag",
        "--autotune",
        "--dry-run",
        env={
            "SUPAMEM_NO_UPDATE_CHECK": "1",
            "SUPAMEM_AUTOTUNE_OFFLINE": "1",
        },
    )
    assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"


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


# ───── Phase 19.1 SM-2c — stale-cache truthfulness at the CLI surface ─────


def test_doctor_never_claims_latest_from_stale_cache(tmp_path) -> None:
    """Phase 19.1 SM-2c: CLI doctor never renders a ✓-prefixed on-latest line
    from a stale update-check cache.

    Seeds a 48h-old cache (latest == installed version, so update_available
    is False) under an env-overridden cache dir (XDG_CACHE_HOME on Linux) and
    runs ``supamem doctor`` with the deterministic pinned env from ``_run``
    (NO_COLOR=1, TERM=dumb, COLUMNS=200, FORCE_COLOR popped) per AGENTS.md.
    """
    import json as _json
    import time as _time

    from supamem import __version__

    xdg = tmp_path / "xdg"
    cache_dir = xdg / "supamem"
    cache_dir.mkdir(parents=True)
    (cache_dir / "update_check.json").write_text(
        _json.dumps(
            {
                "last_check_ts": _time.time() - 48 * 3600,
                "latest_version": __version__,
                "etag": None,
                "backoff_until_ts": 0.0,
            }
        ),
        encoding="utf-8",
    )
    r = _run(
        "doctor",
        env={
            "XDG_CACHE_HOME": str(xdg),
            "SUPAMEM_NO_UPDATE_CHECK": "1",
        },
    )
    out = r.stdout
    assert "✓ on latest cached version" not in out, out
    assert "cache stale" in out, out
    assert "cannot confirm latest" in out, out


def test_skip_patch_agents_help_names_both_agent_scopes() -> None:
    """SM-7d: --skip-patch-agents help (install/repair/init) and the
    unpatch-agents docstring name BOTH agent scopes — the patcher scans
    ~/.claude/agents/ AND <project>/.claude/agents/."""
    needle = "~/.claude/agents/ and <project>/.claude/agents/"
    for sub in ("install", "repair", "init", "unpatch-agents"):
        r = _run(sub, "--help")
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert needle in r.stdout, f"{sub} --help missing dual-scope text:\n{r.stdout}"
