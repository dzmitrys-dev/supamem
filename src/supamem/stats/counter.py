"""Welford-style usage counter (schema v2) for supamem.

Two artifacts per cache_dir:

- ``aggregates.json`` — JSON object keyed by ``"<kind>:<source>"`` mapping to
  Welford online aggregates ``{"sum","sumsq","count","min","max"}``.
- ``audit.jsonl`` — one JSON record per call (ts, kind, source, tokens,
  latency_ms). Append-only.

Both writes are guarded by ``fcntl.LOCK_EX`` on a sentinel file under
``cache_dir`` so concurrent agents never corrupt the aggregate. A failure
anywhere in the bump path is logged to stderr and **swallowed** —
observability must never block the calling tool (Plan 80.6-06 fail-soft
contract).

Phase 8 adds a process-local ``_LATENCY_DEQUES`` ring buffer keyed by
``(kind, source)`` so ``supamem doctor`` can render TRUE p50/p95
percentiles for the last 100 rerank calls (W3 verifiability — Welford
aggregates only carry mean+variance; the deque carries the raw samples).
"""
from __future__ import annotations

import collections
import datetime as _dt
import fcntl
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("supamem.stats.counter")
if not log.handlers:
    log.addHandler(logging.StreamHandler(stream=sys.stderr))

# The audit log lives in the platformdirs cache, where every reader (live, session-start, doctor) looks. It used to
# be written to ~/.cache/supamem while the readers read ~/Library/Caches/supamem on macOS, so `supamem live` and the
# status line always saw an empty log (field report 2026-11-06). The legacy dir is migrated once.
_LEGACY_CACHE_DIR = Path.home() / ".cache" / "supamem"
_MIGRATED_FILES = ("audit.jsonl", "aggregates.json")  # the indexer's manifest.json is not ours: it stays


def default_cache_dir() -> Path:
    """The ONE location of the audit log + aggregates; every reader (live, session-start) resolves through here.
    ``SUPAMEM_AUDIT_DIR`` overrides it (the test suite points it at a temp dir so no test writes the user's log)."""
    override = os.environ.get("SUPAMEM_AUDIT_DIR")
    if override:
        return Path(override)
    root = os.environ.get("SUPAMEM_CACHE_DIR")  # the repo-wide cache-root override (rerankers, agent patcher)
    if root:
        return Path(root)
    try:
        import platformdirs

        return Path(platformdirs.user_cache_dir("supamem"))
    except Exception:  # noqa: BLE001 — platformdirs is a dependency; this is only the last resort
        return _LEGACY_CACHE_DIR


DEFAULT_CACHE_DIR = default_cache_dir()


def _migrate_legacy(target: Path) -> None:
    if os.environ.get("SUPAMEM_AUDIT_DIR"):
        return  # an explicit location never adopts the legacy user log
    legacy = _LEGACY_CACHE_DIR
    if legacy.resolve() == target.resolve():
        return
    for name in _MIGRATED_FILES:
        old, new = legacy / name, target / name
        if not old.exists():
            continue
        if not new.exists():
            old.replace(new)
        elif name == "audit.jsonl":  # both exist: keep history, legacy rows first
            new.write_text(old.read_text(encoding="utf-8") + new.read_text(encoding="utf-8"), encoding="utf-8")
            old.unlink()

# Phase 8 D-CPU-02 / W3: process-local ring buffer of recent latency samples
# keyed by ``(kind, source)``. ``maxlen=100`` mirrors the doctor panel's
# "last 100 queries" window. Doctor reads via ``get_latency_samples`` to
# compute TRUE percentiles (Welford aggregates would only give mean+variance).
_LATENCY_DEQUES: dict[tuple[str, str], "collections.deque[float]"] = (
    collections.defaultdict(lambda: collections.deque(maxlen=100))
)


