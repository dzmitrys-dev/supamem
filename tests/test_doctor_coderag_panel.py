"""Plan 15-D Task D2 — supamem doctor coderag panel tests.

Read-only contract (mirrors Plan 08.1 D-DOCTOR-04 + Phase 8 D-FETCH-04):
panel renders information; NEVER raises; NEVER flips the doctor's exit
code. Verified by running ``run_doctor`` directly so the assertions don't
depend on Qdrant being up (the panel is independent of Qdrant
reachability — it's a cache-presence check, not a live probe).
"""
from __future__ import annotations

import os
import subprocess
import sys


def _run_doctor() -> subprocess.CompletedProcess[str]:
    """Run ``supamem doctor`` with deterministic env (NO_COLOR/TERM/COLUMNS)."""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    env.pop("FORCE_COLOR", None)
    env["SUPAMEM_NO_UPDATE_CHECK"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "supamem", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_doctor_renders_coderag_panel() -> None:
    """The doctor output mentions the coderag panel even without a cache."""
    r = _run_doctor()
    out = r.stdout + r.stderr
    assert "coderag" in out.lower(), (
        f"expected 'coderag' substring in doctor output; got: {out!r}"
    )


def test_doctor_coderag_panel_never_flips_exit_code() -> None:
    """The coderag panel must not bump the doctor exit code.

    Doctor exits 0 (all green) or 1 (Qdrant unreachable, etc.). Both are
    acceptable here; what we assert is that adding the coderag panel does
    NOT introduce a new non-{0,1} exit code path.
    """
    r = _run_doctor()
    assert r.returncode in (0, 1), (
        f"doctor returncode={r.returncode} unexpected; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )


def test_doctor_coderag_panel_lists_collection_name() -> None:
    """The panel surfaces the supamem-side collection name."""
    r = _run_doctor()
    out = r.stdout + r.stderr
    # The panel labels the bench collection (15-B-shipped constant).
    assert "supamem_eval_coderag" in out, (
        f"expected 'supamem_eval_coderag' in doctor output; got: {out!r}"
    )
