"""Tests for the turn-pair extractor (Plan 06-01 Task 3).

Behaviors locked to Phase 06 CONTEXT §D-03 / D-04 / D-05 + RESEARCH
§Code Examples §3 + §Pitfalls §5: 1-user + N-contiguous-assistants
grouping; interrupted users still emit chunks (D-05); isSidechain and
non-Q+A event types skipped.
"""
from __future__ import annotations

from supamem.indexer.transcript.pair import TurnPair, extract_pairs


def _user(uuid: str, **extra: object) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "isSidechain": False,
        "message": {"role": "user", "content": f"q-{uuid}"},
        **extra,
    }


def _assistant(uuid: str, **extra: object) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "isSidechain": False,
        "message": {"role": "assistant", "content": [{"type": "text", "text": f"a-{uuid}"}]},
        **extra,
    }


def test_extract_pairs_groups_user_with_contiguous_assistants() -> None:
    """D-03: [U1,A1,A2,U2,A3] → 2 pairs: (U1,[A1,A2]), (U2,[A3])."""
    events = [_user("u1"), _assistant("a1"), _assistant("a2"), _user("u2"), _assistant("a3")]
    pairs = extract_pairs(events)
    assert len(pairs) == 2
    assert pairs[0].user_uuid == "u1"
    assert pairs[0].assistant_uuids == ["a1", "a2"]
    assert pairs[1].user_uuid == "u2"
    assert pairs[1].assistant_uuids == ["a3"]


def test_interrupted_user_emits_chunk() -> None:
    """D-05: [U1,A1,U2] → 2 pairs; the second has empty assistant_uuids."""
    events = [_user("u1"), _assistant("a1"), _user("u2")]
    pairs = extract_pairs(events)
    assert len(pairs) == 2
    assert pairs[1].user_uuid == "u2"
    assert pairs[1].assistant_uuids == []
    assert pairs[1].assistant_events == []


def test_assistant_before_user_skipped() -> None:
    """[A0,U1,A1] → 1 pair (U1,[A1]); A0 dropped (no preceding user)."""
    events = [_assistant("a0"), _user("u1"), _assistant("a1")]
    pairs = extract_pairs(events)
    assert len(pairs) == 1
    assert pairs[0].user_uuid == "u1"
    assert pairs[0].assistant_uuids == ["a1"]


def test_sidechain_events_skipped() -> None:
    """Pitfall §5: isSidechain=True events filtered; remaining grouping correct."""
    events = [
        _user("u1"),
        _assistant("a1"),
        _user("uS", isSidechain=True),  # sub-agent user — skip
        _assistant("aS", isSidechain=True),  # sub-agent assistant — skip
        _user("u2"),
        _assistant("a2"),
    ]
    pairs = extract_pairs(events)
    assert len(pairs) == 2
    assert [p.user_uuid for p in pairs] == ["u1", "u2"]
    assert [p.assistant_uuids for p in pairs] == [["a1"], ["a2"]]


def test_summary_filehistory_queueop_system_skipped() -> None:
    """summary, file-history-snapshot, queue-operation, system events all filtered."""
    events = [
        _user("u1"),
        {"type": "summary", "uuid": "sum1", "summary": "...", "leafUuid": "u1"},
        {"type": "file-history-snapshot", "uuid": "fh1"},
        {"type": "queue-operation", "uuid": "q1"},
        {"type": "system", "uuid": "sys1"},
        _assistant("a1"),
        _user("u2"),
        _assistant("a2"),
    ]
    pairs = extract_pairs(events)
    assert len(pairs) == 2
    assert [p.user_uuid for p in pairs] == ["u1", "u2"]
    assert [p.assistant_uuids for p in pairs] == [["a1"], ["a2"]]


def test_turn_index_monotonic() -> None:
    """turn_index increments 0,1,2 across emitted pairs."""
    events = [
        _user("u1"), _assistant("a1"),
        _user("u2"), _assistant("a2"),
        _user("u3"), _assistant("a3"),
    ]
    pairs = extract_pairs(events)
    assert [p.turn_index for p in pairs] == [0, 1, 2]


def test_pair_carries_full_events() -> None:
    """user_event / assistant_events retain the full original dicts (chunker needs them)."""
    u = _user("u1")
    a = _assistant("a1")
    pairs = extract_pairs([u, a])
    assert len(pairs) == 1
    assert isinstance(pairs[0], TurnPair)
    assert pairs[0].user_event is u
    assert pairs[0].assistant_events == [a]
    # Spot-check that the message payload survives — Plan 06-02 will render from these.
    assert pairs[0].user_event["message"]["content"] == "q-u1"
    assert pairs[0].assistant_events[0]["message"]["content"][0]["text"] == "a-a1"
