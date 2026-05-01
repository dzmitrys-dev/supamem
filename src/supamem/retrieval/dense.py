"""Dense-only retrieval backend (no fusion). Plan 80.6-04 stub for entry-points.

Lands as a thin pass-through so ``load_retrieval('dense')`` resolves to a real
class. Concrete dense-only query implementation arrives in a follow-up plan
(D-25 already mandates the hybrid arm as the production default).
"""
from __future__ import annotations

from typing import Any, Optional

from supamem.config import ResolvedConfig
from supamem.retrieval.filters import WhereDict
from supamem.retrieval.types import RetrievedChunk


class DenseBackend:
    name = "dense"

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
            "supamem.retrieval.dense: not yet implemented — use 'tuned_hybrid' "
            "for production retrieval (D-25 lock)."
        )

    # Hook so registry tests can introspect without instantiation
    @classmethod
    def kind(cls) -> str:
        return "dense"


__all__ = ["DenseBackend"]


def _ensure_unused(_: Any) -> None:  # pragma: no cover
    pass
