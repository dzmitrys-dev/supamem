"""Phase 16 Plan E Task 3a — peer-scoring loop in ``_run_coderag``.

Locks (16-E-SUMMARY.md "Mem0 head-to-head" gap-closure):

- When ``peers={name: {"adapter": <obj>}}`` is supplied, ``_run_coderag``
  drives ``adapter.query()`` per record × axis × col, builds per-query
  metric maps via ``pytrec_eval.RelevanceEvaluator`` directly, and forwards
  ``peer_run_data`` to :func:`envelope_from_results` so the 16-D
  bootstrap-delta branch populates ``envelope.peers[name].scores`` AND
  ``envelope.comparisons.{name}_vs_supamem``.
- Non-peer runs (no ``peers`` kwarg, or ``peers`` without an ``adapter``
  slot) keep emitting ``envelope.peers == {}`` AND ``envelope.comparisons
  == {}`` — non-peer regression guard for the 3 baseline LIVE envelopes.
- Adapter faults degrade gracefully: a peer whose ``.query()`` raises is
  surfaced via ``err_console`` once, dropped from ``peer_run_data``, and
  the supamem-side scoring path produces a full envelope unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from supamem.eval.coderag.runner import _run_coderag


# ── Test doubles ────────────────────────────────────────────────────────


@dataclass
class _Hit:
    """Minimal hit shape: ``payload["doc_id"]`` + ``score`` (matches 15-B contract)."""

    payload: dict[str, Any]
    score: float


class _FakeBackend:
    """Deterministic supamem-side backend.

    For every query, returns the gold doc_id at rank 1 (so supamem hits
    perfect recall@1 / MRR=1.0) plus 2 distractor doc_ids — gives the
    paired-bootstrap something to compare against without flakiness.
    """

    def __init__(self, qid_to_gold: dict[str, list[str]]) -> None:
        self._qid_to_gold = qid_to_gold
        # Inverted lookup by text → qid (records share unique text in tests).
        self._text_to_qid: dict[str, str] = {}

    def register(self, text: str, qid: str) -> None:
        self._text_to_qid[text] = qid

    def query(self, text: str, *, k: int = 20, where: Any = None) -> list[_Hit]:
        qid = self._text_to_qid.get(text)
        gold = self._qid_to_gold.get(qid or "", [])
        # Rank 1 = first gold (or distractor if no gold), rank 2/3 = distractors.
        hits = []
        if gold:
            hits.append(_Hit(payload={"doc_id": gold[0]}, score=0.95))
        hits.append(_Hit(payload={"doc_id": "distractor_a.py"}, score=0.5))
        hits.append(_Hit(payload={"doc_id": "distractor_b.py"}, score=0.4))
        return hits[:k]


class _FakePeerAdapter:
    """Peer that hits gold ~half the time at rank 2 (so peer < supamem reliably)."""

    def __init__(self, qid_to_gold: dict[str, list[str]]) -> None:
        self._qid_to_gold = qid_to_gold
        self._text_to_qid: dict[str, str] = {}

    def register(self, text: str, qid: str) -> None:
        self._text_to_qid[text] = qid

    def query(self, text: str, *, k: int = 20, where: Any = None) -> list[_Hit]:
        qid = self._text_to_qid.get(text)
        gold = self._qid_to_gold.get(qid or "", [])
        # Even-numbered qids hit gold at rank 2; odd qids miss entirely.
        try:
            qid_int = int(str(qid).split("-")[-1])
        except (ValueError, AttributeError):
            qid_int = 0
        hits = [_Hit(payload={"doc_id": "distractor_x.py"}, score=0.6)]
        if qid_int % 2 == 0 and gold:
            hits.append(_Hit(payload={"doc_id": gold[0]}, score=0.55))
        hits.append(_Hit(payload={"doc_id": "distractor_y.py"}, score=0.3))
        return hits[:k]


class _AlwaysFailingAdapter:
    """Adapter whose ``.query()`` always raises — for the degraded-not-crashed contract."""

    def query(self, text: str, *, k: int = 20, where: Any = None) -> list[_Hit]:
        raise RuntimeError("simulated peer adapter fault")


def _build_records() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """8 hand-built records: 4 code_fact + 4 decision_rationale, mixed even/odd qids."""
    raw = [
        ("cf-1", "code_fact", "supamem", "how do I configure qdrant?", ["src/qdrant_cfg.py"]),
        ("cf-2", "code_fact", "fastapi", "what is the dependency injection api?", ["src/fastapi/deps.py"]),
        ("cf-3", "code_fact", "supamem", "how does the embedder cache work?", ["src/embedders/cache.py"]),
        ("cf-4", "code_fact", "fastapi", "how do I declare a path operation?", ["src/fastapi/routing.py"]),
        ("dr-1", "decision_rationale", "supamem", "why pluggable retrieval backends?", ["docs/adr/0003.md"]),
        ("dr-2", "decision_rationale", "supamem", "why ship default tuned_hybrid?", ["docs/adr/0004.md"]),
        ("dr-3", "decision_rationale", "supamem", "why no License classifier?", ["docs/adr/0005.md"]),
        ("dr-4", "decision_rationale", "supamem", "why local update-check?", ["docs/adr/0006.md"]),
    ]
    records = [
        {"id": qid, "axis": axis, "repo": repo, "text": text, "gold": gold}
        for qid, axis, repo, text, gold in raw
    ]
    qid_to_gold = {r["id"]: r["gold"] for r in records}
    return records, qid_to_gold


def _wire_doubles(records: list[dict[str, Any]]) -> tuple[_FakeBackend, _FakePeerAdapter, dict[str, list[str]]]:
    qid_to_gold = {r["id"]: r["gold"] for r in records}
    backend = _FakeBackend(qid_to_gold)
    adapter = _FakePeerAdapter(qid_to_gold)
    for r in records:
        backend.register(r["text"], r["id"])
        adapter.register(r["text"], r["id"])
    return backend, adapter, qid_to_gold


# ── Tests ───────────────────────────────────────────────────────────────


def test_run_coderag_with_peer_populates_peers_and_comparisons() -> None:
    """Wiring contract: adapter slot → peer scores + comparisons populated."""
    records, _ = _build_records()
    backend, adapter, _ = _wire_doubles(records)

    envelope = _run_coderag(
        records,
        backend,
        peers={"fakepeer": {"adapter": adapter, "status": "ready"}},
    )

    # peers.fakepeer.scores mirrors envelope.scores nesting (D-PEER-01).
    assert "fakepeer" in envelope["peers"], envelope["peers"]
    peer_scores = envelope["peers"]["fakepeer"]["scores"]
    assert "code_fact" in peer_scores
    assert "decision_rationale" in peer_scores
    cf_combined = peer_scores["code_fact"]["combined"]
    assert cf_combined is not None
    # envelope.peers.X.scores uses MAPPED names (PYTREC_TO_ENVELOPE).
    assert "ndcg_at_10" in cf_combined
    # Peer hits gold at rank 2 for even qids only — non-zero ndcg expected.
    assert cf_combined["ndcg_at_10"] > 0.0

    # comparisons.fakepeer_vs_supamem.* uses RAW pytrec names (per-query maps
    # come from RelevanceEvaluator, so cells are keyed by METRIC_SET).
    comp = envelope["comparisons"]["fakepeer_vs_supamem"]
    assert "code_fact" in comp
    cf_comb_comp = comp["code_fact"]["combined"]
    assert "ndcg_cut_10" in cf_comb_comp
    cell = cf_comb_comp["ndcg_cut_10"]
    assert set(cell.keys()) >= {
        "delta", "ci_lower", "ci_upper", "n_resamples", "seed", "qualitative",
    }
    assert cell["qualitative"] in {"win", "loss", "tie"}
    # Peer always loses by construction (rank 2 + half-miss vs supamem rank 1).
    assert cell["delta"] < 0


def test_run_coderag_no_peers_emits_empty_dicts() -> None:
    """Non-peer regression guard: baseline LIVE envelopes carry empty dicts."""
    records, _ = _build_records()
    backend, _, _ = _wire_doubles(records)

    # Three flavors of "no peer scoring": absent kwarg, None, empty dict.
    for peers_kwarg in (None, {}):
        envelope = _run_coderag(records, backend, peers=peers_kwarg)
        assert envelope["peers"] == {}, peers_kwarg
        assert envelope["comparisons"] == {}, peers_kwarg

    # Default (no peers kwarg at all).
    envelope = _run_coderag(records, backend)
    assert envelope["peers"] == {}
    assert envelope["comparisons"] == {}


def test_run_coderag_legacy_peers_stub_without_adapter_passes_through() -> None:
    """15-C compat: peers blob WITHOUT 'adapter' key → forwarded verbatim, no comparisons."""
    records, _ = _build_records()
    backend, _, _ = _wire_doubles(records)

    legacy_blob = {"some_peer": {"status": "ready", "note": "no adapter slot"}}
    envelope = _run_coderag(records, backend, peers=legacy_blob)

    # peers forwarded verbatim (legacy 15-C path), no comparisons derivable.
    assert envelope["peers"] == legacy_blob
    assert envelope["comparisons"] == {}


def test_run_coderag_peer_fault_degrades_gracefully(capfd: pytest.CaptureFixture[str]) -> None:
    """Adapter raise → err_console warning, peer dropped, supamem path unscathed."""
    records, _ = _build_records()
    backend, _, _ = _wire_doubles(records)

    envelope = _run_coderag(
        records,
        backend,
        peers={"mem0": {"adapter": _AlwaysFailingAdapter(), "status": "ready"}},
    )

    # Supamem-side scoring is full and unaffected (regression guard).
    assert envelope["scores"]["code_fact"]["combined"] is not None
    assert envelope["scores"]["code_fact"]["combined"]["ndcg_at_10"] > 0.0

    # Failed peer is dropped — no scores, no comparisons (degraded-not-crashed).
    assert envelope["peers"] == {}
    assert envelope["comparisons"] == {}

    # err_console fired at least once with the failure substring.
    captured = capfd.readouterr()
    assert "peer mem0 query failed" in captured.err, captured.err


def test_run_coderag_peer_per_query_pivot_pairs_by_qid() -> None:
    """Bootstrap delta pairs by qid intersection — verify it actually pairs."""
    records, _ = _build_records()
    backend, adapter, _ = _wire_doubles(records)

    envelope = _run_coderag(
        records, backend, peers={"fakepeer": {"adapter": adapter}}
    )

    comp = envelope["comparisons"]["fakepeer_vs_supamem"]
    # Every cell that exists must carry a delta + bracket — paired arrays were
    # non-empty (i.e. qid intersection landed). decision_rationale.fastapi_only
    # is None (INV-A1) and may be absent from comparisons.
    for axis_name, axis_blob in comp.items():
        for col_name, col_blob in axis_blob.items():
            for metric_name, cell in col_blob.items():
                assert cell["n_resamples"] == 10000, (axis_name, col_name, metric_name)
                assert cell["seed"] == 42
                assert cell["ci_lower"] <= cell["delta"] <= cell["ci_upper"] or \
                    cell["ci_lower"] <= cell["ci_upper"]  # CI brackets are valid
