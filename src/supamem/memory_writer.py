"""Agent memory write path (v0.1.3+).

Lets MCP-connected agents save research findings / insights mid-session by:
1. Writing a deterministic Markdown file with YAML frontmatter to
   ``<project>/.claude/insights/_agent/<slug>.md``.
2. Immediately upserting that single doc into the project's tuned-hybrid
   Qdrant collection so the very next ``dual_memory_search`` sees it.

Idempotency: same ``topic`` → same ``slug`` → same on-disk path → same
``UUIDv5`` Qdrant point ID. Re-writing the same topic overwrites in place.

This module composes existing primitives (``indexer._index_doc``, the dense
and sparse embedders) — no new embedding or upsert logic is introduced.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from supamem.config import ResolvedConfig
from supamem.qdrant_collection import ensure_collection, validate_writable_collection

log = logging.getLogger("supamem.memory_writer")

# ── Limits ──────────────────────────────────────────────────────────────────
MAX_TOPIC_LEN = 120
MAX_CONTENT_LEN = 64_000
MAX_DESCRIPTION_LEN = 300
MAX_TAGS = 10
MAX_TAG_LEN = 32
SLUG_MAX_LEN = 64

# Stable UUIDv5 namespace for slug → point-id derivation. Generated once with
# `uuid.uuid5(uuid.NAMESPACE_URL, "supamem.dual_memory_write")` and frozen here.
NAMESPACE_AGENT_WRITE = uuid.UUID("0e6c4d3f-3a8c-5b8b-9f2e-7c8b4a4f1d72")

# Subdirectory under the FIRST source root that we accept as the agent-write
# target. Always relative to project root via the ``sources`` config.
AGENT_WRITE_DIRNAME = "_agent"


@dataclass(frozen=True)
class WriteResult:
    summary: str
    path: str
    topic: str
    slug: str
    indexed: bool
    points_added: int
    error: str | None = None


# ── Slug helpers ────────────────────────────────────────────────────────────


def _slugify(topic: str) -> str:
    s = topic.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "untitled"
    return s[:SLUG_MAX_LEN]


def _point_id_for_slug(slug: str) -> str:
    return str(uuid.uuid5(NAMESPACE_AGENT_WRITE, slug))


# ── Path resolution ─────────────────────────────────────────────────────────


def _resolve_write_root(cfg: ResolvedConfig, project_root: Path) -> Path:
    """Pick the first directory-ish source as the agent-write parent.

    Falls back to ``.claude/insights/`` if no directory source is configured.
    """
    for src in cfg.sources or []:
        candidate = (project_root / src).resolve()
        # Source ending in '/' or pointing at an existing directory wins
        if src.endswith("/") or candidate.is_dir():
            return candidate / AGENT_WRITE_DIRNAME
    # Fallback — matches supamem's auto_detect default
    return (project_root / ".claude" / "insights").resolve() / AGENT_WRITE_DIRNAME


def _safe_target_path(write_root: Path, slug: str, project_root: Path) -> Path:
    """Resolve ``<write_root>/<slug>.md`` and refuse anything outside project root."""
    target = (write_root / f"{slug}.md").resolve()
    project_root = project_root.resolve()
    try:
        target.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(
            f"agent-write target {target} escapes project root {project_root}"
        ) from exc
    return target


# ── Atomic write with frontmatter ───────────────────────────────────────────


def _build_markdown(
    *,
    topic: str,
    content: str,
    description: str | None,
    tags: list[str] | None,
    slug: str,
) -> str:
    fm = {
        "topic": slug,
        "name": topic,
        "type": "agent-write",
        "description": (description or topic)[:MAX_DESCRIPTION_LEN],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if tags:
        fm["tags"] = [t.strip()[:MAX_TAG_LEN] for t in tags if t and t.strip()][:MAX_TAGS]
    body = (content or "").rstrip() + "\n"
    rendered_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{rendered_fm}\n---\n\n{body}"


def _atomic_write(target: Path, contents: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(contents, encoding="utf-8")
    tmp.replace(target)


# ── Single-doc Qdrant upsert (composes the indexer primitives) ──────────────


def _index_single_doc(
    cfg: ResolvedConfig,
    *,
    target_path: Path,
    body: str,
    point_id: str,
) -> int:
    """Upsert one Markdown doc into the tuned-hybrid collection. Returns chunks added.

    Uses ``UUIDv5(slug)`` as the FIRST chunk's id so re-writes overwrite in
    place. Subsequent chunks (if the doc spans multiple T-1 chunks) get
    deterministic ``UUIDv5(slug + ":" + idx)`` IDs.

    When ``cfg.dedup_enabled``, skips later points in the same upsert batch
    that share the same file-level ``content_hash`` (no collection-wide scan).
    """
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    from supamem.embedders import build_dense_embedder, build_sparse_embedder
    from supamem.indexer import DENSE_VECTOR, SPARSE_VECTOR
    from supamem.indexer.chunker import chunk_markdown

    chunks = chunk_markdown(body) or [body]
    if not chunks:
        return 0

    client = QdrantClient(
        url=cfg.qdrant_url,
        api_key=cfg.qdrant_api_key or None,
        check_compatibility=False,
        timeout=60,
    )
    validate_writable_collection(cfg)
    ensure_collection(client, cfg.collection)
    dense = build_dense_embedder()
    sparse = build_sparse_embedder()

    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    abs_path = str(target_path.resolve())
    points: list[Any] = []
    seen_hashes: set[str] = set()
    for idx, chunk in enumerate(chunks):
        if cfg.dedup_enabled:
            if sha in seen_hashes:
                continue
            seen_hashes.add(sha)
        chunk_id = (
            point_id
            if idx == 0
            else str(uuid.uuid5(NAMESPACE_AGENT_WRITE, f"{point_id}:{idx}"))
        )
        dense_vec = [float(x) for x in next(dense.embed([chunk]))]
        sparse_vec = next(sparse.embed([chunk]))
        points.append(
            qmodels.PointStruct(
                id=chunk_id,
                vector={
                    DENSE_VECTOR: dense_vec,
                    SPARSE_VECTOR: qmodels.SparseVector(
                        indices=[int(i) for i in sparse_vec.indices],
                        values=[float(v) for v in sparse_vec.values],
                    ),
                },
                payload={
                    "file_path": abs_path,
                    "chunk_idx": idx,
                    "content_hash": sha,
                    "document": chunk,
                    "type": "agent-write",
                },
            )
        )
    if not points:
        return 0
    client.upsert(collection_name=cfg.collection, points=points, wait=True)
    return len(points)


# ── Public entry point ──────────────────────────────────────────────────────


def write_memory(
    *,
    topic: str,
    content: str,
    description: str | None = None,
    tags: list[str] | None = None,
    config: ResolvedConfig | None = None,
    project_root: Path | None = None,
) -> WriteResult:
    """Write + index an agent-authored memory. Idempotent on ``topic``.

    Validation errors raise ``ValueError`` (translated to ``ToolError`` by the
    MCP wrapper). Indexing failures DO NOT delete the on-disk file — they
    return ``indexed=False`` with the error message so the agent can retry
    later (e.g. after a Qdrant restart).
    """
    if not (topic or "").strip():
        raise ValueError("topic required")
    if len(topic) > MAX_TOPIC_LEN:
        raise ValueError(f"topic too long (>{MAX_TOPIC_LEN} chars)")
    if len(content or "") > MAX_CONTENT_LEN:
        raise ValueError(f"content too long (>{MAX_CONTENT_LEN} chars)")
    if description and len(description) > MAX_DESCRIPTION_LEN:
        raise ValueError(f"description too long (>{MAX_DESCRIPTION_LEN} chars)")
    if tags and len(tags) > MAX_TAGS:
        raise ValueError(f"too many tags (>{MAX_TAGS})")

    cfg = config or ResolvedConfig()
    project_root = (project_root or Path.cwd()).resolve()

    slug = _slugify(topic)
    write_root = _resolve_write_root(cfg, project_root)
    target = _safe_target_path(write_root, slug, project_root)
    point_id = _point_id_for_slug(slug)

    md = _build_markdown(
        topic=topic, content=content, description=description, tags=tags, slug=slug
    )
    _atomic_write(target, md)

    indexed = False
    points_added = 0
    err: str | None = None
    try:
        points_added = _index_single_doc(
            cfg, target_path=target, body=content, point_id=point_id
        )
        indexed = points_added > 0
    except Exception as exc:  # noqa: BLE001 — surface to caller, file is still useful
        log.exception("agent-write index failed: %s", exc)
        err = f"{type(exc).__name__}: {exc}"

    summary = (
        f"saved memory {topic!r} → {target.relative_to(project_root)} "
        f"({'indexed' if indexed else 'NOT indexed'}, {points_added} chunks)"
    )
    return WriteResult(
        summary=summary,
        path=str(target),
        topic=topic,
        slug=slug,
        indexed=indexed,
        points_added=points_added,
        error=err,
    )


__all__ = [
    "WriteResult",
    "write_memory",
    "MAX_TOPIC_LEN",
    "MAX_CONTENT_LEN",
    "MAX_DESCRIPTION_LEN",
    "MAX_TAGS",
    "MAX_TAG_LEN",
    "NAMESPACE_AGENT_WRITE",
    "AGENT_WRITE_DIRNAME",
]
