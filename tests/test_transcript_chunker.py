"""Tests for the Phase 6 transcript chunker (Plan 06-02 — RED).

Locks the divergent ``chunk_transcript(...) -> list[ChunkRecord]`` contract
(D-20), the byte-for-byte fence preservation invariant (D-15, INGEST-03),
the 2000-char tool-payload elision threshold (D-06), the always-populated
``tool_uses`` metadata (D-07) and its cross-pair status correlation
(W1 — observed via the next pair's ``tool_result.is_error``), the D-09
"assistant-with-only-tool_use still emits a chunk" invariant (W2),
the D-04/R-05 metadata schema, and the D-16 nested-fence fail-loud rule.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from supamem.indexer.transcript import (
    ChunkRecord,
    chunk_transcript,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(uuid: str, content, *, sid: str = "s1") -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": None,
        "sessionId": sid,
        "isSidechain": False,
        "message": {"role": "user", "content": content},
    }


def _assistant(uuid: str, content, *, sid: str = "s1") -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": None,
        "sessionId": sid,
        "isSidechain": False,
        "message": {"role": "assistant", "content": content},
    }


def _jsonl(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


# ---------------------------------------------------------------------------
# 1. ChunkRecord shape & metadata
# ---------------------------------------------------------------------------

def test_chunk_transcript_returns_chunkrecord_list() -> None:
    text = _jsonl(
        _user("u1", "hello"),
        _assistant("a1", [{"type": "text", "text": "hi back"}]),
    )
    result = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    assert isinstance(result, list)
    assert len(result) >= 1
    assert isinstance(result[0], ChunkRecord)


def test_metadata_carries_chunker_and_room() -> None:
    text = _jsonl(
        _user("u1", "hello"),
        _assistant("a1", [{"type": "text", "text": "hi"}]),
    )
    result = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    for chunk in result:
        assert chunk.metadata["chunker"] == "transcript"
        assert chunk.metadata["room"] == "transcript"


def test_metadata_carries_transcript_payload() -> None:
    text = _jsonl(
        _user("u1", "hello", sid="sess-A"),
        _assistant("a1", [{"type": "text", "text": "hi"}], sid="sess-A"),
        _assistant("a2", [{"type": "text", "text": "more"}], sid="sess-A"),
    )
    result = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    md = result[0].metadata["transcript"]
    assert md["session_id"] == "sess-A"
    assert md["user_uuid"] == "u1"
    assert isinstance(md["assistant_uuids"], list)
    assert md["assistant_uuids"] == ["a1", "a2"]
    assert isinstance(md["turn_index"], int)
    assert md["turn_index"] == 0


# ---------------------------------------------------------------------------
# 2. Fence preservation (D-15, INGEST-03, Pitfall §6)
# ---------------------------------------------------------------------------

def test_fence_indentation_preserved() -> None:
    fenced = "```python\n    def foo():\n        return 1\n```"
    text = _jsonl(
        _user("u1", "format this"),
        _assistant("a1", [{"type": "text", "text": fenced}]),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    joined = "\n".join(c.text for c in chunks)
    assert "    def foo():\n        return 1" in joined


def test_fence_with_tilde_delimiter() -> None:
    fenced = "~~~yaml\n  key: value\n  nested:\n    - a\n~~~"
    text = _jsonl(
        _user("u1", "render yaml"),
        _assistant("a1", [{"type": "text", "text": fenced}]),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    joined = "\n".join(c.text for c in chunks)
    assert "  key: value\n  nested:\n    - a" in joined


# ---------------------------------------------------------------------------
# 3. Tool payload elision (D-06, D-07, D-08)
# ---------------------------------------------------------------------------

def test_tool_payload_above_threshold_elided() -> None:
    """Use the committed tool_heavy fixture (3kB tool_use input)."""
    text = (FIXTURES / "tool_heavy_session.jsonl").read_text(encoding="utf-8")
    chunks = chunk_transcript(text, source_path=FIXTURES / "tool_heavy_session.jsonl")
    joined = "\n".join(c.text for c in chunks)
    assert "elided" in joined
    # The huge "xxxx..." payload must NOT appear verbatim
    assert "x" * 500 not in joined


def test_tool_payload_below_threshold_kept() -> None:
    text = _jsonl(
        _user("u1", "list files"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}],
        ),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    joined = "\n".join(c.text for c in chunks)
    assert "ls" in joined
    assert "elided" not in joined


def test_tool_uses_metadata_always_populated() -> None:
    """D-07: metadata['tool_uses'] populated regardless of elision."""
    text = _jsonl(
        _user("u1", "list"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}],
        ),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    tool_uses = chunks[0].metadata["tool_uses"]
    assert isinstance(tool_uses, list)
    assert len(tool_uses) == 1
    assert tool_uses[0]["id"] == "tu1"
    assert tool_uses[0]["tool_name"] == "Bash"
    assert "status" in tool_uses[0]


# ---------------------------------------------------------------------------
# 4. W1 cross-pair tool_result status correlation (D-07)
# ---------------------------------------------------------------------------

def test_tool_use_status_ok_when_no_error() -> None:
    """tool_result.is_error == False → status 'ok'."""
    text = _jsonl(
        _user("u1", "do thing"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"cmd": "ls"}}],
        ),
        _user(
            "u2",
            [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}],
        ),
        _assistant("a2", [{"type": "text", "text": "done"}]),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    # Find the chunk for the first turn-pair (turn_index=0)
    first = next(c for c in chunks if c.metadata["transcript"]["turn_index"] == 0)
    assert first.metadata["tool_uses"][0]["status"] == "ok"


def test_tool_use_status_error_when_tool_result_is_error() -> None:
    text = _jsonl(
        _user("u1", "do thing"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"cmd": "bad"}}],
        ),
        _user(
            "u2",
            [{"type": "tool_result", "tool_use_id": "tu1", "content": "boom", "is_error": True}],
        ),
        _assistant("a2", [{"type": "text", "text": "sorry"}]),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    first = next(c for c in chunks if c.metadata["transcript"]["turn_index"] == 0)
    assert first.metadata["tool_uses"][0]["status"] == "error"


def test_tool_use_status_ok_when_no_following_pair() -> None:
    """Last pair w/ tool_use → no follow-up to inspect → defaults to 'ok'."""
    text = _jsonl(
        _user("u1", "do thing"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"cmd": "ls"}}],
        ),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    last = chunks[-1]
    assert last.metadata["tool_uses"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# 5. Soft-cap splitting respects fences
# ---------------------------------------------------------------------------

def test_oversize_pair_splits_outside_fence() -> None:
    big_prose = ("alpha beta gamma delta epsilon zeta eta theta iota kappa " * 100).strip()
    fenced = "```python\n    def keep_together():\n        return 42\n```"
    body = big_prose + "\n\n" + fenced + "\n\n" + big_prose
    text = _jsonl(
        _user("u1", "ramble"),
        _assistant("a1", [{"type": "text", "text": body}]),
    )
    chunks = chunk_transcript(
        text, source_path=Path("/tmp/x.jsonl"), soft_max_tokens=100
    )
    assert len(chunks) >= 2
    fence_hits = sum("def keep_together():" in c.text for c in chunks)
    assert fence_hits == 1, (
        f"fenced block must land in exactly one chunk; got {fence_hits}"
    )


# ---------------------------------------------------------------------------
# 6. D-16 nested same-delim fences fail loud
# ---------------------------------------------------------------------------

def test_nested_same_delim_fence_fails_loud() -> None:
    # Two `````` openers without an intervening closer of the same delim
    bad = "```python\nouter\n```ruby\ninner\n"  # only opens, never closes
    text = _jsonl(
        _user("u1", "broken"),
        _assistant("a1", [{"type": "text", "text": bad}]),
    )
    with pytest.raises(ValueError, match=r"nested|unclosed fence"):
        chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))


# ---------------------------------------------------------------------------
# 7. D-05 interrupted user
# ---------------------------------------------------------------------------

def test_interrupted_user_pair_chunked() -> None:
    text = _jsonl(
        _user("u1", "still typing..."),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    assert len(chunks) == 1
    assert "still typing" in chunks[0].text
    assert chunks[0].metadata["transcript"]["assistant_uuids"] == []


# ---------------------------------------------------------------------------
# 8. D-09 (W2) — assistant with only tool_use still emits chunk
# ---------------------------------------------------------------------------

def test_assistant_with_only_tool_use_emits_chunk() -> None:
    text = _jsonl(
        _user("u1", "run it"),
        _assistant(
            "a1",
            [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"cmd": "ls"}}],
        ),
    )
    chunks = chunk_transcript(text, source_path=Path("/tmp/x.jsonl"))
    assert len(chunks) == 1
    assert "tool_use" in chunks[0].text or "Bash" in chunks[0].text
    assert len(chunks[0].metadata["tool_uses"]) == 1
    assert chunks[0].metadata["tool_uses"][0]["id"] == "t1"


# ---------------------------------------------------------------------------
# 9. R-02 token parity — _token_count comes from indexer.chunker
# ---------------------------------------------------------------------------

def test_token_count_uses_shared_helper() -> None:
    """Verify the chunker module imports _token_count from indexer.chunker."""
    import supamem.indexer.transcript.chunker as chunker_mod

    # Either the symbol is bound at module level, or it appears in the source.
    src = Path(chunker_mod.__file__).read_text(encoding="utf-8")
    assert "from supamem.indexer.chunker import _token_count" in src
