"""Tests for supamem.update_check (v0.1.1+).

Covers cache read/write, version comparison semantics, env-var suppression,
network-failure isolation, rate-limit backoff, and the doctor report shape.
"""
from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from supamem import update_check as uc


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect platformdirs.user_cache_dir → tmp dir for the duration of the test."""
    monkeypatch.setattr(uc, "_cache_dir", lambda: tmp_path / "supamem")
    return tmp_path / "supamem"


@pytest.fixture
def tty_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stderr is a TTY so suppression doesn't kick in unintentionally."""
    monkeypatch.setattr(uc.sys.stderr, "isatty", lambda: True, raising=False)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("SUPAMEM_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"):
        monkeypatch.delenv(v, raising=False)


# ── version comparison ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        ("0.1.0", "0.1.1", True),
        ("0.1.1", "0.1.1", False),
        ("0.2.0", "0.1.1", False),
        ("0.1.0", "0.2.0a1", False),  # don't push stable users to prereleases
        ("0.2.0a1", "0.2.0a2", True),  # prerelease → newer prerelease ok
        ("garbage", "0.1.1", False),  # malformed never raises
    ],
)
def test_is_newer(current: str, latest: str, expected: bool) -> None:
    assert uc._is_newer(current, latest) is expected


# ── suppression ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("var", ["SUPAMEM_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"])
def test_is_suppressed_by_env(monkeypatch: pytest.MonkeyPatch, var: str, tty_stderr: None) -> None:
    monkeypatch.setenv(var, "1")
    assert uc._is_suppressed() is True


def test_is_suppressed_when_stderr_not_tty(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setattr(uc.sys.stderr, "isatty", lambda: False, raising=False)
    assert uc._is_suppressed() is True


def test_not_suppressed_when_tty_and_no_envs(
    clean_env: None, tty_stderr: None
) -> None:
    assert uc._is_suppressed() is False


# ── cache I/O ───────────────────────────────────────────────────────────────


def test_read_cache_missing_file_returns_none(cache_dir: Path) -> None:
    assert uc._read_cache() is None


def test_read_cache_corrupt_json_returns_none(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "update_check.json").write_text("not json {")
    assert uc._read_cache() is None


def test_write_then_read_roundtrip(cache_dir: Path) -> None:
    entry = uc.UpdateCacheEntry(
        last_check_ts=1700000000.0,
        latest_version="0.1.5",
        etag='W/"abc123"',
        backoff_until_ts=0.0,
    )
    uc._write_cache(entry)
    loaded = uc._read_cache()
    assert loaded == entry


# ── pending notification ────────────────────────────────────────────────────


def test_get_pending_notification_when_newer(
    cache_dir: Path, clean_env: None, tty_stderr: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time(),
            latest_version="0.1.5",
            etag=None,
        )
    )
    msg = uc.get_pending_notification("0.1.0")
    assert msg is not None
    assert "0.1.0" in msg and "0.1.5" in msg
    assert "SUPAMEM_NO_UPDATE_CHECK" in msg


def test_get_pending_notification_when_current_is_latest(
    cache_dir: Path, clean_env: None, tty_stderr: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time(),
            latest_version="0.1.0",
            etag=None,
        )
    )
    assert uc.get_pending_notification("0.1.0") is None


def test_get_pending_notification_suppressed_returns_none(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, tty_stderr: None
) -> None:
    monkeypatch.setenv("SUPAMEM_NO_UPDATE_CHECK", "1")
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time(), latest_version="9.9.9", etag=None
        )
    )
    assert uc.get_pending_notification("0.1.0") is None


# ── network probe (mocked urllib) ───────────────────────────────────────────


class _FakeResponse:
    def __init__(self, body: bytes, etag: str | None = None) -> None:
        self._body = body
        self.headers = {"ETag": etag} if etag else {}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_probe_github_success() -> None:
    body = json.dumps({"tag_name": "v0.1.5"}).encode()
    fake = _FakeResponse(body, etag='W/"newetag"')
    with patch.object(uc.urllib.request, "urlopen", return_value=fake):
        latest, etag, rate_limited = uc._probe_github("0.1.0", None)
    assert latest == "0.1.5"
    assert etag == 'W/"newetag"'
    assert rate_limited is False


def test_probe_github_strips_v_prefix() -> None:
    body = json.dumps({"tag_name": "0.1.5"}).encode()  # without 'v'
    fake = _FakeResponse(body)
    with patch.object(uc.urllib.request, "urlopen", return_value=fake):
        latest, _, _ = uc._probe_github("0.1.0", None)
    assert latest == "0.1.5"


