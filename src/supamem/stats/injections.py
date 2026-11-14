"""Injection journal + hook-stats for the Claude Code edit hook (v0.4.0a4).

The 2026-11-14 effectiveness study (cl-harness research) could only measure the edit
hook by mining Claude Code transcripts because supamem kept no per-invocation record of
what it injected. This module is that record:

- ``<audit dir>/injections.jsonl`` — one JSON line per hook invocation (the hook writes
  it, fail-soft; ``supamem stats.counter.default_cache_dir`` resolves the dir, so
  ``SUPAMEM_AUDIT_DIR`` overrides it exactly like ``audit.jsonl``).
- ``<audit dir>/dedup-<session>.json`` — per-session markers of already-injected chunk
  keys (sha256 of source|text), 24 h TTL, so a session never sees the same chunk twice
  (baseline: 65.6% of injections were exact repeats).
- ``compute_stats`` — the usefulness metric: correlate the journal with the Claude Code
  transcripts (``<projects root>/<project>/<session>.jsonl``) and count how often an
  injected chunk is actually used later in the same session (Read/Edit/Write/Grep on its
  source, or its basename in assistant text).

Everything here is best-effort: a journaling or marker failure must never break the hook
that calls it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("supamem.stats.injections")

JOURNAL_NAME = "injections.jsonl"
_DEDUP_TTL_SECONDS = 24 * 3600
_DEDUP_MAX_IDS = 2000
_TRANSCRIPT_BYTE_CAP = 256 * 1024
_TASK_TEXT_MAX_CHARS = 400
_USED_TOOLS = frozenset({"Read", "Edit", "Write", "Grep"})

ZERO_DROPPED: dict[str, int] = {"stale": 0, "floor": 0, "foreign": 0}


def journal_path(cache_dir: Path | None = None) -> Path:
    """The ONE location of the injection journal; resolves through the audit dir."""
    from supamem.stats.counter import default_cache_dir

    return (Path(cache_dir) if cache_dir else default_cache_dir()) / JOURNAL_NAME


def append_row(row: dict[str, Any], cache_dir: Path | None = None) -> None:
    """Append one journal line. Fail-soft: ANY journaling error never breaks the hook.

    Broad ``Exception`` (not just ``OSError``): a lone surrogate in a chunk payload turns
    the ``write`` into ``UnicodeEncodeError`` — a ``ValueError`` — and one escaped here
    used to re-trigger the hook's outer handler for a second stdout JSON (review round
    2026-11-14). ``errors="replace"`` keeps the file line-parseable instead.
    """
    try:
        p = journal_path(cache_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 — journaling is strictly best-effort
        log.debug("injections journal append failed: %s", exc)


def empty_row(*, session: str = "", target: str = "", query: str = "") -> dict[str, Any]:
    """A minimal journal row for invocations that never reach the search path."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "target": target,
        "query": query,
        "chunks": [],
        "empty": True,
        "deduped": 0,
        "dropped": dict(ZERO_DROPPED),
        "ms": 0.0,
        "shadow": None,
    }


def chunk_lang(text: str) -> str:
    """ "ru" when more than 30% of the alphabetic characters are Cyrillic, else "en"."""
    letters = 0
    cyrillic = 0
    for ch in text:
        if ch.isalpha():
            letters += 1
            if "CYRILLIC" in unicodedata.name(ch, ""):
                cyrillic += 1
    return "ru" if letters and cyrillic / letters > 0.30 else "en"


def source_exists(source: str, root: Path) -> bool:
    """Absolute sources are checked as-is; relative ones resolve against the project root."""
    p = Path(source).expanduser()
    if not p.is_absolute():
        p = root / p
    try:
        return p.exists()
    except OSError:
        return False


def chunk_key(source: str | None, text: str) -> str:
    """Stable content key for the session dedup marker (source + text hash)."""
    raw = f"{source or ''}|{text or ''}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def _safe_session(session: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:120]
    return s or "nosession"


def dedup_marker_path(session: str, cache_dir: Path | None = None) -> Path:
    return journal_path(cache_dir).parent / f"dedup-{_safe_session(session)}.json"


