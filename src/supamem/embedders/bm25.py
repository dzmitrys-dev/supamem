"""Sparse embedder — thin wrapper over fastembed.SparseTextEmbedding(BM25)."""
from __future__ import annotations

from typing import Any, Iterable


class BM25Embedder:
    """BM25 sparse embedder. Requires ``fastembed[nlp]``."""

    def __init__(self, model: str = "Qdrant/bm25") -> None:
        self.model = model
        self._impl: Any | None = None

    def _ensure(self) -> Any:
        if self._impl is None:
            try:
                from fastembed import SparseTextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "supamem: fastembed[nlp] required for sparse retrieval"
                ) from exc
            self._impl = SparseTextEmbedding(self.model)
        return self._impl

    def embed(self, batch: Iterable[str]) -> Iterable[Any]:
        return self._ensure().embed(list(batch))
