"""Dense embedder — thin wrapper over fastembed.TextEmbedding."""
from __future__ import annotations

from typing import Any, Iterable


class MiniLMEmbedder:
    """Default dense embedder. Uses ``sentence-transformers/all-MiniLM-L6-v2`` (384-dim)."""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model = model
        self._impl: Any | None = None

    def _ensure(self) -> Any:
        if self._impl is None:
            from fastembed import TextEmbedding

            self._impl = TextEmbedding(self.model)
        return self._impl

    def embed(self, batch: Iterable[str]) -> Iterable[Any]:
        return self._ensure().embed(list(batch))
