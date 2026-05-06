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

import importlib.metadata as _ilm
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

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


# ---- Phase 8 fixtures (added by 08-00-PLAN) -----------------------------


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch):
    """Isolate the supamem model cache to tmp_path/cache."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    return cache


@pytest.fixture
def network_blocked(monkeypatch):
    """Force HF + transformers offline mode for the test process."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    yield


@pytest.fixture
def mock_reranker_entry_point(monkeypatch):
    """Register tests._fixtures.mock_reranker:MockReranker as entry-point name='mock'."""
    from tests._fixtures.mock_reranker import MockReranker

    class _FakeEP:
        def __init__(self, name, target):
            self.name = name
            self._target = target

        def load(self):
            return self._target

    real = _ilm.entry_points
    fake_eps = [_FakeEP("mock", MockReranker)]

    def _patched(*, group=None, **kw):
        if group == "supamem.reranker":
            return fake_eps
        return real(group=group, **kw) if group else real(**kw)

    monkeypatch.setattr(_ilm, "entry_points", _patched)
    try:
        import supamem.rerankers as _rr
        monkeypatch.setattr(_rr, "entry_points", _patched, raising=False)
    except ImportError:
        pass
    return MockReranker


# ---- Phase 9 helpers (added by 09-01-PLAN) ------------------------------
#
# Forward-compat helpers for Phase 9 per-source temporal validity (TEMP-01,
# TEMP-02, TEMP-03). The four ``recency_per_source_transcript_*`` and
# ``temporal_retention_days`` fields do NOT yet exist on ``ResolvedConfig``
# (Plan 02 wires them). Like ``_cfg_with_caps``, we attach them via setattr
# so RED tests can construct configs with these knobs BEFORE the production
# fields land — collection stays clean either way.
#
# Decision references (see .planning/phases/09-per-source-temporal-validity):
# - D-CONFIG-01: flat-field naming ``recency_per_source_transcript_*`` and
#   ``temporal_retention_days``.
# - D-CONFIG-02: validators reject α∉[0,1], hl≤0, retention<0 at boot
#   (Plan 02 lands the gate; tests here exercise the helper shape only).


def _cfg_with_temporal(
    *,
    retention_days: int = 90,
    decay_enabled: bool = False,
    half_life_days: float = 14.0,
    alpha: float = 0.7,
    **overrides: Any,
) -> ResolvedConfig:
    """Build a ``ResolvedConfig`` with the four Phase 9 temporal knobs setattr-applied.

    Mirrors :func:`_cfg_with_caps` exactly: known dataclass fields are
    routed into the constructor, unknown ones (and the four temporal knobs)
    are setattr'd post-construction so the helper works on RED-phase
    configs (before Plan 02 lands the real fields) AND on GREEN-phase
    configs (where setattr on an existing dataclass field is a normal
    assignment).

    Decisions: D-CONFIG-01 (flat fields), D-DECAY-01 (defaults α=0.7,
    hl=14d), D-GC-DEFAULT-01 (retention_days=90).
    """
    base: dict[str, Any] = {
        "qdrant_url": "http://localhost:6333",
        "collection": "test_temporal",
    }
    known = set(ResolvedConfig.__dataclass_fields__)
    extras: dict[str, Any] = {}
    for k, v in overrides.items():
        if k in known:
            base[k] = v
        else:
            extras[k] = v
    cfg = ResolvedConfig(**base)
    setattr(cfg, "recency_per_source_transcript_enabled", decay_enabled)
    setattr(cfg, "recency_per_source_transcript_half_life_days", half_life_days)
    setattr(cfg, "recency_per_source_transcript_alpha", alpha)
    setattr(cfg, "temporal_retention_days", retention_days)
    for k, v in extras.items():
        setattr(cfg, k, v)
    return cfg


def make_temporal_qdrant_mock() -> MagicMock:
    """Build a MagicMock Qdrant client wired for Phase 9 helper tests.

    Pre-configures the methods Phase 9 helpers exercise:

    - ``scroll(...)`` returns ``([], None)`` by default (empty page,
      offset=None terminates the pagination loop). Tests override
      ``client.scroll.return_value`` or ``.side_effect`` for multi-page cases.
    - ``count(count_filter=...)`` returns ``SimpleNamespace(count=0)`` —
      mirrors the real ``CountResult.count`` attribute access pattern in
      ``doctor.py`` (Phase 7 D-07 room histogram precedent).
    - ``set_payload(...)`` / ``delete(...)`` / ``create_payload_index(...)``
      return None (typical mutating-RPC shape).

    Cross-references (see 09-RESEARCH.md):
    - §R-1: ``IsEmptyCondition`` — IsNullCondition does NOT match missing
      payload fields in Qdrant 1.10+, so the temporal sub-filter MUST use
      IsEmpty for live-chunk detection (legacy pre-Phase-9 points).
    - §R-3: ``create_payload_index`` with ``field_schema=DATETIME`` — first
      ``create_payload_index`` call site in the codebase; idempotent on
      re-creation per qdrant-client semantics.
    - §R-5: ``delete(points_selector=PointIdsList(points=ids))`` — Form A
      (scroll → batch IDs → delete by id list), chosen over server-side
      ``delete(filter=...)`` so the GC count remains visible to doctor +
      Welford counters.
    """
    client = MagicMock()
    client.scroll.return_value = ([], None)
    client.count.return_value = SimpleNamespace(count=0)
    client.set_payload.return_value = None
    client.delete.return_value = None
    client.create_payload_index.return_value = None
    return client


# ---- Phase 15 fixtures (added by 15-B-PLAN) -----------------------------


@pytest.fixture
def tiny_repo(tmp_path):
    """Build a deterministic tiny git repo with 10 commits + 2 ADRs.

    Used by coderag corpus / auto-query tests for offline reproducibility.
    Implementation lives at ``tests/fixtures/coderag_tiny_repo/build_tiny_repo.py``.
    """
    from tests.fixtures.coderag_tiny_repo.build_tiny_repo import build

    return build(tmp_path / "tiny")
