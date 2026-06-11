"""Bench-only LongMemEval haystack ingest (Phase 14 Plan A, Task A2).

Reachable ONLY from :mod:`supamem.eval.runner` when ``suite='longmemeval_s'``.
Production indexer paths (``supamem.indexer.*``, ``supamem.chunker``) are
NOT touched — this module is the eval-only writer for an isolated bench
collection (D-SCOPE-05).

Pipeline:

1. Resolve the bench collection name via :func:`eval_collection_name`
   (reserved ``supamem_eval_*`` prefix per RESEARCH risk #3).
2. Build dense + sparse embedders via the SHARED utility helpers
   (:func:`build_dense_embedder`, :func:`build_sparse_embedder`) — these
   are NOT production-indexer-only; they live under
   :mod:`supamem.embedders` and are reused as a library, not as part of
   ``run_index``.
3. Create the bench collection with vector params matching production
   (``dense`` 384-d cosine + ``sparse`` BM25 IDF modifier).
4. Create an idempotent ``session_id`` keyword payload index
   (``KeywordIndexParams(type='keyword', on_disk=True)``) per D-SCOPE-04,
   mirroring the ``path_prefixes`` precedent at
   ``src/supamem/indexer/__init__.py:469-474``.
5. Iterate :func:`supamem.eval.datasets.longmemeval_loader.iter_haystack_chunks`,
   embed each (role, content) turn, build a Qdrant ``PointStruct`` with
   ``payload = {"session_id": <id>, "text": <text>, "axis": <axis>}``,
   and upsert in batches.

Per CLAUDE.md hard constraints: NEVER bare ``print`` (use :mod:`supamem.console`),
NEVER suppress errors in retrieval/indexing paths (surface via
``err_console`` and re-raise where appropriate). The single sanctioned
defensive ``except`` here is the idempotent-DDL guard for
``create_payload_index`` — Qdrant's contract is no-op-if-matching but we
catch + log + continue for resilience against future API changes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from supamem.config import ResolvedConfig
from supamem.console import err_console
from supamem.embedders import build_dense_embedder, build_sparse_embedder
from supamem.eval.datasets.longmemeval_loader import iter_haystack_chunks
from supamem.qdrant_collection import ensure_collection as _ensure_collection

EVAL_COLLECTION_PREFIX = "supamem_eval_"

# Vector schema mirrors production (init.py:74-87 + tuned_hybrid.py:43-44).
# Pinning these here — rather than re-importing from supamem.indexer or
# supamem.init — keeps the bench-only path independent of production code,
# satisfying the D-SCOPE-02 invariant ("zero modification to production
# indexer paths").
_DENSE_VECTOR_NAME = "dense"
_SPARSE_VECTOR_NAME = "sparse"
_DEFAULT_VECTOR_SIZE = 384
_UPSERT_BATCH = 64


def eval_collection_name(cfg: ResolvedConfig, suite: str) -> str:
    """Reserved-prefix scheme: ``supamem_eval_<suite>``.

    The ``cfg`` argument is reserved for future per-project overrides
    (e.g. ``supamem_eval_<cfg.collection>_<suite>`` for users who run
    multiple bench projects against one Qdrant instance) but is unused
    today — the prefix-only scheme keeps RESEARCH risk #3
    (collection-name collisions with production data) trivially mitigated.
    """
    del cfg  # reserved for future per-project namespacing
    return f"{EVAL_COLLECTION_PREFIX}{suite}"


def _ensure_session_id_index(client: QdrantClient, coll: str) -> None:
    """Create the ``session_id`` keyword payload index (D-SCOPE-04, idempotent).

    Defensive ``except`` is sanctioned here — Qdrant's
    ``create_payload_index`` contract is no-op-if-matching, but on a
    matching-schema collision some server versions raise. We log + continue
    so a re-run of the bench ingest never aborts on a duplicate index.
    """
    try:
        client.create_payload_index(
            collection_name=coll,
            field_name="session_id",
            field_schema=qmodels.KeywordIndexParams(type="keyword", on_disk=True),
        )
    except Exception as exc:  # noqa: BLE001 — idempotent DDL guard
        err_console.print(
            f"[supamem.warn]bench-ingest: session_id payload index "
            f"create skipped ({type(exc).__name__}: {exc})[/supamem.warn]"
        )


def _flush(client: QdrantClient, coll: str, points: list[Any]) -> None:
    if not points:
        return
    client.upsert(collection_name=coll, points=points)


def ingest(
    cfg: ResolvedConfig,
    records: Iterable[Mapping[str, Any]],
    *,
    client: QdrantClient | None = None,
    suite: str = "longmemeval_s",
) -> int:
    """Bootstrap the bench collection + session_id index, embed + upsert.

    Returns the count of upserted points (one per haystack turn).

    The caller's ``cfg`` is NOT mutated — we shallow-copy via
    :func:`dataclasses.replace` so the override is local to this call.
    The override is not strictly required here (this module always uses
    ``bench_coll`` directly), but keeping the local copy in lockstep
    documents the contract for symmetry with the runner's
    ``_build_backend`` swap (Task A3).

    Raises on hard Qdrant connection / RPC failures (NEVER suppress per
    CLAUDE.md). The single sanctioned ``except`` is the idempotent-DDL
    guard inside :func:`_ensure_session_id_index`.
    """
    bench_coll = eval_collection_name(cfg, suite)
    # Shallow override — caller's cfg is untouched.
    _ = replace(cfg, collection=bench_coll)

    if client is None:
        client = QdrantClient(
            url=cfg.qdrant_url,
            api_key=cfg.qdrant_api_key or None,
            check_compatibility=False,
            timeout=30,
        )

    _ensure_collection(client, bench_coll)
    _ensure_session_id_index(client, bench_coll)

    dense = build_dense_embedder()
    sparse = build_sparse_embedder()

    upserted = 0
    batch: list[Any] = []
    point_id = 0

    # Materialize haystack tuples so we can batch embed-by-text-batch.
    chunks = list(iter_haystack_chunks(records))
    if not chunks:
        return 0

    texts = [text for (_sid, text, _axis) in chunks]
    # Embed in a single pass (the embedder internally batches).
    dense_iter = iter(dense.embed(texts))
    sparse_iter = iter(sparse.embed(texts))

    for (sid, text, axis), dvec, svec in zip(chunks, dense_iter, sparse_iter):
        dense_vec = [float(x) for x in dvec]
        # NOTE: fastembed returns numpy arrays for `indices`/`values`. The
        # `array or default` truthiness pattern raises "truth value of an
        # array with more than one element is ambiguous" — use explicit
        # None checks instead.
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
                # Production retrieval (`tuned_hybrid.py`) reads chunk text
                # from `payload["document"]`. Using "text" here makes
                # retrieved chunks have an empty `.text` attribute — the
                # bench scoped/unscoped passes then measure nothing
                # meaningful. Match the production contract.
                "session_id": sid,
                "document": text,
                "axis": axis,
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
    "EVAL_COLLECTION_PREFIX",
    "eval_collection_name",
    "ingest",
]
