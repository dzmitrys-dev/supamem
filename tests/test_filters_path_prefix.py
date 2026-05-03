"""Tests for Phase 11 magic-key branches in ``build_qdrant_filter``.

Covers two new magic keys that MUST be siphoned BEFORE the generic where
pass-through loop (Pitfall 2 — composition order):

- ``valid_to`` (D-VT-01..02): accept the literal string ``"now"`` as a no-op
  alias for the always-on temporal clause from Phase 9 D-FILTER-01; reject
  any other value with ``ValueError``.
- ``path_prefix`` (D-PFX-03): translate ``str`` → ``MatchValue`` and
  ``list[str]`` → ``MatchAny`` on the payload key ``path_prefixes`` (note the
  trailing ``s`` — the user-facing magic key is singular, the stored payload
  field is plural).

Single-element lists MUST still emit ``MatchAny`` (mirrors
``test_list_with_one_element_still_match_any`` in tests/test_filters.py).

The existing pass-through loop for non-magic keys (e.g. ``room``) is asserted
byte-identical to v0.1.5 by ``test_existing_pass_through_unchanged``.
"""
from __future__ import annotations

import json

import pytest
from qdrant_client.http import models as qmodels

from supamem.retrieval.filters import build_qdrant_filter


# ---------------------------------------------------------------------------
# path_prefix branch (D-PFX-03)
# ---------------------------------------------------------------------------


def test_path_prefix_string_emits_match_value():
    """D-PFX-03: where={'path_prefix': 'src/supamem'} → FieldCondition(
    key='path_prefixes', match=MatchValue(value='src/supamem')).
    """
    f = build_qdrant_filter({"path_prefix": "src/supamem"}, temporal=False)
    assert isinstance(f, qmodels.Filter)
    assert f.must is not None and len(f.must) == 1
    cond = f.must[0]
    assert isinstance(cond, qmodels.FieldCondition)
    assert cond.key == "path_prefixes"
    assert cond.match.value == "src/supamem"


def test_path_prefix_list_emits_match_any():
    """D-PFX-03: where={'path_prefix': ['src','docs']} → MatchAny on
    payload key ``path_prefixes``.
    """
    f = build_qdrant_filter(
        {"path_prefix": ["src", "docs"]}, temporal=False
    )
    cond = f.must[0]
    assert cond.key == "path_prefixes"
    assert list(cond.match.any) == ["src", "docs"]


def test_path_prefix_single_element_list_still_match_any():
    """Mirror test_list_with_one_element_still_match_any: list shape → MatchAny
    consistently; do NOT collapse single-element lists to MatchValue.
    """
    f = build_qdrant_filter({"path_prefix": ["src"]}, temporal=False)
    cond = f.must[0]
    assert cond.key == "path_prefixes"
    assert hasattr(cond.match, "any")
    assert list(cond.match.any) == ["src"]


def test_path_prefix_does_not_leak_to_generic_loop():
    """Composition order (Pitfall 2): the magic key ``path_prefix`` is siphoned
    off BEFORE the generic loop, so no FieldCondition with key='path_prefix'
    (singular) ever appears — only key='path_prefixes' (plural).
    """
    f = build_qdrant_filter({"path_prefix": "src"}, temporal=False)
    keys = [c.key for c in f.must]
    assert "path_prefix" not in keys
    assert keys == ["path_prefixes"]


# ---------------------------------------------------------------------------
# valid_to branch (D-VT-01..02)
# ---------------------------------------------------------------------------


def test_valid_to_now_is_noop():
    """D-VT-01: where={'valid_to': 'now'} is a no-op — produces wire shape
    identical to where={} (only the always-on temporal sub-filter remains).
    """
    flt_with_now = build_qdrant_filter({"valid_to": "now"})
    flt_without = build_qdrant_filter({})
    body_with = json.loads(flt_with_now.model_dump_json(exclude_none=True))
    body_without = json.loads(flt_without.model_dump_json(exclude_none=True))
    assert body_with == body_without


def test_valid_to_other_value_raises():
    """D-VT-02: any value other than the literal ``"now"`` raises ValueError
    referencing the always-on lock from Phase 9 D-FILTER-01.
    """
    with pytest.raises(ValueError, match="always-on"):
        build_qdrant_filter({"valid_to": "yesterday"}, temporal=False)


def test_valid_to_iso_timestamp_raises():
    """D-VT-04: time-travel queries are out of scope; ISO timestamps for
    ``valid_to`` are explicitly rejected.
    """
    with pytest.raises(ValueError):
        build_qdrant_filter(
            {"valid_to": "2025-01-01T00:00:00Z"}, temporal=False
        )


def test_valid_to_does_not_leak_to_generic_loop():
    """Composition order: valid_to='now' is siphoned BEFORE the generic loop,
    so co-passed non-magic keys (e.g. room) still pass through cleanly and
    no FieldCondition with key='valid_to' appears in the final must list
    (the always-on temporal clause is a nested Filter, not a FieldCondition
    keyed 'valid_to' at the top level).
    """
    f = build_qdrant_filter({"valid_to": "now", "room": "backend"})
    # must = [temporal nested Filter, room FieldCondition]
    assert f.must is not None and len(f.must) == 2
    assert isinstance(f.must[0], qmodels.Filter)  # temporal nested
    room_cond = f.must[1]
    assert isinstance(room_cond, qmodels.FieldCondition)
    assert room_cond.key == "room"
    assert room_cond.match.value == "backend"
    # No top-level FieldCondition with key='valid_to' was emitted by the
    # generic loop.
    top_level_keys = [
        c.key for c in f.must if isinstance(c, qmodels.FieldCondition)
    ]
    assert "valid_to" not in top_level_keys


# ---------------------------------------------------------------------------
# Regression lock: existing pass-through path byte-identical to v0.1.5
# ---------------------------------------------------------------------------


def test_existing_pass_through_unchanged():
    """Regression lock: where with only non-magic keys produces the same wire
    shape as before Phase 11 (matches test_wire_shape_matches_research_r01 in
    tests/test_filters.py).
    """
    f = build_qdrant_filter(
        {
            "room": "backend",
            "chunker": ["markdown_header", "transcript"],
        },
        temporal=False,
    )
    body = json.loads(f.model_dump_json(exclude_none=True))
    assert body == {
        "must": [
            {"key": "room", "match": {"value": "backend"}},
            {
                "key": "chunker",
                "match": {"any": ["markdown_header", "transcript"]},
            },
        ],
    }
