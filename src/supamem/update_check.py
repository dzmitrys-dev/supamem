"""Background update check against GitHub Releases (v0.1.1+).

Pattern: pip-style fire-and-forget daemon thread. Each invocation spawns a
short probe that writes the latest known version to a JSON cache file. The
*next* invocation reads the cache and prints a stderr footer if the cached
version is newer than the running version. This keeps the hot path free of
network IO — the user never waits.

Failure isolation is total: every code path is wrapped in ``except Exception:
pass``. If the probe fails (offline, rate-limited, malformed JSON, anything),
the CLI continues unaffected.

Suppressed when any of these env vars are set:
    SUPAMEM_NO_UPDATE_CHECK=1   (project-specific)
    NO_UPDATE_NOTIFIER=1        (npm convention)
    CI=1                        (continuous integration)

Also skipped when stderr is not a TTY (script/pipe consumers).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import platformdirs
from packaging.version import InvalidVersion, Version

GITHUB_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/dzmitrys-dev/supamem/releases/latest"
)
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h
RATE_LIMITED_BACKOFF_SECONDS = 6 * 60 * 60  # 6h on 403/429
NETWORK_TIMEOUT_SECONDS = 2.0


def _cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("supamem"))


def _cache_path() -> Path:
    return _cache_dir() / "update_check.json"


@dataclass(frozen=True)
class UpdateCacheEntry:
    last_check_ts: float
    latest_version: str | None
    etag: str | None
    backoff_until_ts: float = 0.0


def _read_cache() -> UpdateCacheEntry | None:
    try:
        raw = _cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return UpdateCacheEntry(
            last_check_ts=float(data.get("last_check_ts", 0.0)),
            latest_version=data.get("latest_version"),
            etag=data.get("etag"),
            backoff_until_ts=float(data.get("backoff_until_ts", 0.0)),
        )
    except Exception:
        return None


def _write_cache(entry: UpdateCacheEntry) -> None:
    try:
        cache_dir = _cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write via .tmp + rename to avoid partial-write reads from
        # concurrent invocations.
        tmp = _cache_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(entry)), encoding="utf-8")
        tmp.replace(_cache_path())
    except Exception:
        pass


def _is_suppressed() -> bool:
    """Skip when CI, opt-out env, or non-TTY stderr (output piped/redirected)."""
    for var in ("SUPAMEM_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"):
        if os.environ.get(var, "").strip():
            return True
    try:
        if not sys.stderr.isatty():
            return True
    except Exception:
        return True
    return False


def _probe_github(current_version: str, etag: str | None) -> tuple[str | None, str | None, bool]:
    """Probe GitHub Releases. Returns (latest_version, etag, rate_limited).

    Sends If-None-Match when an ETag is cached. On 304, returns the cached
    version unchanged. On 403/429, sets ``rate_limited=True`` so the caller
    can apply a longer backoff.
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"supamem/{current_version}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if etag:
        headers["If-None-Match"] = etag

    req = urllib.request.Request(GITHUB_RELEASES_LATEST_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_SECONDS) as resp:
            body = resp.read()
            new_etag = resp.headers.get("ETag")
            data: Any = json.loads(body)
            tag = str(data.get("tag_name") or "").lstrip("v").strip()
            return (tag or None, new_etag, False)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            # Not modified — cached version still current
            return (None, etag, False)
        if e.code in (403, 429):
            return (None, etag, True)
        return (None, etag, False)
    except Exception:
        return (None, etag, False)


def _is_newer(current_raw: str, latest_raw: str) -> bool:
    try:
        current = Version(current_raw)
        latest = Version(latest_raw)
    except InvalidVersion:
        return False
    if latest <= current:
        return False
    # Suppress prerelease nags unless the user is also on a prerelease — most
    # users don't want to be pushed to alphas.
    if latest.is_prerelease and not current.is_prerelease:
        return False
    return True


def _run_probe(current_version: str) -> None:
    """Daemon thread body. Never raises."""
    try:
        cache = _read_cache()
        now = time.time()
        # Honor backoff window before talking to GitHub
        if cache and cache.backoff_until_ts and now < cache.backoff_until_ts:
            return
        # Honor TTL — skip probe if cache is fresh
        if cache and (now - cache.last_check_ts) < DEFAULT_TTL_SECONDS:
            return
        latest, etag, rate_limited = _probe_github(current_version, cache.etag if cache else None)
        backoff_until = (
            now + RATE_LIMITED_BACKOFF_SECONDS if rate_limited else 0.0
        )
        # Preserve previously-cached latest_version if 304 / network failure
        # returned None — we don't want to clobber a valid result.
        resolved_latest = latest or (cache.latest_version if cache else None)
        _write_cache(
            UpdateCacheEntry(
                last_check_ts=now,
                latest_version=resolved_latest,
                etag=etag,
                backoff_until_ts=backoff_until,
            )
        )
    except Exception:
        pass


def start_background_check(current_version: str) -> threading.Thread | None:
    """Start daemon thread to probe GitHub. Returns the thread or None if suppressed.

    The thread is daemonic — interpreter exit terminates it cleanly even
    mid-probe. We never join.
    """
    if _is_suppressed():
        return None
    try:
        t = threading.Thread(
            target=_run_probe,
            args=(current_version,),
            name="supamem-update-check",
            daemon=True,
        )
        t.start()
        return t
    except Exception:
        return None


def get_pending_notification(current_version: str) -> str | None:
    """Return a stderr-formatted update notice if a newer version is cached, else None.

    Reads from cache only — never blocks on network. Safe to call on every
    invocation. Returns None when no cache, no newer version, or suppressed.
    """
    if _is_suppressed():
        return None
    try:
        cache = _read_cache()
        if not cache or not cache.latest_version:
            return None
        if not _is_newer(current_version, cache.latest_version):
            return None
        return (
            f"\n  ┃ A new release of supamem is available: "
            f"{current_version} → {cache.latest_version}\n"
            f"  ┃ Upgrade:  uv tool upgrade supamem  (or  pip install -U supamem)\n"
            f"  ┃ Suppress: SUPAMEM_NO_UPDATE_CHECK=1\n"
        )
    except Exception:
        return None


def doctor_report(current_version: str) -> dict[str, Any]:
    """Snapshot of update-check state for ``supamem doctor``."""
    cache = _read_cache()
    suppressed_by = [
        v for v in ("SUPAMEM_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI")
        if os.environ.get(v, "").strip()
    ]
    update_available = False
    if cache and cache.latest_version:
        update_available = _is_newer(current_version, cache.latest_version)
    return {
        "current_version": current_version,
        "cached_latest_version": cache.latest_version if cache else None,
        "last_check_ts": cache.last_check_ts if cache else None,
        "update_available": update_available,
        "cache_path": str(_cache_path()),
        "suppressed_by_env": suppressed_by,
        "stderr_is_tty": _safe_isatty(),
    }


def _safe_isatty() -> bool:
    try:
        return sys.stderr.isatty()
    except Exception:
        return False