def load_seen(session: str, cache_dir: Path | None = None) -> set[str]:
    """Chunk keys already injected in this session; empty when the marker expired (24 h TTL)."""
    if not session:
        return set()
    p = dedup_marker_path(session, cache_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return set()
        if time.time() - float(data.get("ts", 0.0)) > _DEDUP_TTL_SECONDS:
            return set()
        ids = data.get("ids")
        if not isinstance(ids, list):
            return set()
        return {str(x) for x in ids if isinstance(x, str)}
    except (OSError, ValueError):
        return set()


def save_seen(session: str, ids: set[str], cache_dir: Path | None = None) -> None:
    """Merge ``ids`` into the session marker (atomic replace; bounded; fail-soft).

    Parallel hooks of ONE session race here: Claude Code can run several edits
    concurrently. The old fixed ``.tmp`` name made two writers collide (torn writes or a
    ``FileNotFoundError`` on ``os.replace`` → the whole update lost). Each writer now uses
    a unique ``mkstemp`` file, re-reads+re-merges immediately before the replace, and
    self-checks that its own ids landed (a lost race re-merges, bounded retries).
    """
    if not session or not ids:
        return
    p = dedup_marker_path(session, cache_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(3):
            merged = load_seen(session, cache_dir) | set(ids)
            data = {"ts": time.time(), "ids": sorted(merged)[-_DEDUP_MAX_IDS:]}
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(data))
                # Re-read+merge immediately before the replace: a parallel hook of the same
                # session may have persisted ids between our first read and now.
                latest = load_seen(session, cache_dir)
                if latest - merged:
                    data = {"ts": time.time(), "ids": sorted(latest | set(ids))[-_DEDUP_MAX_IDS:]}
                    with open(tmp, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(data))
                os.replace(tmp, p)
                tmp = None
            finally:
                if tmp is not None:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            # Self-check: our ids must be in the file we just published (bounded merge retry
            # on a lost race). Skipped when the bound truncated the merged set.
            if len(merged) <= _DEDUP_MAX_IDS and not set(ids) - load_seen(session, cache_dir):
                break
        else:
            log.debug("dedup marker save: merge retries exhausted for session %r", session)
        _sweep_expired_markers(cache_dir)
    except OSError as exc:
        log.debug("dedup marker save failed: %s", exc)


def _sweep_expired_markers(cache_dir: Path | None = None, *, max_files: int = 50) -> int:
    """Delete ``dedup-*.json`` markers older than the 24 h TTL (bounded, fail-soft).

    Called opportunistically on each ``save_seen`` — markers were previously never
    deleted and accumulated forever. mtime stands in for the JSON ``ts`` (every save
    rewrites it). Stale ``.tmp`` leftovers from the old fixed-tmp writer are swept too.
    """
    removed = 0
    try:
        d = dedup_marker_path("sweep", cache_dir).parent
        now = time.time()
        for pattern in ("dedup-*.json", "*.tmp"):
            for f in d.glob(pattern):
                if removed >= max_files:
                    return removed
                try:
                    if now - f.stat().st_mtime > _DEDUP_TTL_SECONDS:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    return removed


# ---- shadow task query: the last real user message from the transcript ----------------------


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        return " ".join(parts)
    return ""


def last_user_task_text(
    transcript: Path,
    *,
    byte_cap: int = _TRANSCRIPT_BYTE_CAP,
    max_chars: int = _TASK_TEXT_MAX_CHARS,
) -> str:
    """The last REAL user message (not a tool_result), bounded to the last ``byte_cap`` bytes.

    Mirrors the gate hook's reverse-scan bound (``hooks.gate_edit``): a multi-hour
    transcript must not slow the edit path down.
    """
    try:
        size = transcript.stat().st_size
    except OSError:
        return ""
    start = max(0, size - byte_cap)
    try:
        with transcript.open("rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return ""
    if start > 0:
        nl = blob.find(b"\n")
        if nl == -1:
            return ""
        blob = blob[nl + 1 :]
    found = ""
    for line in blob.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isMeta"):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        text = _user_text(msg.get("content")).strip()
        if text:
            found = text[:max_chars]
    return found


# ---- hook-stats: correlate the journal with the session transcripts -------------------------


def default_transcript_roots() -> list[Path]:
    """Where Claude Code keeps session transcripts (config default, expanded)."""
    from supamem.config import ResolvedConfig

    return [Path(ResolvedConfig().transcript_default_root).expanduser()]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # The hook stamps rows with timezone-aware UTC (datetime.now(timezone.utc)), but
        # older/hand-written rows can be naive — comparing naive with the aware cutoff
        # raised TypeError and crashed `supamem hook-stats`. Journal stamps are UTC by
        # construction, so naive means UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_int(value: Any) -> int:
    """Counter fields from the journal tolerate junk (``"abc"``, ``None``) → 0, never raise."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def find_transcript(session: str, roots: list[Path]) -> Path | None:
    """Locate ``<root>/<project>/<session>.jsonl`` under any of the roots."""
    if not session:
        return None
    for root in roots:
        try:
            for p in sorted(root.rglob(f"{session}.jsonl")):
                return p
        except OSError:
            continue
    return None


def _iter_events(transcript: Path) -> list[dict[str, Any]]:
    """Assistant-side use events: tool_use Read/Edit/Write/Grep targets + text mentions.

    Streams the transcript line by line — multi-hour session JSONL files must not be
    read into memory whole (hook-stats runs on the user's machine, not in a bench).
    """
    events: list[dict[str, Any]] = []
    try:
        fh = transcript.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        try:
            for line in fh:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                items = content if isinstance(content, list) else []
                ts = _parse_ts(entry.get("timestamp"))
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_use":
                        tool = str(item.get("name") or "")
                        target = ""
                        tool_input = item.get("input")
                        if isinstance(tool_input, dict):
                            for key in ("file_path", "notebook_path", "path"):
                                value = tool_input.get(key)
                                if isinstance(value, str) and value:
                                    target = value
                                    break
                        if tool in _USED_TOOLS and target:
                            events.append({"ts": ts, "kind": "tool", "path": target, "text": ""})
                    elif item.get("type") == "text" and isinstance(item.get("text"), str):
                        events.append({"ts": ts, "kind": "text", "path": "", "text": item["text"]})
        except OSError:
            return events  # a mid-read failure keeps what was parsed so far
    return events


def _same_target(a: str, b: str) -> bool:
    try:
        pa, pb = Path(a).expanduser(), Path(b).expanduser()
        if pa.is_absolute() and pb.is_absolute():
            return os.path.normcase(os.path.abspath(pa)) == os.path.normcase(os.path.abspath(pb))
    except OSError:
        pass
    na, nb = a.replace("\\", "/"), b.replace("\\", "/")
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    # Suffix match ONLY on a path-component boundary: /repo/src/myapp.py must not match
    # app.py (the old bare endswith did), src/app.py must match /repo/src/app.py.
    cut = len(longer) - len(shorter)
    return longer.endswith(shorter) and longer[cut - 1 : cut] == "/"


def _mentions_source(text: str, src: str) -> bool:
    """A prose "use" mention: the basename on a path-component boundary.

    ``app.py`` in ``myapp.py`` must NOT count (the old bare-substring check inflated the
    use rate with false positives); ``src/app.py`` or a standalone ``app.py`` must. The
    lookarounds block word characters and ``.``/``-`` around the basename, so a longer
    name, a hidden-file prefix or an extension suffix all fail, while a ``/`` before the
    basename (a component boundary) still matches.
    """
    if not text or not src or src == "?":
        return False
    base = Path(src).name
    if not base:
        return False
    return re.search(rf"(?<![\w.-]){re.escape(base)}(?![\w.-])", text) is not None


def _chunks_used(row: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """True when any chunk of ``row`` is used strictly LATER in the same session."""
    row_ts = _parse_ts(row.get("ts"))
    for ch in row.get("chunks") or []:
        if not isinstance(ch, dict):
            continue
        src = str(ch.get("source") or "")
        if not src or src == "?":
            continue
        for ev in events:
            if row_ts is not None and ev["ts"] is not None and not ev["ts"] > row_ts:
                continue
            if ev["kind"] == "tool":
                if _same_target(ev["path"], src):
                    return True
            elif _mentions_source(ev["text"], src):
                return True
    return False


def _is_code_target_path(path: str) -> bool:
    from supamem.hooks.claude_code import is_code_target  # noqa: PLC0415 — avoids an import cycle

    try:
        return bool(is_code_target(Path(path)))
    except (ValueError, OSError):
        return False


def compute_stats(
    days: float = 7.0,
    *,
    journal_path: Path | None = None,
    transcript_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Usefulness metrics over the injection journal (v0.4.0a4 — the study's §5 design).

    - ``injection_rate`` — non-empty injections / invocations (baseline 4.2%).
    - ``use_rate`` — injected chunk used later in the session / non-empty rows (baseline 0.6%).
    - ``repeat_rate`` — emitted rows that suppressed an already-injected chunk. Kept for
      continuity but ~blind to the dedup filter: a row whose candidates were ALL deduped
      emits nothing (``chunks=[]``) and is invisible here — compare
      ``dedup_suppressed_rate`` instead (baseline repeats 65.6%).
    - ``dedup_suppressed_rate`` — candidate rows that suppressed a repeat / candidate rows
      (rows that had anything survive the floor: emitted, deduped, or stale-dropped).
    - ``stale_rate`` — emitted rows citing a missing source. ~0 by construction (stale
      sources are dropped BEFORE the emit) — compare ``stale_dropped_rate`` instead
      (baseline stale 8.5%).
    - ``stale_dropped_rate`` — candidate rows that lost sources to the stale drop /
      candidate rows.
    - ``lang_mismatch_rate`` — rows with a ru chunk while the edited target is a code file (baseline 78.8%).
    - ``useful_per_100_edits`` — absolute first-uses per 100 edits (Goodhart guard — must not fall).
    - ``shadow_use_rate`` — the same use metric on the shadow task-query chunks.

    Malformed rows (non-dict, non-string/naive timestamps, junk counter fields) are
    skipped or coerced — hook-stats must never crash on a bad journal line.
    """
    p = journal_path or self_journal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("ts"))
        if ts is None or ts < cutoff:
            continue
        rows.append(r)

    roots = transcript_roots if transcript_roots is not None else default_transcript_roots()
    events_by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        s = str(r.get("session") or "")
        if s and s not in events_by_session:
            transcript = find_transcript(s, roots)
            if transcript is not None:
                events_by_session[s] = _iter_events(transcript)

    def events_for(row: dict[str, Any]) -> list[dict[str, Any]]:
        return events_by_session.get(str(row.get("session") or ""), [])

    non_empty = [r for r in rows if r.get("chunks")]
    used = sum(1 for r in non_empty if _chunks_used(r, events_for(r)))
    repeats = sum(1 for r in non_empty if _safe_int(r.get("deduped")) > 0)
    stale = sum(
        1
        for r in non_empty
        if any(isinstance(c, dict) and not c.get("exists") for c in r.get("chunks") or [])
    )
    lang_mismatch = sum(
        1
        for r in non_empty
        if any(
            isinstance(c, dict)
            and c.get("lang") == "ru"
            and _is_code_target_path(str(r.get("target") or ""))
            for c in r.get("chunks") or []
        )
    )

    # Filter-aware denominators (review round 2026-11-14): the old repeat_rate counted
    # only rows that EMITTED something, so a row whose candidates were ALL deduped
    # (chunks=[], deduped>0) was invisible, and stale_rate ran on emitted chunks whose
    # "exists" flag is ~always true by construction (stale ones are dropped pre-emit).
    # The study-comparable numbers are therefore dedup_suppressed_rate and
    # stale_dropped_rate, both over "candidate rows" — rows that had anything survive
    # the score floor (emitted, deduped, or stale-dropped).
    def _row_dropped_stale(r: dict[str, Any]) -> int:
        dropped = r.get("dropped")
        return _safe_int(dropped.get("stale")) if isinstance(dropped, dict) else 0

    candidates = [
        r
        for r in rows
        if r.get("chunks") or _safe_int(r.get("deduped")) > 0 or _row_dropped_stale(r) > 0
    ]
    dedup_suppressed = sum(1 for r in candidates if _safe_int(r.get("deduped")) > 0)
    stale_dropped = sum(1 for r in candidates if _row_dropped_stale(r) > 0)

    shadow_rows = [
        r for r in rows if isinstance(r.get("shadow"), dict) and r["shadow"].get("chunks")
    ]
    shadow_used = 0
    for r in shadow_rows:
        shadow_view = dict(r)
        shadow_view["chunks"] = r["shadow"].get("chunks") or []
        if _chunks_used(shadow_view, events_for(r)):
            shadow_used += 1

    def rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    return {
        "journal_path": str(p),
        "window_days": days,
        "invocations": len(rows),
        "non_empty": len(non_empty),
        "injection_rate": rate(len(non_empty), len(rows)),
        "use_rate": rate(used, len(non_empty)),
        "used_events": used,
        "useful_per_100_edits": round(100.0 * used / len(rows), 2) if rows else 0.0,
        "repeat_rate": rate(repeats, len(non_empty)),
        "stale_rate": rate(stale, len(non_empty)),
        "lang_mismatch_rate": rate(lang_mismatch, len(non_empty)),
        "dedup_candidates": len(candidates),
        "dedup_suppressed_rate": rate(dedup_suppressed, len(candidates)),
        "stale_dropped_rate": rate(stale_dropped, len(candidates)),
        "baselines": {"repeat_rate": 0.656, "stale_rate": 0.085},
        "shadow_rows": len(shadow_rows),
        "shadow_use_rate": rate(shadow_used, len(shadow_rows)),
    }


def self_journal() -> Path:
    return journal_path()


__all__ = [
    "JOURNAL_NAME",
    "append_row",
    "chunk_key",
    "chunk_lang",
    "compute_stats",
    "dedup_marker_path",
    "default_transcript_roots",
    "empty_row",
    "find_transcript",
    "journal_path",
    "last_user_task_text",
    "load_seen",
    "save_seen",
    "source_exists",
]
