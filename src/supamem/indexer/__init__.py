"""Indexer entry point — ports ``embed-dev-memories.py`` to the D-25 hybrid schema.

``run_index(target, force, sources, config)`` walks the source globs, chunks
each Markdown doc OR Claude Code session JSONL, computes both dense + sparse
vectors, and upserts to the configured Qdrant collection. Fail-soft: any
Qdrant or fastembed import / RPC error short-circuits to ``return 0`` so
calling hooks never break.

Plan 06-03 wires the transcript chunker into this dispatcher (Pattern 2):
``*.md`` sources flow through ``chunk_markdown`` (returns ``list[str]``),
``*.jsonl`` sources flow through ``chunk_transcript`` (returns
``list[ChunkRecord]`` with per-pair metadata). ``_normalize_chunks`` adapts
the legacy shape to ``ChunkRecord`` so the rest of the pipeline is uniform.

Per-message dedupe (D-25, D-27) keys on ``(session_id, user_uuid,
content_hash)`` via ``Manifest.transcript_needs_index``. Re-running on an
unchanged transcript corpus produces zero upserts.

W4 (fail-loud): a transcript ``ChunkRecord`` missing ``session_id`` or
``user_uuid`` indicates an upstream parser-contract violation (the parser
in 06-01 guarantees these per INGEST-05) and is NOT swallowed by the
fail-soft envelope — it raises ``ValueError`` so the bug surfaces.

B3, D-22: the per-source iteration is wrapped in a Rich ``Progress`` bar
attached to ``supamem.console.console`` when stdout is a real terminal and
``NO_COLOR`` is unset; CI / NO_COLOR / non-tty paths skip the bar entirely
to keep smoke-test output deterministic.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, Literal

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from supamem.config import ResolvedConfig
from supamem.console import console
from supamem.embedders import build_dense_embedder, build_sparse_embedder
from supamem.indexer.chunker import CHUNK_MIN_TOKENS, _token_count, chunk_markdown
from supamem.indexer.manifest import Manifest
from supamem.indexer.transcript import ChunkRecord, chunk_transcript

try:
    from qdrant_client import QdrantClient  # noqa: F401
except ImportError:  # qdrant-client missing — fail-soft path still works
    QdrantClient = None  # type: ignore[assignment, misc]

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
DEFAULT_VECTOR_SIZE = 384

Target = Literal["prod", "tuned", "both"]

__all__ = [
    "DENSE_VECTOR",
    "SPARSE_VECTOR",
    "Progress",
    "QdrantClient",
    "_expand_sources",
    "_normalize_chunks",
    "_progress_enabled",
    "build_dense_embedder",
    "build_sparse_embedder",
    "chunk_markdown",
    "chunk_transcript",
    "run_index",
]


# ---------------------------------------------------------------------------
# Source expansion + helpers
# ---------------------------------------------------------------------------


def _expand_sources(sources: Iterable[str]) -> list[Path]:
    """Walk the source list; collect ``*.md`` and ``*.jsonl`` files (Plan 06-03)."""
    out: list[Path] = []
    for s in sources:
        p = Path(s)
        if p.is_file() and p.suffix in (".md", ".jsonl"):
            out.append(p.resolve())
        elif p.is_dir():
            for ext in ("*.md", "*.jsonl"):
                out.extend(sorted(q.resolve() for q in p.rglob(ext)))
    return sorted(set(out))


def _compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _chunk_id(file_path: str, idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}#chunk={idx}"))


def _transcript_chunk_id(session_uuid: str, message_uuid: str, idx: int) -> str:
    """Deterministic chunk id for transcript chunks (Plan 06-03 Task 3).

    Pattern: ``{session_uuid}#message={message_uuid}#chunk={idx}`` so a
    re-embed of the same (session, message) at the same chunk slot
    overwrites the prior point in Qdrant rather than duplicating it.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{session_uuid}#message={message_uuid}#chunk={idx}",
        )
    )


def _manifest_path(config: ResolvedConfig) -> Path:
    base = Path(config.cache_dir) if config.cache_dir else Path.home() / ".cache" / "supamem"
    return base / "manifest.json"


# ---------------------------------------------------------------------------
# Pattern 2 — adapter for both chunker return shapes
# ---------------------------------------------------------------------------


