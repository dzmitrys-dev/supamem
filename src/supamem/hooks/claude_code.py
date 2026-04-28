"""Claude Code session/edit hook for supamem.

Ports ``softchat/scripts/dual_memory_bootstrap.py`` into a library module
that lives next to the package. Hook contract (Phase 80.3):

- Stdin: nothing (file_path comes from the hook invocation)
- Stdout: exactly one line ``{"hookSpecificOutput": {"hookEventName":
  "PreToolUse", "additionalContext": "<retrieved chunks>"}}``
- Exit: always 0 (fail-soft per PATTERNS — never block the calling tool)
- Side effects: marker file at /tmp/<slug>-queried-YYYYMMDD; counter bump
  via supamem.stats.counter.bump.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk

log = logging.getLogger("supamem.hooks.claude_code")

# Default tokens to drop from query derivation when the user hasn't
# overridden ``config.drop_tokens``. Verbatim from the SoftChat hook.
_DEFAULT_DROP_TOKENS: tuple[str, ...] = (
    "src", "tests", "test", "app", "api", "v1",
    "fastapi_service", "nuxt_app",
    "infrastructure", "application", "domain",
    "shared", "entities", "features", "widgets", "pages",
    "__init__",
)
_GENERIC_STEMS: frozenset[str] = frozenset({
    "models", "model", "service", "services",
    "handler", "handlers", "repository", "repositories",
    "endpoint", "endpoints", "route", "routes",
    "schema", "schemas", "utils", "helpers",
    "main", "index",
})

_REJECT_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".json", ".yaml", ".yml", ".toml",
    ".env", ".cfg", ".ini", ".lock",
})


def is_code_target(file_path: Path) -> bool:
    text = file_path.as_posix()
    in_src = (
        "/src/" in text
        or "/tests/" in text
        or text.startswith("src/")
        or text.startswith("tests/")
    )
    if not in_src:
        return False
    return file_path.suffix not in _REJECT_SUFFIXES


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


# ---- Output formatting ---------------------------------------------------


def _format_chunks(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    lines = ["### Memory context (supamem)"]
    for c in chunks:
        src = c.source_path or c.file_path or "?"
        score_pct = int(round(float(c.score or 0.0) * 100))
        lines.append(f"- **{src}** ({score_pct}%): {c.text.strip()[:280]}")
    return "\n".join(lines)


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---- Hook entry point ----------------------------------------------------


def run(file_path: Path, config: ResolvedConfig) -> int:
    """Main hook entry — emit hookSpecificOutput JSON, exit 0 always."""
    empty_payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "",
        }
    }

    try:
        if not is_code_target(file_path):
            _emit(empty_payload)
            _touch_marker()
            return 0

        drop = list(getattr(config, "drop_tokens", None) or _DEFAULT_DROP_TOKENS)
        query = derive_query(file_path, drop_tokens=drop)
        if not query:
            _emit(empty_payload)
            _touch_marker()
            return 0

        t0 = time.perf_counter()
        try:
            chunks = _get_backend(config).query(query, k=5)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            log.warning("supamem hook claude_code: backend failed: %s", exc)
            _emit(empty_payload)
            _touch_marker()
            return 0
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        context_text = _format_chunks(chunks)
        _emit({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": context_text,
            }
        })
        total_tokens = sum(max(1, len(c.text) // 4) for c in chunks)
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
        try:
            _emit(empty_payload)
        except Exception:  # noqa: BLE001
            pass
        return 0


__all__ = ["derive_query", "is_code_target", "run"]
