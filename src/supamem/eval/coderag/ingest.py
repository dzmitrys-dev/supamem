"""Bench-only coderag ingest.

Mirrors :mod:`supamem.eval.longmemeval_ingest` shape and isolation rules.
NEVER imports any symbol from ``supamem.indexer.*`` (D-SCOPE-05 carry-lock —
Phase 14 → Phase 15). The shared embedder library API
(:mod:`supamem.embedders`) is fair game; the production indexer is not.

Plan 15-A scope: collection-name constants + idempotent payload-index DDL on
``repo`` and ``axis``. The corpus walk + embed + upsert flow lands in 15-B.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from supamem.console import err_console

EVAL_COLLECTION_PREFIX = "supamem_eval_"
CODERAG_COLLECTION = f"{EVAL_COLLECTION_PREFIX}coderag"

# Two keyword-typed payload indexes are required to back the Phase 11
# pass-through ``where`` filter for the three-column reporting axis
# (supamem_only / fastapi_only / combined) and the rationale/code-fact axis.
CODERAG_PAYLOAD_INDEX_FIELDS: tuple[str, ...] = ("repo", "axis")


def coderag_collection_name() -> str:
    """Stable bench-collection name. Hardcoded constant per CLAUDE.md
    ("never hardcode collection names" applies to PRODUCTION cfg.collection;
    the eval-isolated bench collection is intentionally pinned)."""
    return CODERAG_COLLECTION


def _ensure_payload_index(client: QdrantClient, coll: str, field: str) -> None:
    """Idempotent payload-index DDL — Phase 14 D-SCOPE-04 precedent.

    Qdrant's ``create_payload_index`` is documented as no-op-if-matching but we
    catch + log + continue to stay resilient against future API changes. The
    blanket-except is the single sanctioned pattern in this module.
    """
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


def ingest(
    cfg: Any,
    records: Iterable[Mapping[str, Any]],
    *,
    client: QdrantClient | None = None,
) -> int:
    """Bench-only ingest. Plan 15-A defines the surface; Plan 15-B fills the
    corpus walk + embed + upsert flow.
    """
    raise NotImplementedError("Plan 15-B fills the corpus walk + embed + upsert flow.")


__all__ = [
    "CODERAG_COLLECTION",
    "CODERAG_PAYLOAD_INDEX_FIELDS",
    "EVAL_COLLECTION_PREFIX",
    "coderag_collection_name",
    "ensure_indexes",
    "ingest",
]
