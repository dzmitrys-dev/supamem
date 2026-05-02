"""Plan 08-03 — tuned_hybrid rerank-on branch tests.

Locks:

- ``test_off_byte_identical`` — D-COMPOSE-03 (RERANK-01). With
  ``cfg.reranker_name="off"``, ``query()`` MUST return byte-identical
  output to the JSON golden captured at HEAD before the rerank branch
  was added.
- ``test_on_replaces_score_and_skips_recency`` — D-COMPOSE-01. When a
  reranker is on, RRF score is REPLACED by rerank score and T-4
  recency multiplier is SKIPPED (else the locked-in 1.0–1.1× boost
  would still leak into the output).
- ``test_on_widens_prefetch`` — D-POOL-01. ``Prefetch.limit=50`` when
  reranker is on; remains 20 (``PREFETCH_LIMIT``) when off.
- ``test_dedup_runs_after_rerank`` — D-COMPOSE-02. T-5 cosine dedup
  consumes the reranked ordering: when two near-duplicate candidates
  carry different rerank scores, the higher-rerank-score one survives.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk

_GOLDEN_PATH = Path(__file__).parent / "_fixtures" / "tuned_hybrid_pre_phase8.json"

# Frozen "now" mirrors the value used in tests/_fixtures/_capture_golden.py.
# Re-baseline by re-running that script if you change this.
_FROZEN_NOW = _dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture
def freeze_recency_clock(monkeypatch: pytest.MonkeyPatch):
    """Pin tuned_hybrid's clock so the recency multiplier is deterministic."""
    import supamem.retrieval.tuned_hybrid as _th_mod

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)

    monkeypatch.setattr(_th_mod, "datetime", _FrozenDT)
    yield


def _build_deterministic_points() -> list[MagicMock]:
    """Mirror tests/_fixtures/_capture_golden.py exactly (byte-identity)."""
    base = 1.0 / math.sqrt(4)
    vec_a = [base, base, base, base]
    vec_b = [base, base, -base, -base]
    vec_c = [base, -base, base, -base]
    vec_d = [-base, base, base, -base]
    vec_a_dup = [base, base, base + 1e-6, base - 1e-6]

    raws = [
        ("d1", 0.95, {"document": "alpha one", "source": "a.md",
                      "updated_at": "2026-04-01T00:00:00+00:00"}, vec_a),
        ("d2", 0.92, {"document": "beta two", "source": "b.md",
                      "updated_at": "2024-01-01T00:00:00+00:00"}, vec_b),
        ("d3", 0.88, {"document": "alpha one prime", "source": "c.md",
                      "updated_at": "2026-04-15T00:00:00+00:00"}, vec_a_dup),
        ("d4", 0.80, {"document": "gamma three", "source": "d.md"}, vec_c),
        ("d5", 0.70, {"document": "delta four", "source": "e.md",
                      "updated_at": "2025-06-01T00:00:00+00:00"}, vec_d),
        ("d6", 0.60, {"document": "epsilon five", "source": "f.md"}, vec_b),
        ("d7", 0.55, {"document": "zeta six", "source": "g.md"}, vec_a),
        ("d8", 0.50, {"document": "eta seven", "source": "h.md"}, vec_c),
        ("d9", 0.45, {"document": "theta eight", "source": "i.md"}, vec_d),
        ("d10", 0.40, {"document": "iota nine", "source": "j.md"}, vec_b),
    ]
    points = []
    for hid, score, payload, vec in raws:
        p = MagicMock()
        p.id = hid
        p.score = score
        p.payload = payload
        p.vector = {"dense": list(vec)}
        points.append(p)
    return points


