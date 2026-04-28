"""Retrieval backend registry.

Backends are discovered via ``importlib.metadata.entry_points`` under the
``supamem.retrieval`` group. Built-in backends (`tuned_hybrid`, `dense`, `bm25`)
are registered in ``pyproject.toml``; third-party packages may register more.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from supamem.retrieval.types import RetrievedChunk

__all__ = ["RetrievedChunk", "load_retrieval"]


def load_retrieval(name: str) -> Any:
    """Resolve a registered ``supamem.retrieval`` plugin class by name."""
    for ep in entry_points(group="supamem.retrieval"):
        if ep.name == name:
            return ep.load()
    raise LookupError(f"supamem: no retrieval plugin registered for name={name!r}")
