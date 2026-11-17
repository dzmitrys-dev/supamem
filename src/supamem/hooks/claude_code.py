"""Claude Code session/edit hook for supamem.

Ports ``softchat/scripts/dual_memory_bootstrap.py`` into a library module
that lives next to the package. Hook contract (Phase 80.3):

- Stdin: the Claude Code PreToolUse payload; the edited path is ``tool_input.file_path`` (see
  ``resolve_file_path``). ``--file-path`` still overrides for other hosts. ``session_id``,
  ``transcript_path`` and ``cwd`` drive the v0.4.0a4 usefulness pipeline.
- Stdout: exactly one line ``{"hookSpecificOutput": {"hookEventName":
  "PreToolUse", "additionalContext": "<retrieved chunks>"}}``
- Exit: always 0 (fail-soft per PATTERNS — never block the calling tool)
- Side effects: marker file at /tmp/<slug>-queried-YYYYMMDD; counter bump
  via supamem.stats.counter.bump; one line per invocation in the injection
  journal (``supamem.stats.injections``).

v0.4.0a4 pipeline (owner-approved plan from the 2026-11-14 effectiveness study):

1. The project root is the MAIN checkout (``git rev-parse --git-common-dir``), so
   worktree sessions (42% of calls, previously 100% empty) see the project's memory.
2. Pipeline order: query(k=fetch_k) → project filter → rerank-score floor → stale drop
   → session dedup → [:inject_k].
3. Pointer-only output format, capped at 2000 chars.
4. Shadow task query: the last user message from the transcript is queried alongside the
   path tokens and JOURNALED (never emitted) so its use rate can be measured before any
   emit flip.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk
from supamem.stats.injections import (
    ZERO_DROPPED,
    append_row,
    chunk_key,
    chunk_lang,
    last_user_task_text,
    load_seen,
    save_seen,
    source_exists,
)

log = logging.getLogger("supamem.hooks.claude_code")

# Default tokens to drop from query derivation when the user hasn't
# overridden ``config.drop_tokens``. Verbatim from the SoftChat hook.
_DEFAULT_DROP_TOKENS: tuple[str, ...] = (
    "src",
    "tests",
    "test",
    "app",
    "api",
    "v1",
    "fastapi_service",
    "nuxt_app",
    "infrastructure",
    "application",
    "domain",
    "shared",
    "entities",
    "features",
    "widgets",
    "pages",
    "__init__",
)
_GENERIC_STEMS: frozenset[str] = frozenset(
    {
        "models",
        "model",
        "service",
        "services",
        "handler",
        "handlers",
        "repository",
        "repositories",
        "endpoint",
        "endpoints",
        "route",
        "routes",
        "schema",
        "schemas",
        "utils",
        "helpers",
        "main",
        "index",
    }
)

_REJECT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".env",
        ".cfg",
        ".ini",
        ".lock",
    }
)


def is_code_target(file_path: Path, *, globs: Iterable[str] = ()) -> bool:
    """Should this edit trigger a memory search?

    Default heuristic: the path lives under ``src/`` or ``tests/``. With
    ``[supamem.hook] target_globs`` set (v0.4.0a5), the heuristic is replaced by an
    any-match ``fnmatch`` over the repo-relative posix path — for repos whose code
    lives elsewhere (cl-harness: ``packages/**``, ``bench-ab/**``). ``fnmatch`` has no
    path semantics: ``*`` crosses ``/`` boundaries, so ``packages/*`` matches
    ``packages/hx/src/x.mts``. Reject-suffixes always apply — docs/config edits never
    trigger a search.
    """
    if file_path.suffix in _REJECT_SUFFIXES:
        return False
    text = file_path.as_posix()
    if not globs:
        in_src = (
            "/src/" in text or "/tests/" in text or text.startswith("src/") or text.startswith("tests/")
        )
        return in_src
    from fnmatch import fnmatch  # noqa: PLC0415 — stdlib, micro-import

    return any(fnmatch(text, g) for g in globs)


def _target_globs(config: ResolvedConfig) -> list[str]:
    raw = _hook_setting(config, "target_globs", [])
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(g) for g in raw if isinstance(g, str) and g.strip()]


def derive_query(
    file_path: Path,
    drop_tokens: Optional[Iterable[str]] = None,
) -> str:
    """Tokenize the file path's basename, strip noise, return ≤3 tokens."""
    drop = set(drop_tokens) if drop_tokens is not None else set(_DEFAULT_DROP_TOKENS)
    stem = file_path.stem.lower()
    parent = file_path.parent.name.lower()

    def normalize(raw: str) -> list[str]:
        return [p for p in re.split(r"[_\-/\.]+", raw.lower()) if p and p not in drop]

    if stem in _GENERIC_STEMS or stem in drop:
        tokens = normalize(parent) + normalize(stem)
    else:
        tokens = normalize(stem)

    deduped: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)
    return " ".join(deduped[:3])


# ---- Backend cache, marker dir, counter (mockable for tests) -------------


_BACKEND_CACHE: dict[int, TunedHybridBackend] = {}


def _get_backend(config: ResolvedConfig) -> TunedHybridBackend:
    key = id(config)
    if key not in _BACKEND_CACHE:
        _BACKEND_CACHE[key] = TunedHybridBackend(config=config)
    return _BACKEND_CACHE[key]


def _marker_dir() -> Path:
    return Path("/tmp")


def _slug() -> str:
    """Repo slug used in the marker filename — basename of cwd."""
    try:
        return Path.cwd().name or "supamem"
    except OSError:
        return "supamem"


def _touch_marker(kind: str = "queried") -> None:
    try:
        target = _marker_dir() / f"{_slug()}-{kind}-{date.today().strftime('%Y%m%d')}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    except OSError as exc:
        log.debug("marker touch failed: %s", exc)


def _bump(kind: str, source: str, tokens: int, latency_ms: float, **_kw: Any) -> None:
    try:
        from supamem.stats.counter import bump as real_bump
    except ImportError:
        return
    try:
        real_bump(kind, source, tokens, latency_ms)
    except Exception as exc:  # noqa: BLE001 — counter must never block
        log.debug("counter bump failed: %s", exc)


@contextlib.contextmanager
def _models_offline() -> Iterator[None]:
    """Never download a model on the edit path: a missing model must fail soft at once, not hang the edit until
    the hook timeout (field report 2026-11-06). Models are fetched outside hooks (`supamem repair`, indexing).
    Scoped: the previous value is restored, so a long-lived process is not switched offline for good."""
    prev = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = prev


# ---- v0.4.0a4 pipeline knobs (config: [supamem.hook]) ---------------------

# Over-fetch, then keep only this project's memory: a collection can hold chunks indexed from elsewhere (temp
# checkouts under /private/tmp, other repos) — field report 2026-11-06 found both injected into edits. v0.4.0a4
# raised the fetch from 10 to 30 so other repos' chunks cannot crowd the project's own out of the fetch window
# (the filter runs AFTER the fetch cut, so a small window guaranteed zero hits in mixed collections).
_FETCH_K = 30
_INJECT_K = 5
_MIN_SCORE = 2.5  # rerank logits: useful chunks 3.5–5.8, noise < 1.5 (2026-11-14 study)
# COST: shadow_task_query roughly DOUBLES the retrieval work per code-target edit (a second
# full query runs even when the main path injects nothing — that is the comparison the
# shadow_use_rate metric needs). Owner accepts the latency; disable with
# [supamem.hook] shadow_task_query = false once the channel is measured.
_SHADOW_TASK_QUERY = True

_POINTER_HEADER = "### Memory context (supamem)"
_POINTER_FOOTER = "Pointers only: open the file before relying on it."
_POINTER_CAP = 2000


def _hook_setting(config: ResolvedConfig, name: str, default: Any) -> Any:
    """Read ``[supamem.hook]`` flat fields (``hook_fetch_k`` …), falling back to the module default."""
    return getattr(config, f"hook_{name}", default)


def main_checkout_root(cwd: Path) -> Path:
    """Map a (worktree) cwd to the main checkout root; outside git keep the cwd.

    A linked worktree's chunk payloads carry the main checkout's absolute paths, while
    ``Path.cwd()`` is the worktree — ``_in_project`` could therefore never match
    (9,169 guaranteed-zero searches in the study). ``--path-format=absolute`` needs
    git ≥ 2.31; older git or a non-git dir falls back to the cwd (old behaviour).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return cwd
    if out.returncode != 0:
        return cwd
    common = out.stdout.strip()
    if not common:
        return cwd
    p = Path(common)
    if not p.is_absolute():
        p = (cwd / p).resolve()
    if p.name == ".git":
        return p.parent
    return cwd  # bare repo / exotic GIT_DIR — keep the old behaviour


# ---- stdin payload ---------------------------------------------------------


def read_stdin_text() -> str:
    """The hook payload arrives ONLY as stdin JSON; never block on a TTY."""
    try:
        return "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except (OSError, ValueError):
        return ""


def parse_payload(stdin_text: str) -> dict[str, Any]:
    try:
        data = json.loads(stdin_text or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_file_path(cli_path: Optional[Path], stdin_text: str) -> Optional[Path]:
    """The edited path: an explicit ``--file-path`` wins; otherwise the Claude Code PreToolUse payload on stdin
    (``tool_input.file_path``, or ``notebook_path`` for NotebookEdit). Claude Code passes hook input ONLY as stdin
    JSON — ``$CLAUDE_FILE_PATH`` is not a hook variable, so the old registration always ran with an empty path
    (field report 2026-11-06). Garbage → None (the hook stays fail-soft)."""
    if cli_path is not None and str(cli_path) not in ("", "."):
        return cli_path
    payload = parse_payload(stdin_text)
    if not payload:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return Path(value)
    return None


# ---- project filter + stages -------------------------------------------------


def _in_project(chunks: list[RetrievedChunk], root: Path) -> list[RetrievedChunk]:
    """Chunks whose source lies inside ``root`` (relative paths are taken as project-relative)."""
    try:
        base = root.resolve()
    except OSError:
        return list(chunks)
    out: list[RetrievedChunk] = []
    for c in chunks:
        src = c.source_path or c.file_path
        if not src:
            continue
        p = Path(src)
        if not p.is_absolute():
            out.append(c)
            continue
        try:
            if p.resolve().is_relative_to(base):
                out.append(c)
        except OSError:
            continue
    return out


def _filter_chunks(
    chunks: list[RetrievedChunk],
    root: Path,
    *,
    min_score: float,
    seen: set[str],
) -> tuple[list[RetrievedChunk], int, dict[str, int]]:
    """project filter → score floor → stale drop → session dedup, with per-stage counts.

    The floor applies ONLY to reranker-produced scores (``rerank_score is not None``):
    raw RRF scores live on a different scale and must not be floored. ``min_score = 0``
    disables the floor entirely.
    """
    dropped = dict(ZERO_DROPPED)
    in_project = _in_project(chunks, root)
    dropped["foreign"] = len(chunks) - len(in_project)

    floored: list[RetrievedChunk] = []
    for c in in_project:
        if min_score > 0 and c.rerank_score is not None and float(c.rerank_score) < min_score:
            dropped["floor"] += 1
            continue
        floored.append(c)

    live: list[RetrievedChunk] = []
    for c in floored:
        src = c.source_path or c.file_path
        if src and not source_exists(src, root):
            dropped["stale"] += 1
            continue
        live.append(c)

    kept: list[RetrievedChunk] = []
    batch_ids: set[str] = set()
    deduped = 0
    for c in live:
        key = chunk_key(c.source_path or c.file_path, c.text or "")
        if key in seen:
            deduped += 1
            continue
        if key in batch_ids:  # exact duplicate inside one fetch
            continue
        batch_ids.add(key)
        kept.append(c)
    return kept, deduped, dropped


# ---- pointer format ----------------------------------------------------------


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")
_HEADER_KEYS = ("Header 1", "Header 2", "Header 3", "Header 4", "Header 5", "Header 6")


def _rel_source(src: str, root: Path) -> str:
    p = Path(src)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return src


def _first_header(c: RetrievedChunk) -> str:
    """The first markdown header recorded in the chunk payload, if any."""
    payload = c.payload if isinstance(c.payload, dict) else {}
    headers = payload.get("headers") or c.headers
    if not isinstance(headers, dict):
        return ""
    for key in _HEADER_KEYS:
        value = headers.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in headers.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pointer_line(c: RetrievedChunk, root: Path) -> str:
    src = _rel_source(c.source_path or c.file_path or "?", root)
    header = _first_header(c)
    full = " ".join((c.text or "").strip().split())
    sentence = _SENTENCE_SPLIT.split(full, maxsplit=1)
    shown = (sentence[0] if sentence else full)[:160]
    suffix = "…" if len(shown) < len(full) else ""
    label = f"- **{src}**"
    if header:
        label += f" — {header}"
    return f"{label} (score {float(c.score or 0.0):.2f}): {shown}{suffix}"


def _format_chunks(chunks: list[RetrievedChunk], root: Path) -> str:
    if not chunks:
        return ""
    lines = [_POINTER_HEADER, *(_pointer_line(c, root) for c in chunks), _POINTER_FOOTER]
    while len("\n".join(lines)) > _POINTER_CAP and len(lines) > 3:
        lines.pop(-2)  # drop the last chunk line, keep the footer last
    text = "\n".join(lines)
    if len(text) > _POINTER_CAP:
        text = text[: _POINTER_CAP - 1] + "…"
    return text


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---- journal -------------------------------------------------------------------


def _safe_float(value: Any) -> float:
    """Journal-visible float that never raises and never emits json ``NaN``.

    ``json.dumps`` writes bare ``NaN`` for NaN floats — invalid JSON for any strict
    parser downstream; a non-numeric score (should the pydantic guard on
    ``RetrievedChunk`` ever loosen) used to raise straight through ``_journal_row``.
    """
    try:
        f = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and f not in (float("inf"), float("-inf")) else 0.0


def _journal_row(
    *,
    session: str,
    target: str,
    query: str,
    chunks: list[RetrievedChunk],
    root: Path,
    empty: bool,
    deduped: int,
    dropped: dict[str, int],
    ms: float,
    shadow: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "target": target,
        "query": query,
        "chunks": [
            {
                "source": c.source_path or c.file_path or "?",
                "score": round(_safe_float(c.score), 4),
                "exists": bool(source_exists(c.source_path or c.file_path or "", root))
                if (c.source_path or c.file_path)
                else False,
                "lang": chunk_lang(c.text or ""),
            }
            for c in chunks
        ],
        "empty": empty,
        "deduped": deduped,
        "dropped": dropped,
        "ms": round(_safe_float(ms), 1),
        "shadow": shadow,
    }


def _shadow_result(
    backend: TunedHybridBackend,
    *,
    base_query: str,
    transcript_path: str,
    root: Path,
    min_score: float,
    fetch_k: int,
    inject_k: int,
    seen: set[str],
) -> dict[str, Any] | None:
    """Query `task text + path tokens` through the SAME filters; journaled, never emitted.

    COST NOTE (review round 2026-11-14, owner accepts the latency): with
    ``shadow_task_query = true`` every code-target edit runs a SECOND full retrieval
    (embed + query + rerank) on top of the main path query — the shadow keeps running
    even when the main path injects nothing, because that exactly is the comparison the
    metric needs (does the task text find what the path tokens miss?). Roughly doubles
    the backend work per edit; disable with ``[supamem.hook] shadow_task_query = false``
    once ``shadow_use_rate`` justifies (or refutes) the channel.
    """
    try:
        task = last_user_task_text(Path(transcript_path))
        if not task:
            return None
        shadow_query = f"{task} {base_query}".strip()
        with _models_offline():
            raw = backend.query(shadow_query, k=fetch_k)
        kept, _deduped, _dropped = _filter_chunks(raw, root, min_score=min_score, seen=seen)
        return {
            "query": shadow_query,
            "chunks": [
                {
                    "source": c.source_path or c.file_path or "?",
                    "score": round(_safe_float(c.score), 4),
                }
                for c in kept[:inject_k]
            ],
        }
    except Exception as exc:  # noqa: BLE001 — the shadow must never break the hook
        log.debug("shadow task query failed: %s", exc)
        return None


# ---- Hook entry point ----------------------------------------------------


def run(file_path: Path, config: ResolvedConfig, payload: dict[str, Any] | None = None) -> int:
    """Main hook entry — emit hookSpecificOutput JSON, exit 0 always."""
    empty_payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "",
        }
    }
    payload = payload if isinstance(payload, dict) else {}
    session = str(payload.get("session_id") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    cwd = Path(payload.get("cwd") or Path.cwd())
    root = main_checkout_root(cwd)
    t0 = time.perf_counter()
    # Structural single-emit guarantee (review round 2026-11-14): the hook contract is
    # EXACTLY ONE stdout JSON per invocation. A journal crash used to land in the outer
    # handler below and emit a SECOND payload. `emitted` flips only after a successful
    # write, so the handler emits only when no payload went out yet.
    emitted = False

    def _emit_once(payload_out: dict[str, Any]) -> None:
        nonlocal emitted
        _emit(payload_out)
        emitted = True

    def journal(**kw: Any) -> None:
        try:
            row = _journal_row(
                session=session,
                target=str(file_path),
                root=root,
                ms=(time.perf_counter() - t0) * 1000.0,
                **kw,
            )
            append_row(row)
        except Exception as exc:  # noqa: BLE001 — journaling must never reach the emit path
            log.warning("supamem hook claude_code: journal failed: %s", exc)

    try:
        # target_globs match the REPO-RELATIVE path ("packages/hx/src/x.mts"), not the
        # absolute one — the heuristic fallback works on either form.
        target_for_globs = file_path
        try:
            target_for_globs = file_path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            pass
        if not is_code_target(target_for_globs, globs=_target_globs(config)):
            journal(
                query="", chunks=[], empty=True, deduped=0, dropped=dict(ZERO_DROPPED), shadow=None
            )
            _emit_once(empty_payload)
            _touch_marker()
            return 0

        drop = list(getattr(config, "drop_tokens", None) or _DEFAULT_DROP_TOKENS)
        query = derive_query(file_path, drop_tokens=drop)
        if not query:
            journal(
                query="", chunks=[], empty=True, deduped=0, dropped=dict(ZERO_DROPPED), shadow=None
            )
            _emit_once(empty_payload)
            _touch_marker()
            return 0

        fetch_k = int(_hook_setting(config, "fetch_k", _FETCH_K))
        inject_k = int(_hook_setting(config, "inject_k", _INJECT_K))
        min_score = float(_hook_setting(config, "min_score", _MIN_SCORE))
        shadow_on = bool(_hook_setting(config, "shadow_task_query", _SHADOW_TASK_QUERY))

        try:
            backend: TunedHybridBackend
            with _models_offline():
                backend = _get_backend(
                    config
                )  # construction stays cache-only too (field report 2026-11-06)
                raw = backend.query(query, k=fetch_k)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            log.warning("supamem hook claude_code: backend failed: %s", exc)
            journal(
                query=query,
                chunks=[],
                empty=True,
                deduped=0,
                dropped=dict(ZERO_DROPPED),
                shadow=None,
            )
            _emit_once(empty_payload)
            _touch_marker()
            return 0

        seen = load_seen(session)
        kept, deduped, dropped = _filter_chunks(raw, root, min_score=min_score, seen=seen)
        final = kept[:inject_k]

        shadow = None
        if shadow_on and transcript_path:
            shadow = _shadow_result(
                backend,
                base_query=query,
                transcript_path=transcript_path,
                root=root,
                min_score=min_score,
                fetch_k=fetch_k,
                inject_k=inject_k,
                seen=seen,
            )

        if session and final:
            save_seen(
                session,
                {chunk_key(c.source_path or c.file_path, c.text or "") for c in final},
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        context_text = _format_chunks(final, root)
        _emit_once(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": context_text,
                }
            }
        )
        journal(
            query=query,
            chunks=final,
            empty=not final,
            deduped=deduped,
            dropped=dropped,
            shadow=shadow,
        )
        total_tokens = sum(max(1, len(c.text or "") // 4) for c in final)
        _bump(
            kind="search",
            source="hook_claude_code",
            tokens=total_tokens,
            latency_ms=elapsed_ms,
        )
        _touch_marker()
        return 0
    except Exception as exc:  # noqa: BLE001 — outermost fail-soft
        log.warning("supamem hook claude_code: unhandled: %s", exc)
        if not emitted:  # never a second JSON on stdout
            try:
                _emit(empty_payload)
            except Exception:  # noqa: BLE001
                pass
        return 0


__all__ = [
    "derive_query",
    "is_code_target",
    "main_checkout_root",
    "parse_payload",
    "read_stdin_text",
    "resolve_file_path",
    "run",
]
