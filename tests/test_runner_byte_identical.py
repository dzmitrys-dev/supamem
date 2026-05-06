"""Phase 14 D-VEND-04 carry-lock to Phase 15: ``_run_goldens_legacy`` source
bytes are byte-identical to the captured snapshot.

Updating the snapshot requires an explicit decision-record in CONTEXT.md.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

from supamem.eval.runner import _run_goldens_legacy

SNAPSHOT = (
    Path(__file__).parent
    / "fixtures"
    / "byte_identical_snapshots"
    / "runner_run_goldens_legacy.sha256"
)


def test_run_goldens_legacy_function_source_sha256() -> None:
    src = inspect.getsource(_run_goldens_legacy).encode("utf-8")
    actual = hashlib.sha256(src).hexdigest()
    expected = SNAPSHOT.read_text().strip()
    assert actual == expected, (
        f"_run_goldens_legacy MUTATED — Phase 14 D-VEND-04 byte-identical lock VIOLATED.\n"
        f"  expected: {expected}\n  actual:   {actual}\n"
        f"  If the change is intentional, propose D-VEND-04 amendment in CONTEXT.md;"
        f" do not silently rewrite the snapshot."
    )
