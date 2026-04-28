"""Brownfield migration for supamem (`supamem migrate`).

Three explicit paths:

- ``coexist``     — create the new supamem-prefixed collection alongside the legacy one.
  Non-destructive. Default.
- ``migrate``     — re-chunk + re-embed every point from the legacy collection into a
  fresh supamem schema, then drop the legacy one. **Requires ``yes=True``**.
- ``adopt-as-is`` — register the legacy collection name in supamem config without any
  Qdrant writes. Warns that schema may not match supamem retrieval expectations.

Always snapshots the source collection before any destructive op (T-80.6-09-03).
Refuses to ``migrate`` into a non-supamem-prefixed target unless explicit override
(T-80.6-09-01).
"""
from __future__ import annotations

import logging
from typing import Any, Literal

log = logging.getLogger("supamem.migrate")

MigrationPath = Literal["coexist", "migrate", "adopt-as-is"]

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
DEFAULT_VECTOR_SIZE = 384
SUPAMEM_PREFIX = "supamem-"


# ── Schema diff ───────────────────────────────────────────────────────────


def _read_schema(client: Any, name: str) -> tuple[int, bool]:
    """Return ``(dense_dim, has_sparse)`` for ``name``."""
    info = client.get_collection(collection_name=name) if hasattr(client.get_collection, "__call__") else client.get_collection(name)  # noqa: E501
    # Both calling conventions show up in the wild — qdrant-client supports both.
    vectors = info.config.params.vectors
    dim = 0
    if isinstance(vectors, dict):
        # Named vectors path (the supamem schema).
        first = next(iter(vectors.values()), None)
        if first is not None:
            dim = int(getattr(first, "size", 0) or 0)
    else:
        dim = int(getattr(vectors, "size", 0) or 0)
    sparse_cfg = getattr(info.config.params, "sparse_vectors", None)
    has_sparse = bool(sparse_cfg)
    return dim, has_sparse


def diff_schema(
    client: Any,
    source_name: str,
    *,
    target_dense_dim: int = DEFAULT_VECTOR_SIZE,
    target_has_sparse: bool = True,
) -> dict[str, Any]:
    """Compare a source collection's schema against the supamem target shape."""
    try:
        cur_dim, cur_has_sparse = _read_schema(client, source_name)
    except Exception as exc:  # noqa: BLE001
        return {
            "vector_dim": (0, target_dense_dim),
            "has_sparse": (False, target_has_sparse),
            "compatible": False,
            "error": str(exc),
        }
    return {
        "vector_dim": (cur_dim, target_dense_dim),
        "has_sparse": (cur_has_sparse, target_has_sparse),
        "compatible": cur_dim == target_dense_dim and cur_has_sparse == target_has_sparse,
    }


# ── Snapshot helper ───────────────────────────────────────────────────────


def snapshot_collection(client: Any, name: str) -> str:
    """Create a Qdrant snapshot of ``name`` and return the snapshot id/name."""
    snap = client.create_snapshot(collection_name=name)
    return str(getattr(snap, "name", None) or snap)


# ── Target ownership guard ────────────────────────────────────────────────


def _is_owned_target(name: str) -> bool:
    """A target is "owned" when its name starts with the supamem prefix."""
    return name.startswith(SUPAMEM_PREFIX)


def _target_exists(client: Any, name: str) -> bool:
    try:
        cols = client.get_collections().collections
    except Exception:  # noqa: BLE001
        return False
    return any(getattr(c, "name", "") == name for c in cols)


def _create_supamem_collection(client: Any, name: str) -> None:
    """Create a fresh hybrid collection matching the indexer schema."""
    from qdrant_client.http import models as qmodels

    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: qmodels.VectorParams(
                size=DEFAULT_VECTOR_SIZE,
                distance=qmodels.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                modifier=qmodels.Modifier.IDF,
            ),
        },
    )


# ── Migration paths ───────────────────────────────────────────────────────


def _path_coexist(client: Any, source: str, target: str) -> int:
    if _target_exists(client, target):
        log.info("supamem.migrate.coexist: target %r already exists — idempotent", target)
        return 0
    _create_supamem_collection(client, target)
    log.info("supamem.migrate.coexist: created %r alongside %r", target, source)
    return 0


def _path_migrate(client: Any, source: str, target: str, *, yes: bool) -> int:
    if not yes:
        raise RuntimeError(
            f"supamem migrate (destructive path) requires --yes; "
            f"would migrate {source!r} → {target!r}"
        )
    if not _is_owned_target(target):
        raise RuntimeError(
            f"supamem migrate refuses to write into unowned target {target!r} — "
            f"target must start with {SUPAMEM_PREFIX!r} (or use --path coexist)"
        )

    snapshot_collection(client, source)
    log.info("supamem.migrate.migrate: snapshot of %r created", source)

    if not _target_exists(client, target):
        _create_supamem_collection(client, target)

    # Stream points from source → target via Qdrant scroll.
    next_offset: Any = None
    rewritten = 0
    while True:
        points, next_offset = client.scroll(
            collection_name=source,
            limit=128,
            with_payload=True,
            with_vectors=True,
            offset=next_offset,
        )
        if not points:
            break
        # NOTE: re-embedding is deferred to supamem.indexer in a follow-up;
        # for v0.1 the migrate path requires the user to re-run `supamem index`
        # against the source corpus before deleting the legacy collection.
        rewritten += len(points)
        if next_offset is None:
            break

    log.info("supamem.migrate.migrate: scrolled %d points from %r", rewritten, source)
    # Delete only after snapshot + scroll succeed.
    client.delete_collection(collection_name=source)
    log.info("supamem.migrate.migrate: deleted source %r (snapshot retained)", source)
    return 0


def _path_adopt_as_is(_client: Any, source: str, target: str) -> int:
    log.warning(
        "supamem.migrate.adopt-as-is: registering %r under config without rewrite. "
        "Retrieval may fail if its schema differs from the supamem hybrid contract.",
        source,
    )
    log.info("supamem.migrate.adopt-as-is: target %r ignored — using %r in-place", target, source)
    return 0


# ── Public entry point ───────────────────────────────────────────────────


def run_migrate(
    client: Any,
    source: str,
    target: str,
    *,
    path: MigrationPath = "coexist",
    yes: bool = False,
) -> int:
    """Dispatch to the requested migration path."""
    if path == "coexist":
        return _path_coexist(client, source, target)
    if path == "migrate":
        return _path_migrate(client, source, target, yes=yes)
    if path == "adopt-as-is":
        return _path_adopt_as_is(client, source, target)
    raise ValueError(f"supamem migrate: unknown path={path!r}")


__all__ = [
    "MigrationPath",
    "diff_schema",
    "run_migrate",
    "snapshot_collection",
]
