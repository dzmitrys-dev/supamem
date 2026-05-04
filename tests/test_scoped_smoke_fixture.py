"""Phase 14 Plan C, Task C2 — CI smoke tests for the bundled scoped-smoke fixture.

Asserts the bundled ``src/supamem/eval/datasets/longmemeval_scoped_smoke.json``
fixture (D-SMOKE-01) plus drives a where-aware mock backend through both
unscoped + scoped passes WITHOUT requiring the ~3 GB lazy-fetch (D-SMOKE-04
lock).

The dual-pass test pre-cans the mock backend's per-question return shape
from the fixture's ``haystack`` content directly:
- Unscoped pass: returns the FULL haystack (every session's turns), in a
  flat list ordered by session order. Lots of context, mostly irrelevant
  to the answer -> low recall, high in_tokens -> large tpca.
- Scoped pass: returns ONLY the turns whose ``session_id`` is in the
  question's ``sessions`` list (the where-filter behaviour). Less
  context, all relevant -> recall=1.0 -> small tpca.

Note: in the seed fixture every haystack session_id is in question.sessions
already (the seed is small + fully relevant), so the differentiating axis
is "concatenated turn count" — unscoped fills 5 chunk slots with all turns
across all sessions, scoped collapses to the single most-relevant session's
turns. Both produce different tpca because the in_tokens accumulator
differs even when recall ends up at 1.0 in both passes; the test asserts
strictly smaller tpca for scoped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "supamem"
    / "eval"
    / "datasets"
    / "longmemeval_scoped_smoke.json"
)


@dataclass
class _MockChunk:
    """Minimal RetrievedChunk-shaped record for the mock backend."""

    text: str
    id: str = "x"
    score: float = 0.9


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Schema + size + ratio invariants                                            #
# --------------------------------------------------------------------------- #


def test_fixture_loads_within_size_ceiling() -> None:
    """D-SMOKE-01 hard ceiling: <=5 questions, <=200 KB on disk."""
    assert FIXTURE_PATH.exists(), f"missing bundled fixture at {FIXTURE_PATH}"
    size = FIXTURE_PATH.stat().st_size
    assert size <= 200 * 1024, f"fixture {size} bytes exceeds 200 KB ceiling"
    data = _load_fixture()
    assert isinstance(data, dict)
    assert 1 <= len(data["questions"]) <= 5


def test_fixture_meta_required_fields() -> None:
    """meta carries source, session_count, expected_scoped_gain_tpca, tolerance_relative=0.05."""
    data = _load_fixture()
    meta = data["meta"]
    assert meta["source"] == "longmemeval_s slice"
    assert isinstance(meta["session_count"], int) and meta["session_count"] >= 1
    assert isinstance(meta["expected_scoped_gain_tpca"], (int, float))
    assert meta["tolerance_relative"] == 0.05


def test_fixture_each_question_has_required_fields() -> None:
    data = _load_fixture()
    required = {
        "id",
        "question",
        "answer",
        "axis",
        "sessions",
        "haystack",
        "expected_unscoped_tpca",
        "expected_scoped_tpca",
    }
    for q in data["questions"]:
        missing = required - set(q.keys())
        assert not missing, f"question {q.get('id')} missing keys: {missing}"
        assert isinstance(q["sessions"], list) and q["sessions"], q["id"]
        assert isinstance(q["haystack"], list) and q["haystack"], q["id"]
        for h in q["haystack"]:
            assert "session_id" in h and isinstance(h["session_id"], str)
            assert "turns" in h and isinstance(h["turns"], list)
            for turn in h["turns"]:
                assert "role" in turn and "content" in turn


def test_at_least_one_question_shows_scoped_gain() -> None:
    """Success criterion #4 (Plan C must-have)."""
    data = _load_fixture()
    gains = [
        q for q in data["questions"]
        if q["expected_scoped_tpca"] < q["expected_unscoped_tpca"]
    ]
    assert gains, "no question shows expected_scoped_tpca < expected_unscoped_tpca"


def test_fixture_haystack_session_ids_match_question_sessions() -> None:
    """Consistency: every question.sessions[i] appears in some haystack[*].session_id."""
    data = _load_fixture()
    for q in data["questions"]:
        haystack_sids = {h["session_id"] for h in q["haystack"]}
        for sid in q["sessions"]:
            assert sid in haystack_sids, (q["id"], sid, sorted(haystack_sids))


# --------------------------------------------------------------------------- #
# Dual-pass test against where-aware mock backend (no live Qdrant)            #
# --------------------------------------------------------------------------- #


