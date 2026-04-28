"""Tests for the T-1 markdown chunker (Plan 80.6-04 Task 1).

Behaviors locked to Phase 80.1 RESEARCH §T-1: header-aware split with a
secondary RecursiveCharacterTextSplitter cap above ``CHUNK_SOFT_MAX_TOKENS``.
"""
from __future__ import annotations

import pytest

from supamem.indexer.chunker import (
    CHUNK_FALLBACK_OVERLAP,
    CHUNK_FALLBACK_SIZE,
    CHUNK_SOFT_MAX_TOKENS,
    HEADERS_TO_SPLIT_ON,
    chunk_markdown,
)


def test_chunk_markdown_splits_on_h1_h2_h3() -> None:
    text = "# A\nx\n## B\ny\n### C\nz"
    chunks = chunk_markdown(text)
    assert len(chunks) == 3, f"expected 3 chunks, got {len(chunks)}: {chunks!r}"
    joined = "\n".join(chunks)
    for needle in ("x", "y", "z"):
        assert needle in joined, f"expected {needle!r} in chunks"


def test_chunk_markdown_preserves_short_chunks() -> None:
    """A chunk under soft_max should be returned whole (no further splitting)."""
    text = "## Short\nbody under threshold"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "body under threshold" in chunks[0]


def test_chunk_markdown_recursive_split_when_long() -> None:
    """A chunk above soft_max gets RecursiveCharacterTextSplitter applied."""
    long_paragraph = ("alpha beta gamma delta epsilon zeta eta theta iota kappa " * 50).strip()
    text = f"## Big\n{long_paragraph}"
    chunks = chunk_markdown(text)
    assert len(chunks) >= 2, f"expected secondary split, got {len(chunks)} chunk(s)"


def test_chunk_markdown_empty_returns_empty_list() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n   ") == []


def test_chunk_markdown_no_headers_returns_whole_body() -> None:
    text = "plain text without any headers — should flow as a single chunk"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert text in chunks[0]


def test_chunker_constants_match_phase_801_lock() -> None:
    """Locked T-1 parameters — changing these breaks the −78.5% token bench."""
    assert CHUNK_SOFT_MAX_TOKENS == 250
    assert CHUNK_FALLBACK_SIZE == 200
    assert CHUNK_FALLBACK_OVERLAP == 20
    assert HEADERS_TO_SPLIT_ON == [("#", "h1"), ("##", "h2"), ("###", "h3")]


@pytest.mark.parametrize("noisy", ["\n\n# Title\n\nbody\n\n", "\t# Tab\nbody"])
def test_chunk_markdown_strips_outer_whitespace(noisy: str) -> None:
    chunks = chunk_markdown(noisy)
    assert chunks  # non-empty
    assert "body" in "\n".join(chunks)
