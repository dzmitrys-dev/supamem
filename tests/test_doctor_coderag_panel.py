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

import pytest


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


# ───── Phase 19.1 SM-3 — optional-extra availability fork ─────────────────
# These cases run the panel IN-PROCESS (not via the _run_doctor subprocess)
# because they monkeypatch importlib.util.find_spec / sys.modules — only an
# in-process call observes the patches.


def _flat_lines(capsys) -> tuple[str, list[str]]:
    """Return (whitespace-collapsed output, raw lines) from the panel render."""
    out = capsys.readouterr().out
    return " ".join(out.split()), out.splitlines()


def test_coderag_panel_pytrec_eval_absent_renders_info(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SM-3 Test 1: pytrec_eval absent on a base install → info line with the
    [eval] install hint; NO warn glyph on the optional-absent line."""
    import importlib.util

    import supamem.doctor as mod

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "pytrec_eval":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    mod._render_coderag_panel()
    flat, lines = _flat_lines(capsys)

    assert "pytrec_eval" in flat, flat
    assert "not installed (pip install supamem[eval])" in flat, flat
    for ln in lines:
        if "not installed" in ln:
            assert "⚠" not in ln, f"optional-absent line must not be a warn: {ln!r}"


def test_coderag_panel_mem0_absent_renders_info(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SM-3 Test 2: mem0 absent → info line with the peers-mem0 install hint;
    NO warn glyph on the optional-absent line."""
    import importlib.util

    import supamem.doctor as mod

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "mem0":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    mod._render_coderag_panel()
    flat, lines = _flat_lines(capsys)

    assert "mem0" in flat, flat
    assert "not installed (pip install supamem[peers-mem0])" in flat, flat
    for ln in lines:
        if "not installed" in ln:
            assert "⚠" not in ln, f"optional-absent line must not be a warn: {ln!r}"


def test_coderag_panel_present_but_broken_keeps_warn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SM-3 Test 3: find_spec present but the coderag import raises → the
    existing warn shape is kept (present-but-broken is an actionable fault)."""
    import importlib.util
    import sys

    import supamem.doctor as mod

    real_find_spec = importlib.util.find_spec

    class _FakeSpec:
        pass

    def fake_find_spec(name, *args, **kwargs):
        if name == "pytrec_eval":
            return _FakeSpec()  # installed
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    # Poison the ingest submodule import (None in sys.modules → ImportError).
    monkeypatch.setitem(sys.modules, "supamem.eval.coderag.ingest", None)
    mod._render_coderag_panel()
    flat, lines = _flat_lines(capsys)

    assert "coderag ingest module probe failed" in flat, flat
    for ln in lines:
        if "coderag ingest module probe failed" in ln:
            assert "⚠" in ln, f"broken-install line must stay a warn: {ln!r}"
