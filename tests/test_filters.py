"""Tests for retrieval/filters.py — single Qdrant Filter construction site (D-02, D-03).

Phase 9 D-FILTER-01..03: ``build_qdrant_filter`` now prepends an always-on
temporal sub-filter unless the caller passes ``temporal=False`` (the diagnostic
fast path used by indexer-side scroll callers). The historical "where-only"
shape is preserved by passing ``temporal=False`` in these tests so they continue
to assert the where-dispatch contract in isolation. The temporal contract has
its own coverage in ``tests/test_retrieval_temporal.py``.
"""
from __future__ import annotations

import json

from qdrant_client.http import models as qmodels

from supamem.retrieval.filters import build_qdrant_filter


def test_none_input_with_temporal_off_returns_none():
    """D-FILTER-02: ``temporal=False`` + no where → None (diagnostic fast path)."""
    assert build_qdrant_filter(None, temporal=False) is None


def test_none_input_default_returns_temporal_filter():
    """D-FILTER-01: default ``temporal=True`` produces a Filter even with where=None."""
    flt = build_qdrant_filter(None)
    assert isinstance(flt, qmodels.Filter)
    assert flt.must is not None and len(flt.must) == 1
    # The single must-entry is the nested temporal sub-filter (Filter, not FieldCondition).
    assert isinstance(flt.must[0], qmodels.Filter)


def test_empty_dict_with_temporal_off_returns_none():
    """D-FILTER-02: empty where + ``temporal=False`` → None."""
    assert build_qdrant_filter({}, temporal=False) is None


def test_single_string_value_uses_match_value():
    """D-FILTER-01: where dispatch — single string → MatchValue.

    Uses ``temporal=False`` to isolate the where-dispatch contract.
    """
    f = build_qdrant_filter({"room": "backend"}, temporal=False)
    assert isinstance(f, qmodels.Filter)
    assert f.must is not None and len(f.must) == 1
    cond = f.must[0]
    assert cond.key == "room"
    # MatchValue carries .value
    assert cond.match.value == "backend"


def test_list_value_uses_match_any():
    """D-FILTER-01: where dispatch — list → MatchAny."""
    f = build_qdrant_filter(
        {"chunker": ["markdown_header", "transcript"]}, temporal=False
    )
    assert f.must[0].key == "chunker"
    # MatchAny carries .any
    assert list(f.must[0].match.any) == ["markdown_header", "transcript"]


def test_multi_key_produces_and_in_must():
    """D-FILTER-01: multi-key where → AND under must=, insertion order preserved."""
    f = build_qdrant_filter(
        {"room": "backend", "chunker": ["markdown_header"]}, temporal=False
    )
    assert len(f.must) == 2
    keys = [c.key for c in f.must]
    assert keys == ["room", "chunker"]  # insertion order preserved


def test_list_with_one_element_still_match_any():
    """RESEARCH §Alternatives Considered: list-shape → MatchAny consistently;
    do NOT collapse single-element lists to MatchValue (preserves contract docs).
    """
    f = build_qdrant_filter({"room": ["backend"]}, temporal=False)
    assert hasattr(f.must[0].match, "any")


def test_returned_object_is_filter_instance():
    f = build_qdrant_filter({"room": "backend"}, temporal=False)
    assert isinstance(f, qmodels.Filter)
    assert not isinstance(f, dict)


def test_wire_shape_matches_research_r01():
    """RESEARCH R-01 wire shape — verified against ``temporal=False`` to keep
    the assertion focused on the where-dispatch wire contract.
    """
    f = build_qdrant_filter(
        {
            "room": "backend",
            "chunker": ["markdown_header", "transcript"],
        },
        temporal=False,
    )
    body = json.loads(f.model_dump_json(exclude_none=True))
    # Verified shape per RESEARCH R-01 lines 581-584
    assert body == {
        "must": [
            {"key": "room", "match": {"value": "backend"}},
            {"key": "chunker", "match": {"any": ["markdown_header", "transcript"]}},
        ],
    }
