"""Claude Code session JSONL ingestion (Phase 06).

Pure transform layer: JSONL → typed SessionEvent stream → Q+A turn pairs.
No Qdrant / fastembed coupling. Downstream chunker (Plan 06-02) consumes
``parse_jsonl_text`` and ``extract_pairs`` from this package.
"""
from __future__ import annotations

from .pair import TurnPair, extract_pairs
from .parser import KNOWN_TYPES, parse_jsonl, parse_jsonl_text

__all__ = [
    "KNOWN_TYPES",
    "TurnPair",
    "extract_pairs",
    "parse_jsonl",
    "parse_jsonl_text",
]
