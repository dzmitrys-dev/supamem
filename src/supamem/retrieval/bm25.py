"""Sparse-only (BM25) retrieval backend. Plan 80.6-04 stub for entry-points."""
from __future__ import annotations

from typing import Optional

from supamem.config import ResolvedConfig
from supamem.retrieval.filters import WhereDict
from supamem.retrieval.types import RetrievedChunk


class BM25Backend:
    name = "bm25"

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: Optional[WhereDict] = None,
    ) -> list[RetrievedChunk]:  # noqa: ARG002
        raise NotImplementedError(
            "supamem.retrieval.bm25: not yet implemented — use 'tuned_hybrid' "
            "for production retrieval (D-25 lock)."
        )


__all__ = ["BM25Backend"]