def test_probe_github_304_returns_etag_unchanged() -> None:
    err = urllib.error.HTTPError(
        url="https://api.github.com/...", code=304, msg="Not Modified",
        hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch.object(uc.urllib.request, "urlopen", side_effect=err):
        latest, etag, rate_limited = uc._probe_github("0.1.0", 'W/"old"')
    assert latest is None  # caller preserves cached value
    assert etag == 'W/"old"'
    assert rate_limited is False


def test_probe_github_rate_limited_403() -> None:
    err = urllib.error.HTTPError(
        url="https://api.github.com/...", code=403, msg="Forbidden",
        hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch.object(uc.urllib.request, "urlopen", side_effect=err):
        _, _, rate_limited = uc._probe_github("0.1.0", None)
    assert rate_limited is True


def test_probe_github_network_failure_swallowed() -> None:
    with patch.object(uc.urllib.request, "urlopen", side_effect=OSError("dns fail")):
        latest, etag, rate_limited = uc._probe_github("0.1.0", None)
    assert latest is None and rate_limited is False


# ── _run_probe (TTL + backoff) ──────────────────────────────────────────────


def test_run_probe_skips_when_cache_fresh(
    cache_dir: Path, clean_env: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time(),  # just now
            latest_version="0.1.0",
            etag=None,
        )
    )
    with patch.object(uc, "_probe_github") as probe:
        uc._run_probe("0.1.0")
    probe.assert_not_called()


def test_run_probe_skips_when_in_backoff(
    cache_dir: Path, clean_env: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time() - 999_999,  # ancient
            latest_version="0.1.0",
            etag=None,
            backoff_until_ts=time.time() + 999_999,  # in the future
        )
    )
    with patch.object(uc, "_probe_github") as probe:
        uc._run_probe("0.1.0")
    probe.assert_not_called()


def test_run_probe_sets_backoff_on_rate_limit(
    cache_dir: Path, clean_env: None
) -> None:
    with patch.object(uc, "_probe_github", return_value=(None, None, True)):
        uc._run_probe("0.1.0")
    cache = uc._read_cache()
    assert cache is not None
    assert cache.backoff_until_ts > time.time()


def test_run_probe_preserves_cached_latest_on_304(
    cache_dir: Path, clean_env: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=time.time() - 999_999,
            latest_version="0.1.5",
            etag='W/"old"',
        )
    )
    # 304 returns latest=None — _run_probe must keep the existing value
    with patch.object(uc, "_probe_github", return_value=(None, 'W/"old"', False)):
        uc._run_probe("0.1.0")
    cache = uc._read_cache()
    assert cache is not None
    assert cache.latest_version == "0.1.5"


def test_run_probe_never_raises(cache_dir: Path, clean_env: None) -> None:
    with patch.object(uc, "_probe_github", side_effect=RuntimeError("explode")):
        uc._run_probe("0.1.0")  # must not raise


# ── start_background_check ──────────────────────────────────────────────────


def test_start_background_check_returns_none_when_suppressed(
    monkeypatch: pytest.MonkeyPatch, tty_stderr: None
) -> None:
    monkeypatch.setenv("CI", "1")
    assert uc.start_background_check("0.1.0") is None


def test_start_background_check_returns_thread_and_is_daemon(
    cache_dir: Path, clean_env: None, tty_stderr: None
) -> None:
    with patch.object(uc, "_run_probe") as run:
        t = uc.start_background_check("0.1.0")
        assert t is not None
        assert t.daemon is True
        t.join(timeout=1.0)
    run.assert_called_once_with("0.1.0")


# ── doctor report ───────────────────────────────────────────────────────────


def test_doctor_report_no_cache(cache_dir: Path, clean_env: None) -> None:
    rpt = uc.doctor_report("0.1.0")
    assert rpt["current_version"] == "0.1.0"
    assert rpt["cached_latest_version"] is None
    assert rpt["last_check_ts"] is None
    assert rpt["update_available"] is False
    assert "supamem" in rpt["cache_path"]


def test_doctor_report_with_update_available(
    cache_dir: Path, clean_env: None
) -> None:
    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=1700000000.0, latest_version="0.2.0", etag=None
        )
    )
    rpt = uc.doctor_report("0.1.0")
    assert rpt["cached_latest_version"] == "0.2.0"
    assert rpt["update_available"] is True


def test_doctor_report_lists_suppression_envs(
    monkeypatch: pytest.MonkeyPatch, cache_dir: Path
) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("SUPAMEM_NO_UPDATE_CHECK", "1")
    rpt = uc.doctor_report("0.1.0")
    assert "CI" in rpt["suppressed_by_env"]
    assert "SUPAMEM_NO_UPDATE_CHECK" in rpt["suppressed_by_env"]