def get_latency_samples(kind: str, source: str) -> list[float]:
    """Return a snapshot list of recent latency samples for ``(kind, source)``.

    Returns an empty list if no ``bump(... latency_ms=...)`` calls have
    landed for this key in the current process. The deque is process-local
    (not persisted) — long-running daemons accumulate; one-shot CLI runs
    start empty.
    """
    key = (kind, source)
    if key not in _LATENCY_DEQUES:
        return []
    return list(_LATENCY_DEQUES[key])


def _resolve_cache_dir(cache_dir: Path | None) -> Path:
    target = Path(cache_dir) if cache_dir else default_cache_dir()
    target.mkdir(parents=True, exist_ok=True)
    if cache_dir is None:
        try:
            _migrate_legacy(target)
        except OSError as exc:
            log.warning("supamem.stats.counter: legacy audit migration failed: %s", exc)
    return target


def _migrate_v1(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Map a legacy flat ``{kind:source -> int}`` doc into the v2 Welford form."""
    out: dict[str, dict[str, float]] = {}
    for k, v in raw.items():
        if isinstance(v, int):
            out[k] = {
                "sum": float(v),
                "sumsq": 0.0,
                "count": int(v),
                "min": 0.0,
                "max": 0.0,
            }
        elif isinstance(v, dict):
            out[k] = {
                "sum": float(v.get("sum", 0.0)),
                "sumsq": float(v.get("sumsq", 0.0)),
                "count": int(v.get("count", 0)),
                "min": float(v.get("min", 0.0)),
                "max": float(v.get("max", 0.0)),
            }
    return out


def _load_aggregates(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _migrate_v1(raw)


def _save_aggregates(path: Path, aggregates: dict[str, dict[str, float]]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(aggregates, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _update(agg: dict[str, float], value: float) -> None:
    is_first = int(agg.get("count", 0)) == 0
    agg["sum"] = float(agg.get("sum", 0.0)) + value
    agg["sumsq"] = float(agg.get("sumsq", 0.0)) + value * value
    agg["count"] = int(agg.get("count", 0)) + 1
    cur_min = agg.get("min")
    if is_first or cur_min is None or value < cur_min:
        agg["min"] = float(value)
    cur_max = agg.get("max")
    if is_first or cur_max is None or value > cur_max:
        agg["max"] = float(value)


def bump(
    kind: str,
    source: str,
    tokens: int,
    latency_ms: float,
    *,
    cache_dir: Path | None = None,
) -> None:
    """Record one tool call. Fail-soft: any error → log + return.

    Updates ``aggregates.json`` (Welford merge keyed by ``f"{kind}:{source}"``)
    and appends one record to ``audit.jsonl``. Both writes are flock-guarded.
    """
    try:
        # Process-local latency ring buffer (D-CPU-02) — record BEFORE the
        # disk write so doctor sees fresh samples even if the flock path
        # below errors out. Always-on for any latency_ms > 0.
        if float(latency_ms) > 0.0:
            _LATENCY_DEQUES[(kind, source)].append(float(latency_ms))

        target = _resolve_cache_dir(cache_dir)
        agg_path = target / "aggregates.json"
        audit_path = target / "audit.jsonl"
        lock_path = target / ".lock"

        record = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "kind": kind,
            "source": source,
            "tokens": int(tokens),
            "latency_ms": float(latency_ms),
        }

        with open(lock_path, "w", encoding="utf-8") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            try:
                aggregates = _load_aggregates(agg_path)
                key = f"{kind}:{source}"
                bucket = aggregates.setdefault(
                    key,
                    {"sum": 0.0, "sumsq": 0.0, "count": 0, "min": 0.0, "max": 0.0},
                )
                _update(bucket, float(tokens))
                _save_aggregates(agg_path, aggregates)
                with open(audit_path, "a", encoding="utf-8") as audit_fp:
                    audit_fp.write(json.dumps(record) + "\n")
            finally:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001 — fail-soft contract
        log.warning("supamem.stats.counter.bump failed: %s", exc)
        return None
