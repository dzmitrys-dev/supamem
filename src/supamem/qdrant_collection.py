"""Shared hybrid Qdrant collection lifecycle (dense cosine + sparse IDF).

Single source of truth for collection schema, write-time ensure, read-time
assert, and forbidden-collection write guard (Phase 18 D-B2 / D-A3e).
"""
from __future__ import annotations

from typing import Any

from qdrant_client.http.exceptions import UnexpectedResponse

from supamem.config import ResolvedConfig
from supamem.console import err_console

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
DEFAULT_VECTOR_SIZE = 384

_FORBIDDEN_COLLECTIONS = frozenset({"dev_memory", "dev_memory_tuned"})


class CollectionMissingError(RuntimeError):
    """Raised when a read path targets a collection that does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"collection {name!r} does not exist — run `supamem index` or `supamem init`"
        )


def hybrid_vectors_config() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (vectors_config, sparse_vectors_config) for hybrid create_collection."""
    from qdrant_client.http import models as qmodels

    vectors_config = {
        DENSE_VECTOR_NAME: qmodels.VectorParams(
            size=DEFAULT_VECTOR_SIZE,
            distance=qmodels.Distance.COSINE,
        ),
    }
    sparse_vectors_config = {
        SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
            modifier=qmodels.Modifier.IDF,
        ),
    }
    return vectors_config, sparse_vectors_config


def ensure_collection(client: Any, name: str) -> bool:
    """Create hybrid collection if missing.

    Idempotent: if the collection is already present we skip create_collection.
    We intentionally do NOT delete-and-recreate (force delete stays init-only).

    Returns True if created, False if it already existed.
    """
    try:
        existing = {c.name for c in client.get_collections().collections}
    except Exception as exc:  # noqa: BLE001 — surface via err_console
        err_console.print(
            f"[supamem.warn]qdrant-collection: get_collections failed "
            f"({type(exc).__name__}: {exc}); attempting create_collection anyway"
            "[/supamem.warn]"
        )
        existing = set()

    if name in existing:
        return False

    vectors_config, sparse_vectors_config = hybrid_vectors_config()
    client.create_collection(
        collection_name=name,
        vectors_config=vectors_config,
        sparse_vectors_config=sparse_vectors_config,
    )
    return True


def assert_collection_exists_for_read(client: Any, name: str) -> None:
    """Raise CollectionMissingError on 404; re-raise other Qdrant HTTP errors."""
    try:
        client.get_collection(name)
    except UnexpectedResponse as exc:
        if exc.status_code == 404:
            raise CollectionMissingError(name) from exc
        raise


def validate_writable_collection(cfg: ResolvedConfig) -> None:
    """Block writes to legacy reserved collection names unless explicitly opted in."""
    if (
        cfg.collection in _FORBIDDEN_COLLECTIONS
        and not getattr(cfg, "allow_legacy_collection", False)
    ):
        raise RuntimeError(
            f"supamem: collection={cfg.collection!r} is forbidden — "
            "this name is reserved for legacy production paths. Pick a "
            "different collection or set "
            "ResolvedConfig.allow_legacy_collection=True to opt in."
        )