def _normalize_chunks(
    result: Any, *, default_metadata: dict
) -> list[ChunkRecord]:
    """Adapt ``list[str]`` (markdown) or ``list[ChunkRecord]`` (transcript).

    Returns the same shape (``list[ChunkRecord]``) so the rest of the
    dispatcher is uniform regardless of source type.
    """
    if not result:
        return []
    first = result[0]
    if isinstance(first, str):
        return [
            ChunkRecord(text=t, metadata=dict(default_metadata)) for t in result
        ]
    return list(result)


# ---------------------------------------------------------------------------
# B3 / D-22 — progress-bar gate
# ---------------------------------------------------------------------------


def _progress_enabled() -> bool:
    """Skip the progress bar in CI / NO_COLOR / non-terminal contexts.

    Smoke tests pin NO_COLOR=1 + TERM=dumb (AGENTS.md) and we never want
    Rich escape sequences in their captured output.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if not console.is_terminal:
        return False
    return True


def _make_progress() -> Progress:
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("· {task.fields[chunks]} chunks"),
        TimeElapsedColumn(),
        console=console,
    )


# ---------------------------------------------------------------------------
# Per-source dispatch
# ---------------------------------------------------------------------------


def _dispatch_chunks(path: Path, body: str) -> list[ChunkRecord]:
    """Route a single source to its chunker and normalize the result."""
    suffix = path.suffix
    if suffix == ".jsonl":
        return chunk_transcript(body, source_path=path)
    raw = chunk_markdown(body)
    return _normalize_chunks(raw, default_metadata={"chunker": "markdown_header"})


def _filter_transcript_dedupe(
    records: list[ChunkRecord], manifest: Manifest
) -> list[ChunkRecord]:
    """Per-message dedupe gate (D-25, D-27).

    W4: a record missing ``session_id`` or ``user_uuid`` indicates an
    upstream parser bug and raises ValueError — NOT silently skipped.
    """
    new_records: list[ChunkRecord] = []
    for rec in records:
        tmeta = rec.metadata.get("transcript", {}) or {}
        session_id = tmeta.get("session_id", "")
        msg_uuid = tmeta.get("user_uuid", "")
        if not session_id or not msg_uuid:
            raise ValueError(
                f"Transcript chunk missing session_id or message_uuid: "
                f"{rec.metadata!r}"
            )
        content_hash = hashlib.sha256(rec.text.encode("utf-8")).hexdigest()
        if manifest.transcript_needs_index(session_id, msg_uuid, content_hash):
            new_records.append(rec)
            manifest.transcript_update(session_id, msg_uuid, content_hash)
    return new_records


def _index_records(
    *,
    client: Any,
    dense: Any,
    sparse: Any,
    path: Path,
    records: list[ChunkRecord],
    sha: str,
    collection: str,
    is_transcript: bool,
) -> int:
    """Embed ``records`` and upsert hybrid points. Returns chunks written."""
    from qdrant_client.http import models as qmodels

    if not records:
        return 0
    abs_path = str(path.resolve())
    points: list[Any] = []
    for idx, rec in enumerate(records):
        if _token_count(rec.text) < CHUNK_MIN_TOKENS and not is_transcript:
            # Transcript drawers can be short (single Q+A); markdown chunks
            # below CHUNK_MIN_TOKENS are dropped per the legacy contract.
            continue
        dense_vec = [float(x) for x in next(dense.embed([rec.text]))]
        sparse_vec = next(sparse.embed([rec.text]))
        sparse_obj = qmodels.SparseVector(
            indices=[int(i) for i in sparse_vec.indices],
            values=[float(v) for v in sparse_vec.values],
        )

        if is_transcript:
            tmeta = rec.metadata.get("transcript", {}) or {}
            session_id = tmeta.get("session_id", "")
            user_uuid = tmeta.get("user_uuid", "")
            # Use turn_index (per-pair) so chunk_id is stable across re-runs
            # regardless of how many pairs were filtered by per-message dedupe.
            turn_index = int(tmeta.get("turn_index", idx))
            point_id = _transcript_chunk_id(session_id, user_uuid, turn_index)
            content_hash = hashlib.sha256(rec.text.encode("utf-8")).hexdigest()
        else:
            point_id = _chunk_id(abs_path, idx)
            content_hash = sha

        payload = {
            "file_path": abs_path,
            "chunk_idx": idx,
            "content_hash": content_hash,
            "document": rec.text,
            **rec.metadata,
        }
        points.append(
            qmodels.PointStruct(
                id=point_id,
                vector={DENSE_VECTOR: dense_vec, SPARSE_VECTOR: sparse_obj},
                payload=payload,
            )
        )
    if not points:
        return 0
    client.upsert(collection_name=collection, points=points)
    return len(points)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _process_one_source(
    *,
    path: Path,
    target: Target,
    force: bool,
    manifest: Manifest,
    client: Any,
    dense: Any,
    sparse: Any,
    collection: str,
) -> tuple[int, int]:
    """Process a single source path. Returns ``(chunks_written, failed)``.

    Failures inside the per-source try are counted but never raised;
    the W4 ValueError path is intentionally NOT caught (programming bug).
    """
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, 1

    sha = _compute_hash(body)
    abs_path = str(path.resolve())
    is_transcript = path.suffix == ".jsonl"

    # File-level dedupe still applies for markdown.
    if not is_transcript and not force and not manifest.needs_index(
        abs_path, sha, target
    ):
        return 0, 0

    try:
        records = _dispatch_chunks(path, body)
    except ValueError:
        # Chunker validation errors (e.g., D-16 nested fences) — surface.
        raise
    except Exception:  # noqa: BLE001 — chunker should not crash the run
        return 0, 1

    if is_transcript:
        # Per-message dedupe (D-25). force=True bypasses the manifest gate
        # so users can re-embed an entire session deliberately.
        if force:
            for rec in records:
                tmeta = rec.metadata.get("transcript", {}) or {}
                session_id = tmeta.get("session_id", "")
                msg_uuid = tmeta.get("user_uuid", "")
                if not session_id or not msg_uuid:
                    raise ValueError(
                        f"Transcript chunk missing session_id or message_uuid: "
                        f"{rec.metadata!r}"
                    )
                content_hash = hashlib.sha256(rec.text.encode("utf-8")).hexdigest()
                manifest.transcript_update(session_id, msg_uuid, content_hash)
        else:
            records = _filter_transcript_dedupe(records, manifest)

    try:
        n = _index_records(
            client=client,
            dense=dense,
            sparse=sparse,
            path=path,
            records=records,
            sha=sha,
            collection=collection,
            is_transcript=is_transcript,
        )
        if n > 0 and not is_transcript:
            manifest.update(abs_path, target, sha)
        return n, 0
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 — keep going; one bad doc shouldn't kill the run
        return 0, 1


def run_index(
    *,
    target: Target = "tuned",
    force: bool = False,
    sources: list[str] | None = None,
    config: ResolvedConfig | None = None,
) -> int:
    """Embed Markdown + JSONL sources into Qdrant. Fail-soft: returns 0 on any external failure."""
    cfg = config or ResolvedConfig()
    src_list = sources if sources is not None else cfg.sources or []
    files = _expand_sources(src_list)
    if not files:
        return 0

    # Fail-soft: Qdrant unreachable or qdrant-client missing.
    try:
        if QdrantClient is None:
            return 0
        client = QdrantClient(url=cfg.qdrant_url, timeout=60)
        client.get_collections()
    except Exception:  # noqa: BLE001 — fail-soft per plan acceptance criterion
        return 0

    try:
        dense = build_dense_embedder()
        sparse = build_sparse_embedder()
    except Exception:  # noqa: BLE001 — embedders may pull heavy deps
        return 0

    manifest_path = _manifest_path(cfg)
    manifest = Manifest.load(manifest_path)

    written = 0
    failed = 0
    chunks_emitted = 0

    if _progress_enabled():
        with _make_progress() as progress:
            task = progress.add_task("indexing", total=len(files), chunks=0)
            for path in files:
                n, fail = _process_one_source(
                    path=path,
                    target=target,
                    force=force,
                    manifest=manifest,
                    client=client,
                    dense=dense,
                    sparse=sparse,
                    collection=cfg.collection,
                )
                written += n
                failed += fail
                chunks_emitted += n
                progress.update(task, advance=1, chunks=chunks_emitted)
    else:
        for path in files:
            n, fail = _process_one_source(
                path=path,
                target=target,
                force=force,
                manifest=manifest,
                client=client,
                dense=dense,
                sparse=sparse,
                collection=cfg.collection,
            )
            written += n
            failed += fail
            chunks_emitted += n

    try:
        manifest.save(manifest_path)
    except OSError:
        pass

    return 0 if failed == 0 else 0  # fail-soft contract — never propagate
