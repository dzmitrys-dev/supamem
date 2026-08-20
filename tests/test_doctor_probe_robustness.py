"""Regression tests for WR-11 / WR-12 — doctor probes must not crash or lie.

WR-12: every probe in ``_render_coderag_panel`` is wrapped in
``try/except Exception  # noqa: BLE001`` except the two ``importlib.util.find_spec``
calls SM-3 added. ``find_spec`` raises ``ValueError`` when the named module is in
``sys.modules`` with ``__spec__ is None`` (a real state for namespace shims, some
editable installs, and test monkeypatching) and ``ImportError`` from a broken
parent ``__init__``. The panel is called unguarded from ``run_doctor``, so such an
exception aborted the whole doctor run — including every section after it. SM-3's
goal was to distinguish "absent" from "present but broken"; crashing on "present
but weird" is the wrong outcome for a health command.

WR-11: ``stale`` is True for three distinct reasons — no cache, age >= TTL, or an
active rate-limit backoff window — but the render only knew how to talk about
age, with an ``or 1`` fallback that fabricated a number. During a 6-hour GitHub
backoff the cache can be minutes old while doctor claims "cache stale (1 day
old)", hiding the real cause; ``refresh_stale_cache`` correctly no-ops during
backoff, so the wrong message persisted for the whole window. Negative
``cache_age_seconds`` from clock skew rendered "-1 days old".
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ───────────────────────── WR-12: find_spec guards ─────────────────────────


@pytest.mark.parametrize("exc", [ValueError("__spec__ is None"), ImportError("broken parent")])
def test_coderag_panel_survives_find_spec_raising(
    exc: Exception,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A raising ``find_spec`` must not abort the panel or the doctor run.

    Pre-fix failure: the exception propagated out of ``_render_coderag_panel``
    and out of ``run_doctor``, losing every later section and any exit code.
    """
    import importlib.util

    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    real = importlib.util.find_spec

    def _raising(name: str, package: str | None = None):
        if name in ("pytrec_eval", "mem0"):
            raise exc
        return real(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", _raising)

    rc = mod.run_doctor()  # must not raise

    out = capsys.readouterr().out
    assert isinstance(rc, int)
    # The probe failure is surfaced, not swallowed silently...
    assert "probe failed" in out, out
    # ...and the sections AFTER the coderag panel still rendered.
    assert "Installed clients" in out, out


def test_spec_present_helper_reports_three_states(monkeypatch: pytest.MonkeyPatch) -> None:
    """True / False / None — None meaning "the probe itself failed"."""
    import importlib.util

    from supamem.doctor import _spec_present

    assert _spec_present("json") is True
    assert _spec_present("definitely_not_a_real_module_xyz") is False

    def _boom(name: str, package: str | None = None):
        raise ValueError("__spec__ is None")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert _spec_present("anything") is None


# ─────────────────── WR-11: honest staleness reporting ─────────────────────


def _write_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict) -> None:
    import json

    import supamem.update_check as uc

    p = tmp_path / "update_check.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(uc, "_cache_path", lambda: p)


def test_rate_limited_backoff_is_reported_as_rate_limited_not_as_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A minutes-old cache inside a backoff window must not be called "1 day old".

    Pre-fix failure: ``cache stale (1 day old)`` — the ``or 1`` fallback
    fabricated the number and hid the real cause (GitHub rate-limited us).
    """
    from supamem.update_check import doctor_report

    now = time.time()
    _write_cache(
        monkeypatch,
        tmp_path,
        {
            "last_check_ts": now - 120,  # two minutes old
            "latest_version": "0.4.0a2",
            "etag": None,
            "backoff_until_ts": now + 6 * 3600,
        },
    )
    report = doctor_report("0.4.0a2")
    assert report["stale"] is True
    assert report["stale_reason"] == "rate-limited", report
    assert report["cache_age_seconds"] < 300


def test_expired_cache_reports_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.update_check import doctor_report

    now = time.time()
    _write_cache(
        monkeypatch,
        tmp_path,
        {
            "last_check_ts": now - 5 * 86400,
            "latest_version": "0.4.0a2",
            "etag": None,
            "backoff_until_ts": 0.0,  # 0.0, not null: _read_cache float()-coerces this
        },
    )
    report = doctor_report("0.4.0a2")
    assert report["stale"] is True
    assert report["stale_reason"] == "expired", report


def test_missing_cache_reports_no_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import supamem.update_check as uc

    monkeypatch.setattr(uc, "_cache_path", lambda: tmp_path / "absent.json")
    report = uc.doctor_report("0.4.0a2")
    assert report["stale"] is True
    assert report["stale_reason"] == "no-cache", report


def test_fresh_cache_has_no_stale_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.update_check import doctor_report

    _write_cache(
        monkeypatch,
        tmp_path,
        {
            "last_check_ts": time.time() - 60,
            "latest_version": "0.4.0a2",
            "etag": None,
            "backoff_until_ts": 0.0,  # 0.0, not null: _read_cache float()-coerces this
        },
    )
    report = doctor_report("0.4.0a2")
    assert report["stale"] is False
    assert report["stale_reason"] is None, report


def test_doctor_renders_rate_limited_cause_not_a_fabricated_age(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.doctor as mod
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    monkeypatch.setattr(mod, "refresh_stale_cache", lambda *_a, **_kw: None, raising=False)
    now = time.time()
    _write_cache(
        monkeypatch,
        tmp_path,
        {
            "last_check_ts": now - 120,
            # Equal to the running version so the render lands in the STALE
            # branch rather than the update-available branch.
            "latest_version": __version__,
            "etag": None,
            "backoff_until_ts": now + 6 * 3600,
        },
    )
    mod.run_doctor()
    flat = " ".join(capsys.readouterr().out.split())
    assert "rate-limited" in flat, flat
    assert "1 day old" not in flat, flat


def test_clock_skew_never_renders_negative_age(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cache timestamp in the FUTURE must not render "-1 days old"."""
    import supamem.doctor as mod
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    monkeypatch.setattr(mod, "refresh_stale_cache", lambda *_a, **_kw: None, raising=False)
    _write_cache(
        monkeypatch,
        tmp_path,
        {
            "last_check_ts": time.time() + 10 * 86400,  # skewed clock
            "latest_version": __version__,
            "etag": None,
            "backoff_until_ts": 0.0,  # 0.0, not null: _read_cache float()-coerces this
        },
    )
    mod.run_doctor()
    flat = " ".join(capsys.readouterr().out.split())
    assert "-1 day" not in flat, flat
    assert "- 1 day" not in flat, flat
