"""Embedder builders + plugin loader for supamem.

Plugin lookup uses ``importlib.metadata.entry_points(group="supamem.embedder")``.
``build_dense_embedder`` and ``build_sparse_embedder`` are convenience helpers
the indexer calls; both return embedder instances exposing ``embed(batch)``.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

DEFAULT_DENSE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"


def load_embedder(name: str) -> Any:
    """Resolve a registered ``supamem.embedder`` plugin by name."""
    for ep in entry_points(group="supamem.embedder"):
        if ep.name == name:
            return ep.load()
    raise LookupError(f"supamem: no embedder plugin registered for name={name!r}")


def build_dense_embedder(model: str = DEFAULT_DENSE_MODEL) -> Any:
    from supamem.embedders.minilm import MiniLMEmbedder

    return MiniLMEmbedder(model)


def build_sparse_embedder(model: str = DEFAULT_SPARSE_MODEL) -> Any:
    from supamem.embedders.bm25 import BM25Embedder

    return BM25Embedder(model)
