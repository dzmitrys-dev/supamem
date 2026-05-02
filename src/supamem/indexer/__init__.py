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
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from supamem.config import ResolvedConfig
from supamem.console import console, err_console
from supamem.embedders import build_dense_embedder, build_sparse_embedder
from supamem.indexer.chunker import CHUNK_MIN_TOKENS, _token_count, chunk_markdown
from supamem.indexer.classifier import classify_room
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


def _classifier_hash(rooms: dict[str, list[str]]) -> str:
    """sha256 of ``[classifier.rooms]`` config — order-sensitive (D-01a + D-08).

    Uses ``sort_keys=False`` (the default) so dict insertion order is part of
    the digest. Insertion order encodes classifier priority (first-match-wins
    per D-01a); reordering rooms in the TOML config changes classification
    outcomes, so it MUST trip the sweep gate. Do NOT add ``sort_keys=True``
    here — that would couple the gate to keyword-set drift only and silently
    miss priority drift (07-01-SUMMARY note + python-hashing insight).
    """
    return hashlib.sha256(json.dumps(rooms).encode("utf-8")).hexdigest()


def _reclassify_sweep(client: Any, cfg: ResolvedConfig, *, batch: int = 512) -> int:
    """Re-classify all points on classifier hash drift (D-08, D-09, R-04).

    Scrolls the collection in batches of ``batch`` points, recomputes ``room``
    per ``file_path`` via :func:`classify_room`, groups updates by new_room,
    and issues ONE :meth:`set_payload` per group (NOT one per point — Phase 7
    D-09 batching invariant). Skips points whose room is already correct.
    Uses ``wait=True`` for idempotency under interruption (RESEARCH R-03).

    Returns the number of points whose payload was updated.
    """
    offset: Any = None
    updated = 0
    while True:
        points, offset = client.scroll(
            collection_name=cfg.collection,
            limit=batch,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        by_room: dict[Optional[str], list[Any]] = {}
        for p in points:
            payload = p.payload or {}
            file_path = payload.get("file_path")
            if not file_path:
                continue
            new_room = classify_room(file_path, cfg.classifier_rooms)
            old_room = payload.get("room", "__missing__")
            if new_room != old_room:
                by_room.setdefault(new_room, []).append(p.id)
        for room, ids in by_room.items():
            client.set_payload(
                collection_name=cfg.collection,
                payload={"room": room},
                points=ids,
                wait=True,
            )
            updated += len(ids)
        if offset is None:
            break
    return updated


# ---------------------------------------------------------------------------
# Phase 9 — temporal validity helpers (D-WINDOW-01, D-NULL-03, D-GC-01,
# D-INDEX-01..02). Each helper mirrors :func:`_reclassify_sweep` shape:
# scroll → group → ONE batched RPC per group (Phase 7 D-09 batching invariant).
# ``wait=True`` for idempotency under interruption (RESEARCH §R-3).
# ---------------------------------------------------------------------------


def _close_validity_window(
    client: Any,
    cfg: ResolvedConfig,
    file_path: str,
    *,
    batch: int = 512,
) -> int:
    """Close the validity window on currently-live chunks of ``file_path``.

    D-WINDOW-01: scroll all points where ``file_path == path AND
    IsEmpty(valid_to)`` (so already-closed chunks are skipped — idempotent),
    then issue ONE batched ``set_payload({"valid_to": now_iso})`` per scroll
    page. Re-raise on RPC failure: TEMP-01 contract requires atomic
    close-then-upsert per file (Threat T-09-03-05).

    Returns the number of points whose validity was closed.
    """
    from qdrant_client.http import models as qmodels  # noqa: PLC0415

    now_iso = datetime.now(timezone.utc).isoformat()
    scroll_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="file_path", match=qmodels.MatchValue(value=file_path)
            ),
            qmodels.IsEmptyCondition(
                is_empty=qmodels.PayloadField(key="valid_to")
            ),
        ]
    )
    offset: Any = None
    closed = 0
    while True:
        points, offset = client.scroll(
            collection_name=cfg.collection,
            scroll_filter=scroll_filter,
            limit=batch,
            with_payload=False,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        ids = [p.id for p in points]
        client.set_payload(
            collection_name=cfg.collection,
            payload={"valid_to": now_iso},
            points=ids,
            wait=True,
        )
        closed += len(ids)
        if offset is None:
            break
    return closed


def _eager_validity_migration(
    client: Any,
    cfg: ResolvedConfig,
    *,
    batch: int = 512,
) -> int:
    """Back-fill ``valid_to = None`` on legacy Phase-8-era points.

    D-NULL-03 + Pitfall 7: scrolls every point where ``IsEmpty(valid_to)`` and
    issues ONE batched ``set_payload({"valid_to": None})`` per scroll page.
    The explicit-null marker makes legacy points eligible for the runtime
    IsEmpty match in :func:`build_qdrant_filter` (Phase-8 chunks would
    otherwise be silently filtered out by the always-on temporal clause).

    Gated externally by ``manifest.validity_migration is None``; called
    exactly ONCE per upgrade, BEFORE the per-file close-old sweep so
    legacy points are not consumed by close-window's IsEmpty filter.

    Returns the number of points back-filled.
    """
    from qdrant_client.http import models as qmodels  # noqa: PLC0415

    scroll_filter = qmodels.Filter(
        must=[
            qmodels.IsEmptyCondition(
                is_empty=qmodels.PayloadField(key="valid_to")
            )
        ]
    )
    offset: Any = None
    migrated = 0
    while True:
        points, offset = client.scroll(
            collection_name=cfg.collection,
            scroll_filter=scroll_filter,
            limit=batch,
            with_payload=False,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        ids = [p.id for p in points]
        client.set_payload(
            collection_name=cfg.collection,
            payload={"valid_to": None},
            points=ids,
            wait=True,
        )
        migrated += len(ids)
        if offset is None:
            break
    return migrated


def _gc_sweep(
    client: Any,
    cfg: ResolvedConfig,
    retention_days: int,
    *,
    batch: int = 512,
) -> int:
    """Delete chunks superseded longer than ``retention_days``.

    D-GC-01: scrolls points where ``valid_to < (now - retention_days)`` and
    issues batched ``client.delete(points_selector=PointIdsList(points=ids))``
    calls (Form A — keeps count visible for doctor + Welford telemetry per
    RESEARCH §R-5; Form B ``delete(filter=...)`` is rejected because it
    hides the count).

    ``retention_days <= 0`` is the kept-forever escape hatch — returns 0
    and never calls ``client.scroll`` (Threat T-09-03-02 mitigation).

    Returns the number of points deleted.
    """
    if retention_days <= 0:
        return 0
    from datetime import timedelta  # noqa: PLC0415
    from qdrant_client.http import models as qmodels  # noqa: PLC0415

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).isoformat()
    scroll_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="valid_to",
                range=qmodels.DatetimeRange(lt=cutoff_iso),
            )
        ]
    )
    offset: Any = None
    deleted = 0
    while True:
        points, offset = client.scroll(
            collection_name=cfg.collection,
            scroll_filter=scroll_filter,
            limit=batch,
            with_payload=False,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        ids = [p.id for p in points]
        client.delete(
            collection_name=cfg.collection,
            points_selector=qmodels.PointIdsList(points=ids),
            wait=True,
        )
        deleted += len(ids)
        if offset is None:
            break
    return deleted


def _ensure_temporal_indexes(client: Any, cfg: ResolvedConfig) -> None:
    """Create payload indexes on ``valid_to`` (DATETIME) and ``chunker`` (KEYWORD).

    D-INDEX-01..02: idempotent — qdrant-client treats re-creation of the
    same-schema index as a no-op (RESEARCH §R-3). Without these, the
    always-on ``Range(gt=now)`` temporal clause and the per-source decay
    fan-out fall back to brute-force scans.

    Fail-soft + SURFACE per CLAUDE.md: errors go to ``err_console`` and
    indexing continues. The single sanctioned blanket except in the indexer
    is the update-check daemon; this RPC surface is critical enough to
    surface but not critical enough to abort the run.
    """
    from qdrant_client.http import models as qmodels  # noqa: PLC0415

    try:
        client.create_payload_index(
            collection_name=cfg.collection,
            field_name="valid_to",
            field_schema=qmodels.PayloadSchemaType.DATETIME,
            wait=True,
        )
        client.create_payload_index(
            collection_name=cfg.collection,
            field_name="chunker",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft + SURFACE per CLAUDE.md
        err_console.print(
            f"[red]temporal index creation failed: "
            f"{type(exc).__name__}: {exc}[/red]"
        )


def _chunk_id(file_path: str, idx: int, content_hash: str) -> str:
    """Deterministic chunk uuid keyed on (path, idx, content_hash) — Phase 9 D-CID-01.

    Identical content → identical uuid (idempotent re-index of unchanged
    content; Qdrant upsert is a no-op). CHANGED content → NEW uuid; the old
    point persists with ``valid_to`` set, the new point upserts with
    ``valid_to = None``. This is the literal TEMP-01 supersede mechanism.

    Transcript chunks use :func:`_transcript_chunk_id` (D-CID-02) — append-only
    by construction, content-independent.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{file_path}#chunk={idx}#hash={content_hash}",
        )
    )


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
    classifier_rooms: dict[str, list[str]],
    wallclock_fallback: Optional[list[int]] = None,
) -> int:
    """Embed ``records`` and upsert hybrid points. Returns chunks written.

    ``wallclock_fallback`` is a single-element mutable counter (list[int])
    so the per-record loop can increment it under Phase 9 D-VFROM-02 without
    a per-file warning storm. ``run_index`` initializes the counter and
    emits ONE end-of-run warning (Task 2b wires it).
    """
    from qdrant_client.http import models as qmodels

    if not records:
        return 0
    abs_path = str(path.resolve())
    # classify_room depends only on abs_path — lift it out of the per-chunk
    # loop. A chunker can still override via metadata["room"] because the
    # **rec.metadata spread happens AFTER "room" in the payload literal.
    default_room = classify_room(abs_path, classifier_rooms)
    # Phase 9 D-VFROM-01: derive valid_from once per file from mtime so the
    # per-chunk loop is cheap. Transcripts may carry a per-message timestamp
    # in rec.metadata['valid_from'] which overrides this default.
    try:
        mt = path.stat().st_mtime
        if mt <= 0:
            raise OSError("zero mtime")
        file_valid_from = datetime.fromtimestamp(mt, tz=timezone.utc).isoformat()
    except OSError:
        file_valid_from = datetime.now(timezone.utc).isoformat()
        if wallclock_fallback is not None:
            wallclock_fallback[0] += 1
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
            content_hash = sha
            point_id = _chunk_id(abs_path, idx, content_hash)

        # Phase 9 D-VFROM-01: transcripts may already expose a per-message
        # timestamp via rec.metadata['valid_from'] (Phase 6 owns the chunker).
        # Filesystem-derived chunks fall back to the per-file mtime computed
        # above. ``valid_to`` is None for live chunks; the close-window sweep
        # stamps it to ISO(now) when a file is re-indexed (D-WINDOW-01).
        rec_valid_from = rec.metadata.get("valid_from")
        valid_from = (
            rec_valid_from
            if isinstance(rec_valid_from, str) and rec_valid_from
            else file_valid_from
        )
        # D-06 + D-11 + D-13: payload.room is ALWAYS present (string or None),
        # positioned BEFORE **rec.metadata so a chunker can deliberately
        # override classification by setting metadata["room"]. v1 default
        # chunkers do NOT set this — the spread is just a forward-compat seam.
        payload = {
            "file_path": abs_path,
            "chunk_idx": idx,
            "content_hash": content_hash,
            "document": rec.text,
            "room": default_room,
            "valid_from": valid_from,
            "valid_to": None,
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
    classifier_rooms: dict[str, list[str]],
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
            classifier_rooms=classifier_rooms,
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

    # Plan 07-02 D-08 / D-09 / R-04: gate the classifier sweep on hash drift.
    # Pre-Phase-7 manifests have classifier_hash=None, which trips the gate
    # exactly once on the first post-upgrade run (set_payload only — no
    # re-embedding). Subsequent runs with stable config skip the scroll.
    current_classifier_hash = _classifier_hash(cfg.classifier_rooms)
    if manifest.classifier_hash != current_classifier_hash:
        err_console.print(
            "[supamem.brand]Classifier config changed — re-classifying chunks…"
            "[/supamem.brand]"
        )
        sweep_ok = False
        try:
            updated = _reclassify_sweep(client, cfg)
            sweep_ok = True
            err_console.print(
                f"  re-classified {updated} chunks (no re-embedding)"
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, but SURFACE
            err_console.print(
                f"[red]classifier sweep failed: "
                f"{type(exc).__name__}: {exc}[/red]"
            )
        if sweep_ok:
            # Only persist the hash on a fully-successful sweep; otherwise
            # leave the stale hash in place so the next run_index retries.
            manifest.classifier_hash = current_classifier_hash

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
                    classifier_rooms=cfg.classifier_rooms,
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
                classifier_rooms=cfg.classifier_rooms,
            )
            written += n
            failed += fail
            chunks_emitted += n

    try:
        manifest.save(manifest_path)
    except OSError:
        pass

    # fail-soft contract: any per-source failure was already counted into
    # `failed` and surfaced via err_console; the overall run still returns 0
    # so callers (hooks, CI) never break on a single bad doc.
    return 0
