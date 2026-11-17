"""Process-wide model pool (v0.4.0a6).

Before a6 every ``TunedHybridBackend`` (and every reranker wrapper it created) loaded its own
dense / sparse / reranker models, so one process serving K collections held K copies of
~2.4 GB of weights. The pool keys heavy models by *model id* — the only thing that determines
the weights — so a process (the shared daemon, an MCP server) holds exactly one copy of each
model however many collections and configs it serves.

The pool never constructs models itself: callers pass a zero-arg ``factory`` so the import
site (and therefore test monkeypatching of e.g. ``tuned_hybrid.TextEmbedding``) is unchanged.
A per-key load lock guarantees two threads never load the same model twice; the global lock
is never held while a model loads.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str], Any] = {}
_LOAD_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def get(kind: str, model_id: str, factory: Callable[[], Any]) -> Any:
    """Return the pooled ``(kind, model_id)`` model, loading it via ``factory`` on a miss."""
    key = (kind, model_id)
    with _LOCK:
        model = _MODELS.get(key)
        if model is not None:
            return model
        load_lock = _LOAD_LOCKS.setdefault(key, threading.Lock())
    with load_lock:
        with _LOCK:
            model = _MODELS.get(key)
        if model is not None:
            return model
        model = factory()
        with _LOCK:
            _MODELS[key] = model
        return model


def clear() -> int:
    """Drop every pooled model reference; returns how many were dropped.

    The caller runs ``gc.collect()`` afterwards (torch finalizers need two passes).
    In-flight users keep their local references alive until they finish.
    """
    with _LOCK:
        n = len(_MODELS)
        _MODELS.clear()
        return n


def loaded() -> dict[str, list[str]]:
    """``{kind: [model_id, ...]}`` snapshot for status/doctor surfaces."""
    with _LOCK:
        out: dict[str, list[str]] = {}
        for kind, model_id in _MODELS:
            out.setdefault(kind, []).append(model_id)
        return out


def dtypes() -> dict[str, str]:
    """Best-effort ``{model_id: dtype}`` for torch-backed models (issue #5 data)."""
    with _LOCK:
        items = list(_MODELS.items())
    out: dict[str, str] = {}
    for (kind, model_id), model in items:
        inner = getattr(model, "model", None)
        dtype = getattr(inner, "dtype", None) or getattr(model, "dtype", None)
        if dtype is not None:
            out[f"{kind}:{model_id}"] = str(dtype)
    return out
