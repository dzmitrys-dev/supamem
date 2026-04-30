"""Shared module-level helpers for cap-related tests (Phase 05).

These are NOT pytest fixtures — they take arguments, so tests import and call
them directly. Located in ``conftest.py`` so they are auto-discoverable for
the whole ``tests/`` package without a separate import path.

Red-phase note: ``ResolvedConfig`` does not yet have ``mcp_caps_max_*`` fields
(Wave 1 adds them). To keep test *collection* clean we set those attributes
post-construction with ``setattr`` — Python lets you set any attribute on a
plain dataclass instance, so this works in both red and green phases. Tests
that read ``cfg.mcp_caps_*`` will resolve correctly either way; assertions
inside the production code path are what fail in red phase.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from supamem.config import ResolvedConfig


def _cfg_with_caps(
    *,
    max_top_k: int = 5,
    max_query_chars: int = 100,
    max_preview_chars: int = 50,
    **overrides: Any,
) -> ResolvedConfig:
    """Build a ``ResolvedConfig`` with cap overrides for boundary tests.

    Constructs the base config first (only known dataclass fields), then
    attaches ``mcp_caps_max_*`` via ``setattr``. Once Wave 1 adds those as
    real dataclass fields the helper continues to work — ``setattr`` is a
    no-op on already-existing fields and the production code reads them
    transparently either way.
    """
    base: dict[str, Any] = {
        "qdrant_url": "http://localhost:6333",
        "collection": "test_caps",
    }
    # Forward only kwargs that are real dataclass fields; stash unknown ones
    # for post-construction setattr. This insulates collection from TypeError
    # if a test passes a future field that hasn't shipped yet.
    known = set(ResolvedConfig.__dataclass_fields__)
    extras: dict[str, Any] = {}
    for k, v in overrides.items():
        if k in known:
            base[k] = v
        else:
            extras[k] = v
    cfg = ResolvedConfig(**base)
    # Cap fields — Wave 1 will promote these to real dataclass fields.
    setattr(cfg, "mcp_caps_max_top_k", max_top_k)
    setattr(cfg, "mcp_caps_max_query_chars", max_query_chars)
    setattr(cfg, "mcp_caps_max_preview_chars", max_preview_chars)
    for k, v in extras.items():
        setattr(cfg, k, v)
    return cfg


def _mock_backend_with_long_chunks(
    monkeypatch: Any,
    n_hits: int = 10,
    text_len: int = 500,
) -> MagicMock:
    """Patch ``supamem.mcp_server._get_backend`` to return a fake backend.

    The fake backend yields ``n_hits`` ``RetrievedChunk`` objects, each with
    ``text="x" * text_len`` and monotonically decreasing scores. Tests use
    this to drive the cap-enforcement code path without touching Qdrant.
    """
    import supamem.mcp_server as mod
    from supamem.retrieval.types import RetrievedChunk

    fake = MagicMock()
    fake.query.return_value = [
        RetrievedChunk(
            id=str(i),
            text="x" * text_len,
            score=0.9 - i * 0.01,
            source_path=f"s{i}.md",
        )
        for i in range(n_hits)
    ]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake)
    return fake
