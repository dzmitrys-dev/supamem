"""Tests for retrieval/filters.py temporal extension + transcript-decay block.

Phase 9 RED stubs (Wave 0). Turn GREEN in Plan 02 (filter + config) and
Plan 04 (decay block). Every test below encodes a TEMP-02 or TEMP-03 success
criterion verbatim.

Decision references (see .planning/phases/09-per-source-temporal-validity):
- D-FILTER-01..03: always-on temporal clause via ``build_qdrant_filter``;
  no user-facing disable knob; internal ``temporal=False`` for diagnostics only.
- D-NULL-01 (CORRECTED → IsEmpty per RESEARCH §R-1): use
  ``IsEmptyCondition`` not ``IsNullCondition`` because IsNull does NOT match
  missing payload fields in pinned Qdrant client (verified Qdrant#5342).
- D-COMPOSE-09: transcript decay applied AFTER rerank (or after T-4 when
  rerank off), as a transcript-only post-multiplier; code/ADR/doc rankings
  invariant under flag flips (success criterion #3).
- D-DECAY-01: ``score *= alpha + (1 - alpha) * 0.5 ** (age_days / hl)``
  with locked defaults α=0.7, hl=14d.
- D-DECAY-02: age sourced from ``payload.valid_from`` (ISO-8601 UTC).
- D-CONFIG-02: Pydantic validators reject α∉[0,1], hl≤0, retention<0
  with ``SystemExit(2)`` at ``ResolvedConfig.from_paths()`` boot.

Pitfall references:
- Pitfall 1 (RESEARCH §R-1): IsNullCondition silently filters out every legacy
  pre-Phase-9 point because missing != null in Qdrant. ``test_uses_isempty_not_isnull``
  encodes the negative assertion.
- Pitfall 2: code rankings MUST be byte-identical knob-on vs knob-off
  (``test_decay_off_byte_identical_code_ranking``).
- Threat-V7: malformed ``valid_from`` string MUST NOT crash retrieval; keep
  raw score (``test_malformed_valid_from_skips_decay_keeps_score``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from qdrant_client.http import models as qmodels

from supamem.retrieval.filters import build_qdrant_filter
from tests.fixtures.temporal_mixed_corpus import mixed_corpus

# ─────────────────────────────────────────────────────────────────────────────
# TEMP-02 — filter contract (build_qdrant_filter temporal extension)
# ─────────────────────────────────────────────────────────────────────────────


def _walk(node):
    """Yield every nested condition / sub-filter under a Filter root."""
    yield node
    for attr in ("must", "should", "must_not"):
        children = getattr(node, attr, None) or []
        for c in children:
            yield from _walk(c)


def test_temporal_filter_default_on():
    """D-FILTER-01: temporal clause is always-on (default ``temporal=True``).

    Even with ``where=None``, the function must produce a Filter wrapping
    a nested OR-clause: ``IsEmptyCondition(valid_to) ∨ DatetimeRange(gt=now)``.
    """
    flt = build_qdrant_filter(None)
    assert flt is not None, "temporal=True default must produce a filter even when where=None"
    assert flt.must is not None and len(flt.must) == 1
    nested = flt.must[0]
    assert isinstance(nested, qmodels.Filter)
    assert nested.should is not None and len(nested.should) == 2
    # First branch: IsEmpty(valid_to) — matches missing AND explicit null.
    assert isinstance(nested.should[0], qmodels.IsEmptyCondition)
    assert nested.should[0].is_empty.key == "valid_to"
    # Second branch: valid_to > now (future-valid points).
    fc = nested.should[1]
    assert isinstance(fc, qmodels.FieldCondition)
    assert fc.key == "valid_to"
    assert fc.range is not None
    assert isinstance(fc.range, qmodels.DatetimeRange)
    assert fc.range.gt is not None


def test_temporal_filter_with_where():
    """D-COMPOSE: temporal clause AND-ed with the caller's where clauses."""
    flt = build_qdrant_filter({"room": "backend"})
    assert flt is not None
    assert flt.must is not None and len(flt.must) == 2
    # Order: temporal first, then where clauses (insertion-stable).
    assert isinstance(flt.must[0], qmodels.Filter)  # nested temporal sub-filter
    assert isinstance(flt.must[1], qmodels.FieldCondition)
    assert flt.must[1].key == "room"
    assert flt.must[1].match.value == "backend"


def test_temporal_disabled_returns_none():
    """D-FILTER-02: ``temporal=False`` + no where → None (diagnostics fast path)."""
    assert build_qdrant_filter(None, temporal=False) is None


