"""Embedder builders + plugin loader for supamem.

Plugin lookup uses ``importlib.metadata.entry_points(group="supamem.embedder")``.
``build_dense_embedder`` and ``build_sparse_embedder`` are convenience helpers
the indexer calls; both return embedder instances exposing ``embed(batch)``.

v0.4.0a5 — ``dense_model_id_for(cfg)`` is the ONE resolver that maps
``cfg.embedder`` (a plugin name) to the fastembed model id the dense arm must
load. Until a5 nothing consumed ``cfg.embedder`` (indexer, retrieval backend
and memory_writer each hardcoded the default model), so configuring a plugin
was a silent no-op. All three production funnels now call this resolver;
``minilm`` (the default) resolves byte-identically to ``DEFAULT_DENSE_MODEL``
so non-opt-in users see no behavior change.
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


def dense_model_id_for(cfg: Any) -> str:
    """The fastembed model id for ``cfg.embedder`` (default → ``DEFAULT_DENSE_MODEL``).

    Plugin classes carry ``model_id`` (class attr) — resolution imports the
    plugin module, which is cheap: heavy deps load lazily inside ``_ensure()``.
    ``load_config`` fails closed on unknown names (mirroring the reranker
    gate), so a ``LookupError`` here means a directly-constructed
    ``ResolvedConfig`` bypassed that gate; it propagates with a clear message
    rather than silently indexing with the wrong model.
    """
    name = str(getattr(cfg, "embedder", "minilm") or "minilm")
    if name == "minilm":
        return DEFAULT_DENSE_MODEL
    cls = load_embedder(name)
    model_id = getattr(cls, "model_id", None)
    if not isinstance(model_id, str) or not model_id:
        raise RuntimeError(
            f"supamem: embedder plugin {name!r} does not declare a model_id class attribute"
        )
    return model_id


def build_dense_embedder(model: str = DEFAULT_DENSE_MODEL) -> Any:
    from supamem.embedders.minilm import MiniLMEmbedder

    return MiniLMEmbedder(model)


def build_sparse_embedder(model: str = DEFAULT_SPARSE_MODEL) -> Any:
    from supamem.embedders.bm25 import BM25Embedder

    return BM25Embedder(model)
