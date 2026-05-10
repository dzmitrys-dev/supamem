"""Phase 17 Plan A — chunk-level recall metric + chunk_id derivation lock.

Locks (Req-05 + Req-06 + D-METRIC-03):
- Ingest payload carries a deterministic `chunk_id` field per chunk.
- Re-running ingest on identical text produces identical chunk_ids.
- Envelope emits BOTH `recall_at_k` (existing doc-level) AND `recall_at_k_chunk`
  (new) keys for every cell at k ∈ {1, 5, 10, 20}.
- `recall_at_k_chunk` is a pure set-ratio metric over chunk-id sets.
- `_build_run_chunk` does NOT dedup — distinct chunk_ids of the same doc_id
  all appear as separate keys (Pitfall 4 mitigation).
- `_build_run` (doc-level) output stays byte-identical for the same hit list
  (Req-06 floor preservation).
"""
from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest


# --- chunk_id formula reference (kept in sync with ingest.py) -----------------


def _expected_chunk_id(doc_id: str, chunk_text: str) -> str:
    return f"{doc_id}#{hashlib.sha1(chunk_text.encode()).hexdigest()[:12]}"


# --- helpers ------------------------------------------------------------------


def _hit(doc_id: str, chunk_id: str | None, score: float) -> SimpleNamespace:
    """Lightweight fake hit, mirroring tests/test_coderag_metrics.py style."""
    payload: dict = {"doc_id": doc_id, "file_path": doc_id}
    if chunk_id is not None:
        payload["chunk_id"] = chunk_id
    return SimpleNamespace(payload=payload, score=score)


# --- Task 1 RED tests ---------------------------------------------------------


def test_chunk_id_derivation_stable() -> None:
    """Same doc_id + same chunk_text → identical chunk_id across calls."""
    from supamem.eval.coderag.ingest import chunk_id_for  # type: ignore[attr-defined]

    a = chunk_id_for("docs/foo.md", "hello world")
    b = chunk_id_for("docs/foo.md", "hello world")
    assert a == b
    assert a == _expected_chunk_id("docs/foo.md", "hello world")


def test_chunk_id_format_is_doc_hash12() -> None:
    """chunk_id matches `<doc_id>#<12-hex>` per D-METRIC-03."""
    from supamem.eval.coderag.ingest import chunk_id_for  # type: ignore[attr-defined]

    cid = chunk_id_for("src/x/y.py", "def foo(): pass")
    assert re.fullmatch(r".+#[0-9a-f]{12}", cid) is not None


def test_envelope_has_both_keys() -> None:
    """Every column-cell of a sample envelope carries both doc-level and
    chunk-level recall keys at k ∈ {1, 5, 10, 20}.
    """
    from supamem.eval.coderag.report import (
        AXIS_NAMES,
        COLUMN_NAMES,
        METRIC_NAMES,
        column_metrics,
        envelope_from_results,
    )

    # METRIC_NAMES tuple itself must carry all four chunk-level recall siblings.
    for k in (1, 5, 10, 20):
        assert f"recall_at_{k}" in METRIC_NAMES
        assert f"recall_at_{k}_chunk" in METRIC_NAMES

    pytrec = {
        "recall_1": 0.1, "recall_5": 0.3, "recall_10": 0.5, "recall_20": 0.7,
        "recip_rank": 0.4, "ndcg_cut_10": 0.42,
    }
    chunk_recalls = {
        "recall_at_1_chunk": 0.05,
        "recall_at_5_chunk": 0.2,
        "recall_at_10_chunk": 0.35,
        "recall_at_20_chunk": 0.5,
    }
    cell = column_metrics(pytrec, 12.0, 30.0, chunk_recalls=chunk_recalls)
    assert cell is not None
    for k in (1, 5, 10, 20):
        assert f"recall_at_{k}" in cell
        assert f"recall_at_{k}_chunk" in cell

    # Build a full envelope and confirm every cell has both keys.
    per_axis = {axis: {col: cell for col in COLUMN_NAMES} for axis in AXIS_NAMES}
    env = envelope_from_results(per_axis)
    for axis in AXIS_NAMES:
        for col in COLUMN_NAMES:
            c = env["scores"][axis][col]
            if c is None:  # INV-A1 null cells are allowed
                continue
            for k in (1, 5, 10, 20):
                assert f"recall_at_{k}" in c
                assert f"recall_at_{k}_chunk" in c


def test_recall_at_k_chunk_pure_set_ratio() -> None:
    """gold={a,b,c}, retrieved=[a,x,b], k=2 → |{a}|/|{a,b,c}| = 1/3."""
    from supamem.eval.coderag.metrics import recall_at_k_chunk  # type: ignore[attr-defined]

    out = recall_at_k_chunk({"a", "b", "c"}, ["a", "x", "b"], 2)
    assert out == pytest.approx(1.0 / 3.0)


def test_recall_at_k_chunk_empty_gold_returns_zero() -> None:
    """D-METRIC-01 zero-guard: empty gold → 0.0 (no divide-by-zero)."""
    from supamem.eval.coderag.metrics import recall_at_k_chunk  # type: ignore[attr-defined]

    assert recall_at_k_chunk(set(), ["a"], 1) == 0.0


def test_build_run_chunk_does_not_dedup() -> None:
    """Two hits with same doc_id but distinct chunk_ids both appear as keys
    in the {qid: {chunk_id: score}} run dict (Pitfall 4 — runner.py was
    last-write-wins on doc_id, which collapses chunk-level signal).
    """
    from supamem.eval.coderag.runner import _build_run_chunk  # type: ignore[attr-defined]

    cid_a = _expected_chunk_id("docs/foo.md", "chunk one")
    cid_b = _expected_chunk_id("docs/foo.md", "chunk two")
    hits = [
        _hit("docs/foo.md", cid_a, 0.9),
        _hit("docs/foo.md", cid_b, 0.7),
    ]
    out = _build_run_chunk("q1", hits)
    assert "q1" in out
    inner = out["q1"]
    assert cid_a in inner
    assert cid_b in inner
    assert inner[cid_a] == pytest.approx(0.9)
    assert inner[cid_b] == pytest.approx(0.7)
    assert len(inner) == 2  # distinct chunks, no dedup


def test_build_run_doc_level_byte_identical() -> None:
    """The existing `_build_run` is unchanged for the same hit list — Req-06
    floor preservation. Two hits with the same doc_id collapse to one row
    (last-write-wins) per the existing contract.
    """
    from supamem.eval.coderag.runner import _build_run

    cid_a = _expected_chunk_id("docs/foo.md", "chunk one")
    cid_b = _expected_chunk_id("docs/foo.md", "chunk two")
    hits = [
        _hit("docs/foo.md", cid_a, 0.9),
        _hit("docs/foo.md", cid_b, 0.7),
        _hit("docs/bar.md", None, 0.5),
    ]
    out = _build_run("q1", hits)
    # last-write-wins on doc_id (existing contract)
    assert out == {"q1": {"docs/foo.md": pytest.approx(0.7), "docs/bar.md": pytest.approx(0.5)}}
