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
    """Append one journal line. Fail-soft: a journaling error never breaks the hook."""
    try:
        p = journal_path(cache_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
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
    """Merge ``ids`` into the session marker (atomic replace; bounded; fail-soft)."""
    if not session or not ids:
        return
    p = dedup_marker_path(session, cache_dir)
    try:
        merged = load_seen(session, cache_dir) | set(ids)
        data = {"ts": time.time(), "ids": sorted(merged)[-_DEDUP_MAX_IDS:]}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        log.debug("dedup marker save failed: %s", exc)


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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    """Assistant-side use events: tool_use Read/Edit/Write/Grep targets + text mentions."""
    try:
        text = transcript.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
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
    return events


def _same_target(a: str, b: str) -> bool:
    try:
        pa, pb = Path(a).expanduser(), Path(b).expanduser()
        if pa.is_absolute() and pb.is_absolute():
            return os.path.normcase(os.path.abspath(pa)) == os.path.normcase(os.path.abspath(pb))
    except OSError:
        pass
    na, nb = a.replace("\\", "/"), b.replace("\\", "/")
    return na.endswith(nb) or nb.endswith(na)


def _chunks_used(row: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    """True when any chunk of ``row`` is used strictly LATER in the same session."""
    row_ts = _parse_ts(row.get("ts"))
    for ch in row.get("chunks") or []:
        if not isinstance(ch, dict):
            continue
        src = str(ch.get("source") or "")
        if not src or src == "?":
            continue
        base = Path(src).name
        for ev in events:
            if row_ts is not None and ev["ts"] is not None and not ev["ts"] > row_ts:
                continue
            if ev["kind"] == "tool":
                if _same_target(ev["path"], src):
                    return True
            elif base and base in ev["text"]:
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
    - ``repeat_rate`` — non-empty rows that suppressed an already-injected chunk (baseline 65.6%).
    - ``stale_rate`` — rows citing a missing source (baseline 8.5%).
    - ``lang_mismatch_rate`` — rows with a ru chunk while the edited target is a code file (baseline 78.8%).
    - ``useful_per_100_edits`` — absolute first-uses per 100 edits (Goodhart guard — must not fall).
    - ``shadow_use_rate`` — the same use metric on the shadow task-query chunks.
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
    repeats = sum(1 for r in non_empty if int(r.get("deduped") or 0) > 0)
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
