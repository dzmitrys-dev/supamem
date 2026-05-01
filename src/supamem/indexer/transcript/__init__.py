"""Claude Code session JSONL ingestion (Phase 06).

Pure transform layer: JSONL → typed SessionEvent stream → Q+A turn pairs.
No Qdrant / fastembed coupling. Downstream chunker (Plan 06-02) consumes
``parse_jsonl_text`` and ``extract_pairs`` from this package.
"""
from __future__ import annotations

from .chunker import (
    CHUNK_SOFT_MAX_TOKENS_TRANSCRIPT,
    TOOL_PAYLOAD_MAX_CHARS,
    ChunkRecord,
    chunk_transcript,
)
from .pair import TurnPair, extract_pairs
from .parser import KNOWN_TYPES, parse_jsonl, parse_jsonl_text

__all__ = [
    "CHUNK_SOFT_MAX_TOKENS_TRANSCRIPT",
    "KNOWN_TYPES",
    "TOOL_PAYLOAD_MAX_CHARS",
    "ChunkRecord",
    "TurnPair",
    "chunk_transcript",
    "extract_pairs",
    "parse_jsonl",
    "parse_jsonl_text",
]
