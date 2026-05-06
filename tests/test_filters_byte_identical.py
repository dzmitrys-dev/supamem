"""Phase 15 D-QGEN-06 lock: ``src/supamem/retrieval/filters.py`` is
byte-identical (full-file SHA-256) — ``repo`` and ``axis`` MUST flow through
the existing pass-through branch; ZERO new branches added in any Phase 15 plan.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

FILTERS = (
    Path(__file__).parent.parent / "src" / "supamem" / "retrieval" / "filters.py"
)
SNAPSHOT = (
    Path(__file__).parent
    / "fixtures"
    / "byte_identical_snapshots"
    / "filters_full.sha256"
)


def test_filters_full_file_sha256() -> None:
    actual = hashlib.sha256(FILTERS.read_bytes()).hexdigest()
    expected = SNAPSHOT.read_text().strip()
    assert actual == expected, (
        f"src/supamem/retrieval/filters.py MUTATED — D-QGEN-06 byte-identical lock VIOLATED.\n"
        f"  expected: {expected}\n  actual:   {actual}\n"
        f"  Phase 15 contract: `repo` and `axis` flow through the existing pass-through "
        f"branch — ZERO new branches added in any plan. If a new branch is genuinely "
        f"needed, propose a CONTEXT.md amendment first."
    )
