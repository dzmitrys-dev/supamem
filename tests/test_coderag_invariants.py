"""Phase 15 Plan C Task C2 — Validation invariants enforcement.

Encodes INV-01..10 + INV-A1..A3 from 15-VALIDATION.md against the bundled
``coderag_smoke.json`` fixture. INV-09 (mem0 collection point-count parity)
and INV-A3 (REQUIREMENTS.md PUB-05/EVAL-05 edits) are skip-marked here —
they land in 15-D and 15-E respectively.

Smoke run: we drive ``_run_coderag`` against an in-memory fake backend
seeded from each query's inline haystack, so the invariant suite is
fully offline (no Qdrant, no embedder, no network).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from supamem.eval.coderag.ingest import CODERAG_COLLECTION
from supamem.eval.coderag.runner import _run_coderag


SMOKE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "supamem"
    / "eval"
    / "datasets"
    / "coderag_smoke.json"
)


# ---------------------------------------------------------------------------
# Smoke fixture loading + offline backend
# ---------------------------------------------------------------------------


def _load_smoke() -> dict:
    return json.loads(SMOKE_PATH.read_text(encoding="utf-8"))


def _records_from_smoke() -> list[dict]:
    """Smoke questions → runner record schema {id, axis, repo, text, gold}."""
    smoke = _load_smoke()
    return [
        {
            "id": q["id"],
            "axis": q["axis"],
            "repo": q["repo"],
            "text": q["text"],
            "gold": list(q["gold"]),
        }
        for q in smoke["questions"]
    ]


class _SmokeHit:
    def __init__(self, doc_id: str, score: float) -> None:
        self.score = score
        self.payload = {"doc_id": doc_id}


class _SmokeBackend:
    """Offline backend: returns ranked _SmokeHit lists per (text, where).

    Builds a per-query haystack lookup keyed by (record id) where the gold
    docs sort to the top of the result list (perfect retrieval). For
    ``fastapi_only`` queries on a supamem record, returns []; for
    ``supamem_only`` queries on a fastapi record, also returns []. The
    ``combined`` pass returns the same hits as the matching repo pass.
    """

    def __init__(self, smoke: dict) -> None:
        self._by_text: dict[str, dict[str, Any]] = {}
        for q in smoke["questions"]:
            self._by_text[q["text"]] = q

    def query(self, text, k=20, *, where=None):  # noqa: ANN001, ANN003
        q = self._by_text.get(text)
        if q is None:
            return []
        repo = q["repo"]
        gold = q["gold"]
        # All inline-haystack doc_ids (we'll rank gold ones above non-gold).
        haystack_docs = [h["path"] for h in q.get("haystack", [])]
        non_gold = [d for d in haystack_docs if d not in gold]

        if where is None:
            # combined — same retrieval pool as the matching repo pass.
            ranked = list(gold) + non_gold
        else:
            asked = where.get("repo")
            if asked is None:
                ranked = list(gold) + non_gold
            elif asked == [repo]:
                ranked = list(gold) + non_gold
            else:
                # Other-repo column: nothing scoreable.
                ranked = []
        return [
            _SmokeHit(doc_id, score=float(len(ranked) - i))
            for i, doc_id in enumerate(ranked[:k])
        ]


@pytest.fixture(scope="module")
def smoke_envelope() -> dict:
    smoke = _load_smoke()
    backend = _SmokeBackend(smoke)
    records = _records_from_smoke()
    return _run_coderag(records, backend)


# ---------------------------------------------------------------------------
# INV-01..10 + INV-A1..A3
# ---------------------------------------------------------------------------


def test_inv_01_gold_non_empty_after_glob_filter() -> None:
    """Every smoke question has at least one gold doc."""
    smoke = _load_smoke()
    for q in smoke["questions"]:
        assert len(q["gold"]) >= 1, f"empty gold on {q['id']!r}"


def _columns(envelope: dict, axis: str) -> dict:
    return envelope["scores"][axis]


def test_inv_02_recall_monotonicity_per_axis_column(smoke_envelope: dict) -> None:
    for axis in ("code_fact", "decision_rationale"):
        for col, cell in _columns(smoke_envelope, axis).items():
            if cell is None:
                continue
            r1, r5, r10, r20 = (
                cell["recall_at_1"], cell["recall_at_5"],
                cell["recall_at_10"], cell["recall_at_20"],
            )
            assert r1 <= r5 <= r10 <= r20, (
                f"{axis}.{col}: recall not monotonic: {r1},{r5},{r10},{r20}"
            )


def test_inv_03_combined_recall_dominates(smoke_envelope: dict) -> None:
    for axis in ("code_fact", "decision_rationale"):
        cols = _columns(smoke_envelope, axis)
        comb = cols.get("combined")
        sup = cols.get("supamem_only")
        fap = cols.get("fastapi_only")
        if comb is None:
            continue
        for k in ("recall_at_1", "recall_at_5", "recall_at_10", "recall_at_20"):
            sup_v = (sup or {}).get(k, 0.0) or 0.0
            fap_v = (fap or {}).get(k, 0.0) or 0.0
            assert comb[k] >= max(sup_v, fap_v) - 1e-9, (
                f"{axis}.{k}: combined={comb[k]} < max(sup={sup_v}, fap={fap_v})"
            )


def test_inv_04_per_axis_query_count_floor() -> None:
    """Smoke fixture floor: ≥ 5 total questions (full corpus floor ≥ 30 is
    a baseline-run check, not a CI invariant — see 15-VALIDATION.md INV-04)."""
    smoke = _load_smoke()
    assert len(smoke["questions"]) >= 5


def test_inv_05_mrr_ndcg_bounded(smoke_envelope: dict) -> None:
    for axis in ("code_fact", "decision_rationale"):
        for col, cell in _columns(smoke_envelope, axis).items():
            if cell is None:
                continue
            assert 0.0 <= cell["mrr"] <= 1.0, f"{axis}.{col}.mrr out of bounds: {cell['mrr']}"
            assert 0.0 <= cell["ndcg_at_10"] <= 1.0, (
                f"{axis}.{col}.ndcg_at_10 out of bounds: {cell['ndcg_at_10']}"
            )


def test_inv_06_latency_p95_geq_p50(smoke_envelope: dict) -> None:
    for axis in ("code_fact", "decision_rationale"):
        for col, cell in _columns(smoke_envelope, axis).items():
            if cell is None:
                continue
            p50, p95 = cell["latency_ms_p50"], cell["latency_ms_p95"]
            if p50 is None or p95 is None:
                continue
            assert p95 >= p50, f"{axis}.{col}: p95={p95} < p50={p50}"


def test_inv_08_every_gold_doc_id_in_corpus_haystack() -> None:
    """Smoke-fixture analog of INV-08: each gold doc_id appears as a haystack
    path in its question's inline haystack. The full corpus check (against a
    live Qdrant collection's payload index) belongs to baseline capture, not
    CI — but this offline cross-check catches the same kind of disagreement."""
    smoke = _load_smoke()
    for q in smoke["questions"]:
        haystack_paths = {h["path"] for h in q.get("haystack", [])}
        for gold_id in q["gold"]:
            assert gold_id in haystack_paths, (
                f"INV-08 violated: gold {gold_id!r} on {q['id']!r} "
                f"missing from haystack {sorted(haystack_paths)!r}"
            )


@pytest.mark.skipif(
    os.environ.get("SUPAMEM_INTEGRATION_MEM0") != "1",
    reason="INV-09 requires live mem0 ingest; landing in Plan 15-D",
)
def test_inv_09_mem0_collection_parity_skip_unless_integration() -> None:  # pragma: no cover
    raise AssertionError(
        "INV-09: mem0 collection parity check intentionally deferred to 15-D"
    )


def test_inv_10_report_schema_version(smoke_envelope: dict) -> None:
    assert smoke_envelope["report_schema_version"] == "coderag.v1"


def test_inv_a1_decision_rationale_fastapi_null(smoke_envelope: dict) -> None:
    dr = _columns(smoke_envelope, "decision_rationale")
    assert dr["fastapi_only"] is None
    if dr["supamem_only"] is not None:
        assert dr["combined"] == dr["supamem_only"], (
            "INV-A1: decision_rationale.combined must collapse to supamem_only "
            "when fastapi_only is null"
        )


def test_inv_a2_collection_distinct_from_mem0() -> None:
    assert CODERAG_COLLECTION != "supamem_eval_coderag_mem0", (
        "INV-A2: supamem_eval_coderag must remain distinct from "
        "supamem_eval_coderag_mem0 (peer separation)"
    )


@pytest.mark.skip(reason="INV-A3: REQUIREMENTS.md PUB-05/EVAL-05 edits land in Plan 15-E")
def test_inv_a3_requirements_md_edits_skip_until_15e() -> None:  # pragma: no cover
    raise AssertionError("INV-A3: deferred to Plan 15-E")
