"""Capture pre-Phase-8 byte-identity golden for tuned_hybrid.query().

Run ONCE BEFORE Plan 08-03 modifies tuned_hybrid.py:

    uv run python tests/_fixtures/_capture_golden.py

Produces tests/_fixtures/tuned_hybrid_pre_phase8.json — the snapshot the
``test_off_byte_identical`` test asserts byte-identical to (RERANK-01 /
D-COMPOSE-03 byte-identity invariant).

Re-run only when you intentionally rebaseline (and document why in the
plan SUMMARY).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock

from supamem.config import ResolvedConfig
from supamem.retrieval.tuned_hybrid import TunedHybridBackend


def _build_deterministic_points() -> list[MagicMock]:
    """10 deterministic Qdrant hits with stable ids/scores/payloads/vectors.

    Mix of payload shapes to exercise T-4 recency multiplier
    (``updated_at``), T-5 cosine dedup (paired vectors), and T-8 token
    budget (varied document lengths).
    """
    # Use fixed unit-length vectors so cosine values are stable across
    # platforms (no float drift from random embeddings).
    base = 1.0 / math.sqrt(4)
    # 4 distinct cluster vectors so dedup spares ≥3 hits
    vec_a = [base, base, base, base]
    vec_b = [base, base, -base, -base]
    vec_c = [base, -base, base, -base]
    vec_d = [-base, base, base, -base]
    # near-duplicate of vec_a -> trips dedup
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
        # Tuned_hybrid reads hit.vector as either dict or list; mirror the
        # dict-with-DENSE_VECTOR_NAME shape used in production.
        p.vector = {"dense": list(vec)}
        points.append(p)
    return points


def _build_backend() -> TunedHybridBackend:
    cfg = ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection="golden_capture_collection",
        reranker_name="off",  # force pre-Phase-8 path
    )
    backend = TunedHybridBackend(config=cfg)

    fake_client = MagicMock()
    fake_client.query_points.return_value = MagicMock(points=_build_deterministic_points())
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

    # Bypass _ensure() for sparse-availability check by patching the
    # module-level flag.
    import supamem.retrieval.tuned_hybrid as mod
    mod._SPARSE_AVAILABLE = True

    return backend


def main() -> None:
    # Freeze time so the recency multiplier (datetime.now()) is deterministic.
    # Test asserts byte-identity at the SAME frozen timestamp.
    import datetime as _dt
    import supamem.retrieval.tuned_hybrid as _th_mod

    _FROZEN_NOW = _dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)

    _orig = _th_mod.datetime
    _th_mod.datetime = _FrozenDT  # type: ignore[misc]
    try:
        backend = _build_backend()
        out = backend.query("known query", k=5)
    finally:
        _th_mod.datetime = _orig

    payload = [c.model_dump() for c in out]
    target = Path(__file__).parent / "tuned_hybrid_pre_phase8.json"
    target.write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {len(payload)} chunks to {target}")


if __name__ == "__main__":
    main()
