"""Tests for retrieval/filters.py — single Qdrant Filter construction site (D-02, D-03)."""
from __future__ import annotations

import json

from qdrant_client.http import models as qmodels

from supamem.retrieval.filters import build_qdrant_filter


def test_none_input_returns_none():
    assert build_qdrant_filter(None) is None


def test_empty_dict_returns_none():
    assert build_qdrant_filter({}) is None


def test_single_string_value_uses_match_value():
    f = build_qdrant_filter({"room": "backend"})
    assert isinstance(f, qmodels.Filter)
    assert f.must is not None and len(f.must) == 1
    cond = f.must[0]
    assert cond.key == "room"
    # MatchValue carries .value
    assert cond.match.value == "backend"


def test_list_value_uses_match_any():
    f = build_qdrant_filter({"chunker": ["markdown_header", "transcript"]})
    assert f.must[0].key == "chunker"
    # MatchAny carries .any
    assert list(f.must[0].match.any) == ["markdown_header", "transcript"]


def test_multi_key_produces_and_in_must():
    f = build_qdrant_filter({"room": "backend", "chunker": ["markdown_header"]})
    assert len(f.must) == 2
    keys = [c.key for c in f.must]
    assert keys == ["room", "chunker"]  # insertion order preserved


def test_list_with_one_element_still_match_any():
    # Per RESEARCH §Alternatives Considered: list-shape → MatchAny consistently;
    # do NOT collapse single-element lists to MatchValue (preserves contract docs).
    f = build_qdrant_filter({"room": ["backend"]})
    assert hasattr(f.must[0].match, "any")


def test_returned_object_is_filter_instance():
    f = build_qdrant_filter({"room": "backend"})
    assert isinstance(f, qmodels.Filter)
    assert not isinstance(f, dict)


def test_wire_shape_matches_research_r01():
    f = build_qdrant_filter({
        "room": "backend",
        "chunker": ["markdown_header", "transcript"],
    })
    body = json.loads(f.model_dump_json(exclude_none=True))
    # Verified shape per RESEARCH R-01 lines 581-584
    assert body == {
        "must": [
            {"key": "room", "match": {"value": "backend"}},
            {"key": "chunker", "match": {"any": ["markdown_header", "transcript"]}},
        ],
    }