def _build_backend(
    reranker_name: str = "off",
    *,
    points: list[Any] | None = None,
) -> tuple[TunedHybridBackend, MagicMock]:
    cfg = ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection="test_rerank_collection",
        reranker_name=reranker_name,
    )
    backend = TunedHybridBackend(config=cfg)

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(
        points=points if points is not None else _build_deterministic_points()
    )
    backend._client = fake_client

    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.5, 0.5, 0.5, 0.5] for _ in batch])

    class _SparseVec:
        indices = [1, 2]
        values = [0.5, 0.4]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])
    backend._dense = fake_dense
    backend._sparse = fake_sparse

    import supamem.retrieval.tuned_hybrid as mod
    mod._SPARSE_AVAILABLE = True

    return backend, fake_client


# ────────────────────────────────────────────────────────────────────────
# RERANK-01 / D-COMPOSE-03 — rerank-off byte-identity
# ────────────────────────────────────────────────────────────────────────


def test_off_byte_identical(freeze_recency_clock) -> None:
    """rerank-off path MUST match the pre-Phase-8 JSON golden bit-for-bit."""
    backend, _ = _build_backend(reranker_name="off")
    out = backend.query("known query", k=5)

    expected = [
        RetrievedChunk.model_validate(d) for d in json.loads(_GOLDEN_PATH.read_text())
    ]
    assert [c.id for c in out] == [c.id for c in expected], (
        f"id ordering drift: got {[c.id for c in out]} "
        f"vs golden {[c.id for c in expected]}"
    )
    for got, want in zip(out, expected):
        assert got.id == want.id
        assert got.text == want.text
        assert got.score == pytest.approx(want.score, rel=1e-6, abs=1e-9)
        assert got.source_path == want.source_path
        assert got.rerank_score is None  # off path never sets it


# ────────────────────────────────────────────────────────────────────────
# RERANK-01 / D-COMPOSE-01 — rerank-on replaces score + skips T-4 recency
# ────────────────────────────────────────────────────────────────────────


def test_on_replaces_score_and_skips_recency(
    mock_reranker_entry_point,
) -> None:
    """With ``reranker_name="mock"``: scores REPLACED by reranker, T-4 SKIPPED.

    The fixture ``MockReranker`` reverses the candidate list and stamps
    integer scores ``len, len-1, ..., 1``. So:

    1. ``out[i].score == out[i].rerank_score`` (D-COMPOSE-01 score replacement).
    2. The recency-boosted scores from the off-path (e.g. d1=0.983, d5=0.7000009)
       MUST NOT appear — every output score is an integer (D-COMPOSE-01 skip-recency).
    3. Ordering changes (mock reverses the input, so the LAST RRF hit becomes first).
    """
    backend, _ = _build_backend(reranker_name="mock")
    out = backend.query("known query", k=5)

    assert len(out) > 0, "rerank-on produced empty output"
    for chunk in out:
        # D-COMPOSE-01: score == rerank_score (mock writes both).
        assert chunk.rerank_score is not None, (
            f"rerank_score not set on {chunk.id} — D-COMPOSE-01 score-passthrough broken"
        )
        assert chunk.score == chunk.rerank_score, (
            f"score / rerank_score divergence on {chunk.id}: "
            f"{chunk.score} != {chunk.rerank_score}"
        )
        # D-COMPOSE-01 T-4 skip: every score MUST be an integer (mock contract);
        # the off-path's recency multiplier would have produced floats like 0.983.
        assert chunk.score == float(int(chunk.score)), (
            f"score {chunk.score} on {chunk.id} is non-integer — recency leaked through"
        )

    # Ordering MUST differ from the off-path golden (the mock reverses the
    # ordering, so id-list cannot be the same as RRF + recency).
    expected_off = [c["id"] for c in json.loads(_GOLDEN_PATH.read_text())]
    assert [c.id for c in out] != expected_off, (
        "rerank-on output matches rerank-off — reranker did not run"
    )


# ────────────────────────────────────────────────────────────────────────
# D-POOL-01 — prefetch widening
# ────────────────────────────────────────────────────────────────────────