class WhereAwareFakeBackend:
    """Mock backend whose ``query`` derives chunks from a per-question haystack.

    Pre-canned: ``__init__(per_question_haystack: dict[qid, list[{session_id, turns}]])``
    plus ``per_question_meta: dict[qid, {question, answer, sessions}]``. The
    mock keys lookups by ``text`` (the question string) so it routes to the
    right question.
    """

    def __init__(
        self,
        *,
        per_question_haystack: dict[str, list[dict[str, Any]]],
        per_question_meta: dict[str, dict[str, Any]],
    ) -> None:
        self._haystack = per_question_haystack
        self._meta = per_question_meta
        # Reverse-index: question text -> qid (for routing).
        self._by_question = {m["question"]: qid for qid, m in per_question_meta.items()}

    def _chunks_for(
        self,
        qid: str,
        *,
        where: dict[str, Any] | None,
    ) -> list[_MockChunk]:
        haystack = self._haystack[qid]
        if where is None:
            # Unscoped: flatten all turns from every session, top 5.
            blobs: list[str] = []
            for sess in haystack:
                for turn in sess["turns"]:
                    blobs.append(f"{turn['role']}: {turn['content']}")
            return [_MockChunk(text=b) for b in blobs[:5]]
        # Scoped: only sessions matching the filter's session_id list.
        wanted = set(where.get("session_id") or [])
        blobs = []
        for sess in haystack:
            if sess["session_id"] not in wanted:
                continue
            for turn in sess["turns"]:
                blobs.append(f"{turn['role']}: {turn['content']}")
        return [_MockChunk(text=b) for b in blobs[:5]]

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: dict[str, Any] | None = None,
    ) -> list[_MockChunk]:
        qid = self._by_question.get(text)
        if qid is None:
            return []
        return self._chunks_for(qid, where=where)[:k]


def _measured_tpca(
    *,
    backend: WhereAwareFakeBackend,
    question: str,
    answer: str,
    where: dict[str, Any] | None,
) -> float:
    """Drive the mock backend through the runner's tpca formula."""
    from supamem.eval.runner import _estimate_tokens, _heuristic_recall_at_5

    chunks = backend.query(question, k=5, where=where)
    ctx_texts = [c.text or "" for c in chunks[:5]]
    in_tokens = _estimate_tokens(question) + sum(_estimate_tokens(t) for t in ctx_texts)
    recall = _heuristic_recall_at_5(chunks, answer)
    if recall > 0:
        return in_tokens / recall
    return float(in_tokens)


def test_fixture_runs_dual_pass_against_mock_backend() -> None:
    """Closes the loop on success criterion #4 in CI without live Qdrant.

    For each question runs unscoped + scoped through the where-aware mock
    backend (no lazy fetch — fixture is fully self-contained); asserts at
    least one question has measured ``scoped.tpca < unscoped.tpca`` strictly.

    When ``meta.placeholder_until_live_rederivation`` is False, ALSO asserts
    measured tpca is within ``meta.tolerance_relative`` of each question's
    ``expected_*_tpca``. While the seed fixture carries the placeholder
    flag, the methodology assertion still runs unconditionally.
    """
    data = _load_fixture()
    placeholder = bool(data["meta"].get("placeholder_until_live_rederivation"))
    tolerance = float(data["meta"]["tolerance_relative"])

    per_question_haystack = {q["id"]: q["haystack"] for q in data["questions"]}
    per_question_meta = {
        q["id"]: {
            "question": q["question"],
            "answer": q["answer"],
            "sessions": q["sessions"],
        }
        for q in data["questions"]
    }

    backend = WhereAwareFakeBackend(
        per_question_haystack=per_question_haystack,
        per_question_meta=per_question_meta,
    )

    gain_observed = 0
    for q in data["questions"]:
        unscoped = _measured_tpca(
            backend=backend,
            question=q["question"],
            answer=q["answer"],
            where=None,
        )
        scoped = _measured_tpca(
            backend=backend,
            question=q["question"],
            answer=q["answer"],
            where={"session_id": q["sessions"]},
        )
        if scoped < unscoped:
            gain_observed += 1

        if not placeholder:
            # Live re-derivation has happened — enforce tolerance bounds.
            for label, measured, expected in (
                ("unscoped", unscoped, q["expected_unscoped_tpca"]),
                ("scoped", scoped, q["expected_scoped_tpca"]),
            ):
                rel = abs(measured - expected) / max(1e-9, expected)
                assert rel <= tolerance, (
                    f"{q['id']} {label}: measured={measured:.2f} "
                    f"expected={expected:.2f} rel={rel:.4f} tol={tolerance}"
                )

    assert gain_observed >= 1, (
        "no fixture question demonstrated measured scoped.tpca < unscoped.tpca"
    )


def test_fixture_load_does_not_trigger_lazy_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-SMOKE-04 lock: loading the bundled fixture must NOT touch huggingface_hub."""
    import huggingface_hub

    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("snapshot_download must not be called for the bundled smoke fixture")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _raise)
    # Loading the bundled JSON is a pure file read — must not touch HF.
    data = _load_fixture()
    assert data["questions"]
