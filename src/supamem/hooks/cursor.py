"""Cursor `.cursor/rules/dual-memory-snapshot.mdc` regenerator.

Ports ``softchat/scripts/regen_cursor_dual_memory_rule.py`` into the supamem
package. Cursor's session-start hook invokes this to refresh a passive
snapshot of the dual-memory corpus: scroll the live collection, score by
recency + type-bonus, render the top-k as Markdown, and atomically replace
the existing snapshot file (D-36 — Cursor reads the .mdc on every session,
so we cap output at 500 lines / ~3000 tokens).

Sanitization: any error path scrubs ``QDRANT_URL`` and ``QDRANT_API_KEY``
substrings before emitting to stderr. Fail-soft contract: never raises.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from supamem.config import ResolvedConfig

log = logging.getLogger("supamem.hooks.cursor")

DEFAULT_OUTPUT = Path(".cursor/rules/dual-memory-snapshot.mdc")
SCROLL_LIMIT = 512
DEFAULT_TOP_K = 80
MAX_LINES = 500  # D-36 hard cap
MAX_TOKENS = 3000  # D-36 budget guard
DECAY_DAYS = 30.0

_TYPE_BONUS: tuple[tuple[str, float], ...] = (
    ("docs/adr/", 0.30),
    ("/decision", 0.30),
    (".claude/insights/", 0.15),
    (".claude/rules/", 0.05),
)

_SECRET_ENV_VARS: tuple[str, ...] = ("QDRANT_URL", "QDRANT_API_KEY")


def _sanitize(msg: str) -> str:
    out = msg
    for var in _SECRET_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val and val in out:
            out = out.replace(val, f"<{var}_REDACTED>")
    return out


def _stderr(msg: str) -> None:
    print(_sanitize(msg), file=sys.stderr)


def _type_bonus(source: str) -> float:
    s = source or ""
    for needle, bonus in _TYPE_BONUS:
        if needle in s:
            return bonus
    return 0.0


def _recency_ts(payload: dict[str, Any]) -> float:
    val = payload.get("indexed_at_epoch")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    val = payload.get("modified_ts")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    return time.time()


def _score(payload: dict[str, Any], *, now: float) -> float:
    ts = _recency_ts(payload)
    age_days = max(0.0, (now - ts) / 86400.0)
    recency = math.exp(-age_days / DECAY_DAYS) if DECAY_DAYS > 0 else 1.0
    return recency + _type_bonus(payload.get("source", ""))


def _get_client(config: ResolvedConfig) -> Any:
    """Return a configured QdrantClient. Mockable from tests."""
    from qdrant_client import QdrantClient

    return QdrantClient(
        url=config.qdrant_url,
        api_key=config.qdrant_api_key or None,
        check_compatibility=False,
        timeout=60,
    )


def _render(points: list[Any], top_k: int) -> str:
    """Score, sort, cap by MAX_LINES / MAX_TOKENS; return rendered Markdown."""
    now = time.time()
    scored: list[tuple[float, dict[str, Any]]] = []
    for p in points:
        payload = getattr(p, "payload", None) or {}
        scored.append((_score(payload, now=now), payload))
    scored.sort(key=lambda x: x[0], reverse=True)

    header = [
        "---",
        "description: supamem dual-memory snapshot (auto-generated)",
        "alwaysApply: true",
        "---",
        "",
        "# Dual-memory snapshot",
        "",
        "Auto-rendered by `supamem index --snapshot cursor`.",
        "Top-k chunks ranked by recency + type-bonus over the live Qdrant collection.",
        "",
    ]
    body: list[str] = []
    cumulative_tokens = 0
    for score, payload in scored[:top_k]:
        if len(header) + len(body) >= MAX_LINES - 4:
            break
        text = str(payload.get("document") or "").strip()
        if not text:
            continue
        approx_tokens = max(1, len(text) // 4)
        if cumulative_tokens + approx_tokens > MAX_TOKENS:
            break
        cumulative_tokens += approx_tokens
        source = str(payload.get("source") or "?")
        body.append(f"## {source}  _(score {score:.2f})_")
        body.append("")
        snippet = text.replace("\r", "")
        if len(snippet) > 600:
            snippet = snippet[:600] + " …"
        for line in snippet.splitlines()[:8]:
            if len(header) + len(body) >= MAX_LINES - 2:
                break
            body.append(f"> {line}")
        body.append("")
    rendered = "\n".join(header + body).rstrip() + "\n"
    lines = rendered.splitlines()
    if len(lines) > MAX_LINES:
        rendered = "\n".join(lines[:MAX_LINES]) + "\n"
    return rendered


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=".dual-memory-snapshot-",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(target))


def run_snapshot(
    *,
    config: ResolvedConfig,
    output_path: Optional[Path] = None,
    top_k: int = DEFAULT_TOP_K,
) -> int:
    """Regenerate the Cursor snapshot. Fail-soft: any error → stderr + exit 0."""
    target = output_path or DEFAULT_OUTPUT
    try:
        client = _get_client(config)
        points, _next_offset = client.scroll(
            collection_name=config.collection,
            limit=SCROLL_LIMIT,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft per D-36
        _stderr(f"supamem: cursor snapshot scroll failed: {exc}")
        return 0

    try:
        content = _render(points or [], top_k=top_k)
        _atomic_write(target, content)
    except Exception as exc:  # noqa: BLE001
        _stderr(f"supamem: cursor snapshot render/write failed: {exc}")
        return 0
    return 0


__all__ = ["run_snapshot", "DEFAULT_OUTPUT"]