def test_on_widens_prefetch(mock_reranker_entry_point) -> None:
    """Prefetch.limit MUST be ``reranker_prefetch_per_arm`` (50) when on."""
    backend, fake_client = _build_backend(reranker_name="mock")
    backend.query("hello", k=5)
    kwargs = fake_client.query_points.call_args.kwargs
    prefetch = kwargs["prefetch"]
    assert len(prefetch) == 2
    assert prefetch[0].limit == 50, (
        f"dense Prefetch.limit = {prefetch[0].limit}; expected 50 (reranker_prefetch_per_arm)"
    )
    assert prefetch[1].limit == 50, (
        f"sparse Prefetch.limit = {prefetch[1].limit}; expected 50"
    )


def test_off_keeps_default_prefetch() -> None:
    """Prefetch.limit MUST stay 20 (PREFETCH_LIMIT) when reranker is off."""
    backend, fake_client = _build_backend(reranker_name="off")
    backend.query("hello", k=5)
    kwargs = fake_client.query_points.call_args.kwargs
    prefetch = kwargs["prefetch"]
    assert prefetch[0].limit == 20
    assert prefetch[1].limit == 20


# ────────────────────────────────────────────────────────────────────────
# D-COMPOSE-02 — T-5 dedup runs AFTER rerank
# ────────────────────────────────────────────────────────────────────────


def test_dedup_runs_after_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two near-duplicates with different rerank scores: higher-rerank survives.

    Setup: 2 hits, identical vectors (cosine ~ 1.0 > 0.97 threshold), different
    RRF scores. A reranker assigns the LOWER-RRF hit a HIGHER rerank score,
    inverting the ranking. After rerank the higher-rerank hit must be processed
    first by dedup, so it (and not the higher-RRF hit) survives.
    """
    base = 1.0 / math.sqrt(4)
    vec = [base, base, base, base]

    p1 = MagicMock()
    p1.id = "high_rrf_low_rerank"
    p1.score = 0.99
    p1.payload = {"document": "first by RRF", "source": "x.md"}
    p1.vector = {"dense": list(vec)}

    p2 = MagicMock()
    p2.id = "low_rrf_high_rerank"
    p2.score = 0.10
    p2.payload = {"document": "first by rerank", "source": "y.md"}
    p2.vector = {"dense": list(vec)}

    # Register a custom reranker that promotes p2 above p1.
    class _PromoteP2:
        name = "promote_p2"
        model_id = "test/promote-p2"

        def __init__(self, *, config: ResolvedConfig) -> None:
            self.config = config

        def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
            # p2 first with score 10.0; p1 second with 1.0
            ordered = []
            for c in candidates:
                if c.id == "low_rrf_high_rerank":
                    ordered.append((10.0, c))
                else:
                    ordered.append((1.0, c))
            ordered.sort(key=lambda t: t[0], reverse=True)
            return [
                c.model_copy(update={"score": s, "rerank_score": s}) for s, c in ordered
            ]

    import importlib.metadata as _ilm

    class _FakeEP:
        def __init__(self, name, target):
            self.name = name
            self._target = target

        def load(self):
            return self._target

    real = _ilm.entry_points
    fake_eps = [_FakeEP("promote_p2", _PromoteP2)]

    def _patched(*, group=None, **kw):
        if group == "supamem.reranker":
            return fake_eps
        return real(group=group, **kw) if group else real(**kw)

    monkeypatch.setattr(_ilm, "entry_points", _patched)
    import supamem.rerankers as _rr
    monkeypatch.setattr(_rr, "entry_points", _patched, raising=False)

    backend, _ = _build_backend(reranker_name="promote_p2", points=[p1, p2])
    out = backend.query("q", k=5)

    # D-COMPOSE-02: dedup runs AFTER rerank → p2 (now first by rerank) survives,
    # p1 is the duplicate that gets killed.
    assert len(out) == 1, f"expected 1 chunk after dedup, got {len(out)}: {[c.id for c in out]}"
    assert out[0].id == "low_rrf_high_rerank", (
        f"D-COMPOSE-02 violated: dedup ran before rerank. "
        f"Got {out[0].id}; expected low_rrf_high_rerank (higher rerank score)"
    )
    assert out[0].rerank_score == 10.0
