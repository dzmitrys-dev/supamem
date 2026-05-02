"""Mixed-source corpus for Phase 9 decay byte-identity test (Pitfall 2 mitigation).

3 markdown_header chunks (code/ADR/doc) + 3 transcript chunks at 0d / 7d / 28d
ages. Used by ``test_retrieval_temporal::test_decay_off_byte_identical_code_ranking``
to verify that flipping the transcript-decay knob leaves NON-transcript score
sequences BYTE-IDENTICAL — the locked invariant from Phase 9 success criterion #3.

Decisions referenced (see 09-CONTEXT.md):
- D-DECAY-01: decay shape ``score *= alpha + (1 - alpha) * 0.5 ** (age_days / hl)``.
- D-DECAY-02: ``valid_from`` is the single age source (NOT ``indexed_at`` or
  ``session_started_at``).
- D-VFROM-01: ``valid_from`` is an ISO-8601 UTC string.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def mixed_corpus() -> list[tuple[str, float, dict, list[float] | None]]:
    """Return 6 ``(doc_id, score, payload, vec)`` tuples for the byte-identity test.

    Three ``markdown_header`` chunks (representing code/ADR/doc — payload tag is
    the dispatch surface, not the file extension) and three ``transcript``
    chunks, each at 0-day / 7-day / 28-day ages relative to ``now``. Vectors
    are ``None`` because the byte-identity test runs on the post-rerank path
    where vectors have already been consumed by the retrieval pipeline.
    """
    now = datetime.now(timezone.utc)
    items: list[tuple[str, float, dict, list[float] | None]] = []
    for i, age_days in enumerate([0, 7, 28]):
        vf = (now - timedelta(days=age_days)).isoformat()
        items.append((
            f"code-{i}",
            1.0 - i * 0.1,
            {"chunker": "markdown_header", "valid_from": vf, "room": "backend"},
            None,
        ))
    for i, age_days in enumerate([0, 7, 28]):
        vf = (now - timedelta(days=age_days)).isoformat()
        items.append((
            f"transcript-{i}",
            0.95 - i * 0.1,
            {"chunker": "transcript", "valid_from": vf, "room": None},
            None,
        ))
    return items
