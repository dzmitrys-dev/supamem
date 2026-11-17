"""Multilingual dense embedder — paraphrase-multilingual-MiniLM-L12-v2.

v0.4.0a5 answer to the language gap measured by the 2026-11-14 effectiveness
study: the corpora are 63–86% Russian while the default all-MiniLM-L6-v2 is
English-only (production lang_mismatch 63.7% after the a4 filters). This model
is multilingual (50+ languages), keeps the SAME 384-dim schema as the default
(``qdrant_collection.DEFAULT_VECTOR_SIZE``) so a converting project only needs
a NEW COLLECTION NAME (different vector space — never mix), needs no
query/passage prefixes, and costs 0.22 GB (vs 2.24 GB for e5-large — rejected
for now: bigger RAM footprint on a swap-starved host, 1024-dim schema
plumbing, prefix protocol). ADR-0004 records the decision and the upgrade
path (e5-large / bge-m3 once the shared daemon ships, issue #5).
"""
from __future__ import annotations

from supamem.embedders.minilm import MiniLMEmbedder

MULTILINGUAL_DENSE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class MultilingualMiniLMEmbedder(MiniLMEmbedder):
    """Opt-in dense embedder ([supamem] embedder = "multilingual_minilm")."""

    name = "multilingual_minilm"
    model_id = MULTILINGUAL_DENSE_MODEL

    def __init__(self, model: str = MULTILINGUAL_DENSE_MODEL) -> None:
        super().__init__(model)
