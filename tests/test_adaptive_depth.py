"""Phase 18 Plan F — opt-in adaptive retrieval depth (Req-03).

Tests ``complexity_score`` and ``_effective_k`` in ``tuned_hybrid`` plus
default-OFF byte-identical ``query()`` integration.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import (
    TunedHybridBackend,
    _effective_k,
    complexity_score,
)


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "test_adaptive_depth"),
        **overrides,
    )


def _adaptive_config(**overrides: Any) -> SimpleNamespace:
    base = {
        "adaptive_depth_enabled": True,
        "adaptive_depth_delta": 0.5,
        "adaptive_depth_k_max": 20,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_complexity_score_bounded() -> None:
    """C_q stays in [0, 1] for representative queries."""
    samples = [
        ("hi", None),
        ("?" * 50, {"room": "tests"}),
        (
            "Why does `foo_bar` in module.py fail when x and y or z; path: src?",
            {"path_prefix": "src/"},
        ),
    ]
    for text, where in samples:
        score = complexity_score(text, where)
        assert 0.0 <= score <= 1.0, f"out of range for {text!r}: {score}"


def test_complexity_score_monotonic() -> None:
    """Simpler query scores lower than a richer coding-style query."""
    simple = "hello"
    complex_q = (
        "Where is `tuned_hybrid` defined in retrieval.py and why does "
        "foo_bar::baz fail; compare x and y or z?"
    )
    assert complexity_score(simple, None) < complexity_score(
        complex_q, {"room": "backend"}
    )


def test_effective_k_never_below_base() -> None:
    config = _adaptive_config()
    for k in (1, 3, 5, 10):
        eff = _effective_k("complex " * 40, k, {"room": "tests"}, config)
        assert eff >= k


def test_effective_k_monotonic() -> None:
    config = _adaptive_config()
    k_base = 5
    simple = "hi"
    complex_q = (
        "Trace `module_name` in src/foo_bar.py: why error and timeout or retry?"
    )
    k_simple = _effective_k(simple, k_base, None, config)
    k_complex = _effective_k(complex_q, k_base, {"path_prefix": "src/"}, config)
    assert k_complex >= k_simple


def test_effective_k_respects_k_max() -> None:
    config = _adaptive_config(adaptive_depth_k_max=8, adaptive_depth_delta=2.0)
    absurd = "?" * 200 + " `snake_case` " * 50 + " and or ; : .py :: "
    eff = _effective_k(absurd, 5, {"room": "x", "path_prefix": "y"}, config)
    assert eff <= 8


def test_adaptive_depth_default_off_preserves_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When disabled, query_points limit matches pre-adaptive-depth formula."""
    import supamem.retrieval.tuned_hybrid as mod

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(points=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    k = 5
    backend = TunedHybridBackend(config=_cfg(adaptive_depth_enabled=False))
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    backend.query("hello", k=k)

    kwargs = fake_client.query_points.call_args.kwargs
    assert kwargs["limit"] == max(k * 2, 10)


def test_adaptive_depth_enabled_raises_query_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled, a complex query widens query_points limit vs simple at same k."""
    import supamem.retrieval.tuned_hybrid as mod

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(points=[])

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        indices = [1]
        values = [0.5]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    monkeypatch.setattr(mod, "_SPARSE_AVAILABLE", True, raising=False)

    cfg = _cfg(
        adaptive_depth_enabled=True,
        adaptive_depth_delta=0.5,
        adaptive_depth_k_max=20,
    )
    backend = TunedHybridBackend(config=cfg)
    backend._client = fake_client
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    k = 5
    simple_limit: int | None = None
    complex_limit: int | None = None

    backend.query("hi", k=k)
    simple_limit = fake_client.query_points.call_args.kwargs["limit"]

    fake_client.reset_mock()
    backend.query(
        "Why does `foo_bar` in src/module.py fail and timeout or retry; path: x?",
        k=k,
        where={"room": "backend"},
    )
    complex_limit = fake_client.query_points.call_args.kwargs["limit"]

    assert simple_limit is not None and complex_limit is not None
    assert complex_limit >= simple_limit
    assert complex_limit > max(k * 2, 10)