def test_temporal_disabled_with_where():
    """D-FILTER-02: ``temporal=False`` with where → ONLY where clause (no temporal)."""
    flt = build_qdrant_filter({"room": "backend"}, temporal=False)
    assert flt is not None
    assert flt.must is not None and len(flt.must) == 1
    cond = flt.must[0]
    assert isinstance(cond, qmodels.FieldCondition)
    assert cond.key == "room"


def test_now_injection():
    """``now=`` kwarg pins the cutoff for deterministic tests."""
    fixed = datetime(2025, 1, 1, tzinfo=timezone.utc)
    flt = build_qdrant_filter(None, now=fixed)
    fc = flt.must[0].should[1]
    assert fc.range.gt == fixed.isoformat()


def test_uses_isempty_not_isnull():
    """Pitfall 1 (RESEARCH §R-1): IsNullCondition does NOT match missing fields.

    Walk the entire filter tree and assert NO IsNullCondition appears.
    A regression to IsNull would silently filter out every legacy
    pre-Phase-9 point (Qdrant#5342) and break TEMP-02.
    """
    flt = build_qdrant_filter(None)
    assert flt is not None
    # Confirm IsEmpty is present at least once.
    nodes = list(_walk(flt))
    assert any(isinstance(n, qmodels.IsEmptyCondition) for n in nodes), (
        "Temporal sub-filter must use IsEmptyCondition for live-chunk detection"
    )
    # Confirm IsNull does NOT appear anywhere.
    for n in nodes:
        assert not isinstance(n, qmodels.IsNullCondition), (
            "IsNullCondition does not match missing fields (Qdrant#5342) — "
            "must use IsEmptyCondition (D-NULL-01 corrected per RESEARCH §R-1)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TEMP-03 — transcript-only decay block (post-rerank, pre-T5 dedup)
# ─────────────────────────────────────────────────────────────────────────────


def test_decay_multiplier_values():
    """D-DECAY-01 worked example: locked α=0.7, hl=14d.

    age=0  → 1.0
    age=14 → 0.85
    age=28 → 0.775

    The exact symbol Plan 04 exposes for the decay math is TBD — until then,
    importorskip keeps collection clean. Once Plan 04 lands a helper named
    ``_decay_multiplier`` (or inlines the formula), update the import.
    """
    pytest.importorskip(
        "supamem.retrieval.tuned_hybrid",
        reason="Plan 04 lands the transcript-decay block / helper",
    )
    try:
        from supamem.retrieval.tuned_hybrid import _decay_multiplier  # type: ignore[attr-defined]
    except ImportError:
        pytest.skip("Plan 04 lands _decay_multiplier (or inlines the formula)")
    assert _decay_multiplier(age_days=0, alpha=0.7, half_life_days=14.0) == pytest.approx(1.0)
    assert _decay_multiplier(age_days=14, alpha=0.7, half_life_days=14.0) == pytest.approx(0.85)
    assert _decay_multiplier(age_days=28, alpha=0.7, half_life_days=14.0) == pytest.approx(0.775)


@pytest.mark.skip(reason="GREEN in Plan 04 — wires _apply_transcript_decay into query()")
def test_decay_off_byte_identical_code_ranking():
    """Pitfall 2 mitigation + success criterion #3.

    Run the post-rerank decay block over ``mixed_corpus()`` with
    ``decay_enabled=False`` then ``decay_enabled=True``; the score sequence
    of the three ``markdown_header`` chunks MUST be byte-identical across
    both runs. Only ``transcript`` chunks may shift.
    """
    from supamem.retrieval.tuned_hybrid import _apply_transcript_decay  # type: ignore[attr-defined]

    corpus_off = mixed_corpus()
    corpus_on = mixed_corpus()
    _apply_transcript_decay(corpus_off, enabled=False, alpha=0.7, half_life_days=14.0)
    _apply_transcript_decay(corpus_on, enabled=True, alpha=0.7, half_life_days=14.0)
    code_off = [(d, s) for d, s, p, _ in corpus_off if p["chunker"] == "markdown_header"]
    code_on = [(d, s) for d, s, p, _ in corpus_on if p["chunker"] == "markdown_header"]
    assert code_off == code_on, "code/ADR/doc ranking MUST be invariant under decay flag flips"


@pytest.mark.skip(reason="GREEN in Plan 04 — wires _apply_transcript_decay into query()")
def test_decay_on_only_transcripts_affected():
    """D-COMPOSE-09: only ``chunker == 'transcript'`` payloads get the multiplier.

    With ``decay_enabled=True``, transcript scores at ages > 0 are STRICTLY
    LESS than their pre-decay values; code chunks are untouched.
    """
    from supamem.retrieval.tuned_hybrid import _apply_transcript_decay  # type: ignore[attr-defined]

    before = mixed_corpus()
    after = mixed_corpus()
    _apply_transcript_decay(after, enabled=True, alpha=0.7, half_life_days=14.0)
    by_id_before = {d: s for d, s, _, _ in before}
    for doc_id, score, payload, _ in after:
        if payload["chunker"] == "transcript":
            # age 0 transcript multiplier is 1.0 (no change); ages 7d and 28d strictly less.
            if "transcript-0" in doc_id:
                assert score == pytest.approx(by_id_before[doc_id])
            else:
                assert score < by_id_before[doc_id]
        else:
            assert score == pytest.approx(by_id_before[doc_id]), (
                "non-transcript chunks must be byte-identical"
            )


@pytest.mark.skip(reason="GREEN in Plan 04 — wires _apply_transcript_decay into query()")
def test_malformed_valid_from_skips_decay_keeps_score():
    """Threat-V7 mitigation: malformed ``valid_from`` → keep raw score, never crash.

    A payload with a non-ISO ``valid_from`` string must not raise; the chunk
    keeps its pre-decay score (try/except on ``datetime.fromisoformat``).
    """
    from supamem.retrieval.tuned_hybrid import _apply_transcript_decay  # type: ignore[attr-defined]

    rows: list[tuple[str, float, dict, list[float] | None]] = [
        ("transcript-bad", 0.5, {"chunker": "transcript", "valid_from": "not-an-iso"}, None),
    ]
    _apply_transcript_decay(rows, enabled=True, alpha=0.7, half_life_days=14.0)
    assert rows[0][1] == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# D-CONFIG-02 — Pydantic validators (boot-time fail-closed)
# ─────────────────────────────────────────────────────────────────────────────


def _write_temporal_toml(
    tmp_path,
    *,
    alpha: float = 0.7,
    half_life_days: float = 14.0,
    retention_days: int = 90,
    decay_enabled: bool = True,
):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[supamem]
qdrant_url = "http://localhost:6333"
collection = "x"

[supamem.recency.per_source.transcript]
enabled = {str(decay_enabled).lower()}
half_life_days = {half_life_days}
alpha = {alpha}

[supamem.temporal]
retention_days = {retention_days}
""".lstrip()
    )
    return cfg_path


@pytest.mark.skip(reason="GREEN in Plan 02 — Pydantic validators wired in config.py")
def test_alpha_out_of_range_rejected(tmp_path, monkeypatch):
    """D-CONFIG-02: α > 1 → SystemExit(2) at boot."""
    from supamem.config import load_config

    cfg_path = _write_temporal_toml(tmp_path, alpha=1.5)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        load_config(explicit_path=cfg_path)
    assert excinfo.value.code == 2


@pytest.mark.skip(reason="GREEN in Plan 02 — Pydantic validators wired in config.py")
def test_alpha_negative_rejected(tmp_path, monkeypatch):
    """D-CONFIG-02: α < 0 → SystemExit(2) at boot."""
    from supamem.config import load_config

    cfg_path = _write_temporal_toml(tmp_path, alpha=-0.1)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        load_config(explicit_path=cfg_path)
    assert excinfo.value.code == 2


@pytest.mark.skip(reason="GREEN in Plan 02 — Pydantic validators wired in config.py")
def test_half_life_zero_rejected(tmp_path, monkeypatch):
    """D-CONFIG-02: hl == 0 → SystemExit(2) (division-by-zero guard at boot)."""
    from supamem.config import load_config

    cfg_path = _write_temporal_toml(tmp_path, half_life_days=0.0)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        load_config(explicit_path=cfg_path)
    assert excinfo.value.code == 2


@pytest.mark.skip(reason="GREEN in Plan 02 — Pydantic validators wired in config.py")
def test_half_life_negative_rejected(tmp_path, monkeypatch):
    """D-CONFIG-02: hl < 0 → SystemExit(2)."""
    from supamem.config import load_config

    cfg_path = _write_temporal_toml(tmp_path, half_life_days=-1.0)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        load_config(explicit_path=cfg_path)
    assert excinfo.value.code == 2


@pytest.mark.skip(reason="GREEN in Plan 02 — Pydantic validators wired in config.py")
def test_retention_days_negative_rejected(tmp_path, monkeypatch):
    """D-CONFIG-02: retention_days < 0 → SystemExit(2). 0 is the kept-forever escape hatch."""
    from supamem.config import load_config

    cfg_path = _write_temporal_toml(tmp_path, retention_days=-1)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        load_config(explicit_path=cfg_path)
    assert excinfo.value.code == 2
