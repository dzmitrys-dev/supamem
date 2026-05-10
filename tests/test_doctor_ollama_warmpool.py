"""Phase 17 Plan D — supamem doctor Ollama warm-pool panel tests.

Read-only contract (mirrors Plan 08.1 D-DOCTOR-04 + Phase 8 D-FETCH-04):
panel renders information; NEVER raises; NEVER flips the doctor exit
code. Verbatim sibling of ``test_doctor_coderag_panel.py``.

The panel is conditional: it renders only when the user has opted into
``retrieval = "tuned_hybrid_hyde"`` via project ``.supamem/config.toml``
(or pyproject ``[tool.supamem]``). Default users see no warm-pool panel.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_doctor(
    *,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``supamem doctor`` with deterministic env (NO_COLOR/TERM/COLUMNS).

    Pinned env mirrors ``test_doctor_coderag_panel._run_doctor`` per
    AGENTS.md "Test Discipline" — Rich autodetect alone is insufficient
    on CI runners.
    """
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    env.pop("FORCE_COLOR", None)
    env["SUPAMEM_NO_UPDATE_CHECK"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "supamem", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


def test_doctor_skips_warmpool_panel_when_default_retrieval(tmp_path: Path) -> None:
    """Default retrieval (``tuned_hybrid``) — panel must skip silently.

    No ``.supamem/config.toml`` in ``tmp_path``: ``ResolvedConfig``
    falls back to the shipped default ``retrieval_name = "tuned_hybrid"``
    so the warm-pool panel must NOT render (D-HYDE-04 conditional render).
    """
    r = _run_doctor(cwd=tmp_path)
    out = (r.stdout + r.stderr).lower()
    assert "ollama warm-pool" not in out, (
        f"warm-pool panel rendered without HyDE configured; got: {out!r}"
    )
    assert r.returncode in (0, 1), (
        f"doctor returncode={r.returncode} unexpected; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_doctor_renders_warmpool_panel_when_hyde_configured(tmp_path: Path) -> None:
    """``retrieval = "tuned_hybrid_hyde"`` — panel must render.

    Project config writes the canonical TOML scalar ``retrieval = "..."``
    under ``[supamem]``; the ``_apply_section`` alias routes it to the
    flat ``retrieval_name`` field consumed by ``_render_ollama_warmpool_panel``.
    """
    supamem_dir = tmp_path / ".supamem"
    supamem_dir.mkdir()
    (supamem_dir / "config.toml").write_text(
        "[supamem]\nretrieval = \"tuned_hybrid_hyde\"\n",
        encoding="utf-8",
    )
    r = _run_doctor(cwd=tmp_path)
    out = (r.stdout + r.stderr).lower()
    assert "ollama warm-pool" in out, (
        f"warm-pool panel did not render with HyDE config; got: {out!r}"
    )
    # D-DOCTOR-04: panel never flips exit code regardless of probe result.
    assert r.returncode in (0, 1), (
        f"doctor returncode={r.returncode} unexpected; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_doctor_warmpool_panel_never_flips_exit_code_on_ollama_unreachable(
    tmp_path: Path,
) -> None:
    """Unreachable Ollama (port 1, RFC reserved) — exit code must stay 0/1.

    D-DOCTOR-04 invariant lock: a probe failure may NOT bump the doctor
    exit code into a new non-{0,1} state. The panel must catch the
    connection error and surface it as ``warn(...)``.
    """
    supamem_dir = tmp_path / ".supamem"
    supamem_dir.mkdir()
    (supamem_dir / "config.toml").write_text(
        "[supamem]\nretrieval = \"tuned_hybrid_hyde\"\n",
        encoding="utf-8",
    )
    r = _run_doctor(
        cwd=tmp_path,
        extra_env={"OLLAMA_HOST": "http://127.0.0.1:1"},
    )
    assert r.returncode in (0, 1), (
        f"doctor returncode={r.returncode} unexpected on unreachable Ollama; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    # Probe failure must surface — either as cold-state warn, probe-failed
    # warn, or host-resolution warn. The panel header must still print.
    out = (r.stdout + r.stderr).lower()
    assert "ollama warm-pool" in out, (
        f"warm-pool panel header missing; got: {out!r}"
    )
