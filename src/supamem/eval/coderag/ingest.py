"""Bench-only coderag ingest.

Mirrors :mod:`supamem.eval.longmemeval_ingest` shape and isolation rules.

D-SCOPE-05 carry-lock (Phase 14 → Phase 15): the ONLY allowed import from
``supamem.indexer.*`` is ``chunk_markdown`` (read-only library use). Any
other ``supamem.indexer.*`` import is a regression and the AST-walk test
``test_ingest_body_does_not_import_supamem_indexer_except_chunker`` will
fail.

Plan 15-A: collection-name constants + idempotent payload-index DDL.
Plan 15-B (Task B3): wire the corpus walk → chunker → embedder → upsert
flow. Records arrive as ``[{repo_slug, repo_root}, ...]``; output is the
count of upserted Qdrant points.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from supamem.console import err_console
from supamem.embedders import build_dense_embedder, build_sparse_embedder
from supamem.eval.coderag.corpus import walk_corpus

# READ-ONLY library use of the markdown chunker — the SINGLE allowed
# ``supamem.indexer.*`` import in this module. Any addition to this import
# section that targets ``supamem.indexer.<other>`` violates D-SCOPE-05.
from supamem.indexer.chunker import chunk_markdown

EVAL_COLLECTION_PREFIX = "supamem_eval_"
CODERAG_COLLECTION = f"{EVAL_COLLECTION_PREFIX}coderag"

# Two keyword-typed payload indexes back the Phase 11 ``where`` filter for
# three-column reporting (supamem_only / fastapi_only / combined) and the
# rationale/code-fact axis breakdown.
CODERAG_PAYLOAD_INDEX_FIELDS: tuple[str, ...] = ("repo", "axis")

# Vector schema mirrors production (matches longmemeval_ingest exactly).
_DENSE_VECTOR_NAME = "dense"
_SPARSE_VECTOR_NAME = "sparse"
_DEFAULT_VECTOR_SIZE = 384
_UPSERT_BATCH = 64


def coderag_collection_name() -> str:
    """Stable bench-collection name. The eval-isolated bench collection is
    intentionally pinned; CLAUDE.md's "never hardcode collection names" rule
    applies to PRODUCTION ``cfg.collection``, not to the bench namespace."""
    return CODERAG_COLLECTION


def _ensure_payload_index(client: QdrantClient, coll: str, field: str) -> None:
    """Idempotent payload-index DDL — Phase 14 D-SCOPE-04 precedent."""
    try:
        client.create_payload_index(
            collection_name=coll,
            field_name=field,
            field_schema=qmodels.KeywordIndexParams(type="keyword", on_disk=True),
        )
    except Exception as exc:  # noqa: BLE001 — sanctioned idempotent guard
        err_console.print(
            f"[supamem.warn]coderag-ingest: {field} index create skipped "
            f"({type(exc).__name__})[/supamem.warn]"
        )


def ensure_indexes(client: QdrantClient, coll: str | None = None) -> None:
    """Create the ``repo`` and ``axis`` keyword payload-indexes idempotently."""
    target = coll or coderag_collection_name()
    for field in CODERAG_PAYLOAD_INDEX_FIELDS:
        _ensure_payload_index(client, target, field)


def _ensure_collection(client: QdrantClient, coll: str) -> None:
    """Create the bench collection with production vector params, idempotently."""
    try:
        existing = {c.name for c in client.get_collections().collections}
    except Exception as exc:  # noqa: BLE001 — surface via err_console
        err_console.print(
            f"[supamem.warn]coderag-ingest: get_collections failed "
            f"({type(exc).__name__}: {exc}); attempting create_collection anyway"
            "[/supamem.warn]"
        )
        existing = set()

    if coll in existing:
        return

    client.create_collection(
        collection_name=coll,
        vectors_config={
            _DENSE_VECTOR_NAME: qmodels.VectorParams(
                size=_DEFAULT_VECTOR_SIZE,
                distance=qmodels.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            _SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                modifier=qmodels.Modifier.IDF,
            ),
        },
    )


def _detect_axis(rel_path: str) -> str:
    """Per-file axis classifier: ADRs → decision_rationale; everything else
    → code_fact. Stable per-file decision drives the three-column metric
    reporting later (D-HAY-02)."""
    if rel_path.startswith("docs/adr/") and rel_path.endswith(".md"):
        return "decision_rationale"
    return "code_fact"


def _flush(client: QdrantClient, coll: str, points: list[Any]) -> None:
    if not points:
        return
    client.upsert(collection_name=coll, points=points)


def ingest(
    cfg: Any,
    records: Iterable[Mapping[str, Any]],
    *,
    client: QdrantClient | None = None,
) -> int:
    """Walk pinned-SHA caches → chunk → embed → upsert.

    ``records``: iterable of ``{"repo_slug": str, "repo_root": Path}``.

    Returns total upserted point count.

    Raises on hard Qdrant connection / RPC failures (NEVER suppress per
    CLAUDE.md). The single sanctioned ``except`` is the idempotent-DDL
    guard inside :func:`_ensure_payload_index` and :func:`_ensure_collection`.
    """
    bench_coll = coderag_collection_name()

    if client is None:
        client = QdrantClient(
            url=getattr(cfg, "qdrant_url", "http://localhost:6333"),
            api_key=getattr(cfg, "qdrant_api_key", None) or None,
            check_compatibility=False,
            timeout=30,
        )

    _ensure_collection(client, bench_coll)
    ensure_indexes(client, bench_coll)

    dense = build_dense_embedder()
    sparse = build_sparse_embedder()

    upserted = 0
    point_id = 0
    batch: list[Any] = []

    for record in records:
        repo_slug = record["repo_slug"]
        repo_root = record["repo_root"]

        # Step 1: walk the corpus (allowlist + exclude per D-HAY-04 / D-QGEN-03).
        # Step 2: per-file chunk → (rel_path, axis, chunk_text) triples.
        triples: list[tuple[str, str, str]] = []
        for path in walk_corpus(repo_root):
            rel = path.relative_to(repo_root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                err_console.print(
                    f"[supamem.warn]coderag-ingest: skip non-utf8 file "
                    f"{repo_slug}:{rel}[/supamem.warn]"
                )
                continue
            chunks = chunk_markdown(text) or [text]
            axis = _detect_axis(rel)
            for chunk in chunks:
                if chunk.strip():
                    triples.append((rel, axis, chunk))

        if not triples:
            continue

        # Step 3: embed in a single batched pass (the embedder batches internally).
        texts = [t[2] for t in triples]
        dense_iter = iter(dense.embed(texts))
        sparse_iter = iter(sparse.embed(texts))

        for (rel, axis, chunk_text), dvec, svec in zip(
            triples, dense_iter, sparse_iter
        ):
            dense_vec = [float(x) for x in dvec]
            _idx = getattr(svec, "indices", None)
            _val = getattr(svec, "values", None)
            sparse_vec = qmodels.SparseVector(
                indices=[int(i) for i in (_idx if _idx is not None else [])],
                values=[float(v) for v in (_val if _val is not None else [])],
            )
            point = qmodels.PointStruct(
                id=point_id,
                vector={
                    _DENSE_VECTOR_NAME: dense_vec,
                    _SPARSE_VECTOR_NAME: sparse_vec,
                },
                payload={
                    "repo": repo_slug,
                    "axis": axis,
                    "document": chunk_text,
                    "file_path": rel,
                    "doc_id": rel,
                },
            )
            batch.append(point)
            point_id += 1
            if len(batch) >= _UPSERT_BATCH:
                _flush(client, bench_coll, batch)
                upserted += len(batch)
                batch = []

    if batch:
        _flush(client, bench_coll, batch)
        upserted += len(batch)

    return upserted


__all__ = [
    "CODERAG_COLLECTION",
    "CODERAG_PAYLOAD_INDEX_FIELDS",
    "EVAL_COLLECTION_PREFIX",
    "coderag_collection_name",
    "ensure_indexes",
    "ingest",
]
