"""Reranker plugin loader for supamem (entry-point group: supamem.reranker).

Plugin contract (D-CONTRACT-01..05 / D-POOL-02 / D-CONFIG-03):

    rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]

``name == "off"`` is the reserved sentinel meaning "no plugin" — the loader
returns ``None`` *without* iterating entry-points (cheap fast-path; preserves
pre-Phase-8 byte-identical retrieval behavior).
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supamem.config import ResolvedConfig
    from supamem.retrieval.types import RetrievedChunk


@runtime_checkable
class RerankerProtocol(Protocol):
    """Public surface every supamem.reranker plugin satisfies."""

    name: str
    model_id: str

    def rerank(
        self, query: str, candidates: list["RetrievedChunk"]
    ) -> list["RetrievedChunk"]:
        ...


def load_reranker(name: str, config: "ResolvedConfig") -> Optional[Any]:
    """Resolve and instantiate a registered ``supamem.reranker`` plugin.

    Returns ``None`` for the sentinel ``"off"`` without iterating entry-points.
    Raises :class:`LookupError` for an unknown name with the list of registered
    plugin names included in the message.
    """
    if name == "off":
        return None
    registered: list[str] = []
    for ep in entry_points(group="supamem.reranker"):
        registered.append(ep.name)
        if ep.name == name:
            cls = ep.load()
            return cls(config=config)
    raise LookupError(
        f"supamem: no reranker plugin registered for name={name!r} "
        f"(known: {sorted(registered) or '[]'})"
    )


# --- Eager-fetch helper stub (filled in by Plan 08-02) -----------------------
# Plan 08-02 adds:
#     from huggingface_hub import snapshot_download
#     from filelock import FileLock
#     def _model_cache_dir() -> Path: ...
#     def prepare(model_id: str, *, progress=None) -> Path: ...
