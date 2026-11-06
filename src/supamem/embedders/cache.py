"""Where fastembed keeps its model files.

fastembed defaults to ``<tempfile.gettempdir()>/fastembed_cache``. On macOS that is ``/var/folders/.../T``, which the
OS purges: the snapshot directories survive while ``model.onnx`` disappears, and every fresh process (an edit hook,
a re-index, a restarted MCP server) then tries to re-download the model mid-call — it hangs or fails
(field report 2026-11-06). Models belong in the persistent supamem cache, like the reranker's
(``platformdirs.user_cache_dir("supamem")/models``).
"""
from __future__ import annotations

import os
from pathlib import Path


def fastembed_cache_dir() -> str:
    """``$FASTEMBED_CACHE_PATH`` (fastembed's own override) → ``$SUPAMEM_CACHE_DIR/fastembed`` →
    ``<user cache>/supamem/fastembed``."""
    override = os.environ.get("FASTEMBED_CACHE_PATH")
    if override:
        return override
    root = os.environ.get("SUPAMEM_CACHE_DIR")  # repo-wide cache-root override, as for the reranker models
    if root:
        target = Path(root) / "fastembed"
    else:
        import platformdirs

        target = Path(platformdirs.user_cache_dir("supamem")) / "fastembed"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def models_offline() -> bool:
    """``HF_HUB_OFFLINE`` set truthy → load models from the cache only (the edit hook sets it: no download mid-edit)."""
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def fastembed_kwargs() -> dict[str, object]:
    """Constructor kwargs for every fastembed model: the persistent cache, and cache-only loading when offline.

    ``local_files_only`` is read at construction time, so it works even when ``huggingface_hub`` was imported
    before ``HF_HUB_OFFLINE`` was set (huggingface_hub reads that variable only at import)."""
    return {"cache_dir": fastembed_cache_dir(), "local_files_only": models_offline()}


def prepare_embedders() -> list[str]:
    """Fetch the retrieval embedders into the persistent cache (install / repair — never inside a hook).

    Instantiating a fastembed model downloads it when absent; ``local_files_only=False`` here on purpose: this is
    the one place that is allowed to go to the network. Returns the model names that are now cached."""
    from fastembed import SparseTextEmbedding, TextEmbedding

    from supamem.retrieval.tuned_hybrid import DEFAULT_DENSE_MODEL, DEFAULT_SPARSE_MODEL

    cache = fastembed_cache_dir()
    TextEmbedding(DEFAULT_DENSE_MODEL, cache_dir=cache, local_files_only=False)
    SparseTextEmbedding(DEFAULT_SPARSE_MODEL, cache_dir=cache, local_files_only=False)
    return [DEFAULT_DENSE_MODEL, DEFAULT_SPARSE_MODEL]
