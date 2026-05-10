"""Plan 17-B2 Task 1 (RED) — retrieval entry-point dispatch tests.

Locks the wiring contract for D-WIRE-03:
- ``eval/runner.py:_build_backend(cfg, suite="coderag")`` reads
  ``cfg.retrieval_name`` and instantiates the matching
  ``supamem.retrieval`` entry-point class — NOT always
  ``TunedHybridBackend``.
- Default fallback (empty / unset / ``"tuned_hybrid"``) preserves
  byte-identical behavior.
- Bench collection name remains ``supamem_eval_coderag`` regardless of
  retrieval choice (Plan 15-A invariant).
"""
from __future__ import annotations

from dataclasses import replace as _replace
from unittest.mock import patch

import pytest

from supamem.config import ResolvedConfig


def _cfg(retrieval_name: str = "") -> ResolvedConfig:
    base = ResolvedConfig()
    if retrieval_name:
        return _replace(base, retrieval_name=retrieval_name)
    return base


def test_build_backend_loads_retrieval_via_entry_points() -> None:
    """``cfg.retrieval_name='tuned_hybrid_hyde'`` returns a
    ``TunedHybridHyDEBackend`` instance from ``_build_backend(cfg, suite='coderag')``."""
    from supamem.eval.runner import _build_backend

    cfg = _cfg("tuned_hybrid_hyde")
    # Backend constructors avoid network at __init__ time — the actual
    # Qdrant client is lazy. We patch instantiation just in case to keep
    # the test pure.
    with patch("supamem.retrieval.tuned_hybrid_hyde.TunedHybridHyDEBackend.__init__",
               return_value=None) as init:
        backend = _build_backend(cfg, suite="coderag")
    assert type(backend).__name__ == "TunedHybridHyDEBackend"
    assert init.called


def test_build_backend_retrieval_dispatch_default() -> None:
    """Empty / unset / ``tuned_hybrid`` returns a ``TunedHybridBackend``
    instance (byte-identical to current behavior)."""
    from supamem.eval.runner import _build_backend

    for name in ("", "tuned_hybrid"):
        cfg = _cfg(name)
        with patch("supamem.retrieval.tuned_hybrid.TunedHybridBackend.__init__",
                   return_value=None):
            backend = _build_backend(cfg, suite="coderag")
        assert type(backend).__name__ == "TunedHybridBackend", name


def test_build_backend_unknown_retrieval_name_raises() -> None:
    """Unknown ``retrieval_name`` raises SystemExit with an actionable msg."""
    from supamem.eval.runner import _build_backend

    cfg = _cfg("definitely_not_a_backend")
    with pytest.raises(SystemExit):
        _build_backend(cfg, suite="coderag")


def test_build_backend_replaces_collection_for_coderag() -> None:
    """Bench collection always rewritten to ``supamem_eval_coderag``
    regardless of the retrieval choice (Plan 15-A invariant)."""
    from supamem.eval.runner import _build_backend

    captured: dict = {}

    def _capture_init(self, *, config):
        captured["collection"] = config.collection

    cfg = _cfg("tuned_hybrid")
    with patch("supamem.retrieval.tuned_hybrid.TunedHybridBackend.__init__",
               new=_capture_init):
        _build_backend(cfg, suite="coderag")
    assert captured["collection"] == "supamem_eval_coderag"

    captured.clear()
    cfg2 = _cfg("tuned_hybrid_hyde")
    with patch("supamem.retrieval.tuned_hybrid_hyde.TunedHybridHyDEBackend.__init__",
               new=_capture_init):
        _build_backend(cfg2, suite="coderag")
    assert captured["collection"] == "supamem_eval_coderag"
