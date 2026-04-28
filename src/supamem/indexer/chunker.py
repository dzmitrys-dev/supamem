"""T-1 markdown header chunker (Phase 80.1 lock).

Header-aware split via ``MarkdownHeaderTextSplitter`` with a secondary
``RecursiveCharacterTextSplitter`` cap above ``CHUNK_SOFT_MAX_TOKENS``.
Constants are locked: changing them invalidates the −78.5% token bench.
"""
from __future__ import annotations

HEADERS_TO_SPLIT_ON: list[tuple[str, str]] = [("#", "h1"), ("##", "h2"), ("###", "h3")]
CHUNK_SOFT_MAX_TOKENS = 250
CHUNK_FALLBACK_SIZE = 200
CHUNK_FALLBACK_OVERLAP = 20
CHUNK_MIN_TOKENS = 20  # match tuned_current.py: skip tiny preamble fragments


def _token_count(text: str) -> int:
    """cl100k_base token count with a length-based fallback if tiktoken absent."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def chunk_markdown(
    text: str,
    *,
    soft_max_tokens: int = CHUNK_SOFT_MAX_TOKENS,
    fallback_size: int = CHUNK_FALLBACK_SIZE,
    fallback_overlap: int = CHUNK_FALLBACK_OVERLAP,
) -> list[str]:
    """Header-aware Markdown chunking with secondary length cap.

    Empty / no-header docs return ``[body]`` so plain prose flows as a single
    chunk into the tuned collection. Mirrors
    ``softchat/scripts/embed-dev-memories.py:chunk_markdown`` exactly.
    """
    body = text.strip()
    if not body:
        return []
    try:
        from langchain_text_splitters import (
            MarkdownHeaderTextSplitter,
            RecursiveCharacterTextSplitter,
        )
    except ImportError:
        return [body]

    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS_TO_SPLIT_ON)
    docs = splitter.split_text(body)
    raw_chunks = [d.page_content for d in docs if d.page_content.strip()]
    if not raw_chunks:
        return [body]

    rc_splitter = RecursiveCharacterTextSplitter(
        chunk_size=fallback_size * 4,  # chars; ~fallback_size tokens
        chunk_overlap=fallback_overlap * 4,
    )
    out: list[str] = []
    for chunk in raw_chunks:
        if _token_count(chunk) <= soft_max_tokens:
            out.append(chunk)
            continue
        for piece in rc_splitter.split_text(chunk):
            if piece.strip():
                out.append(piece)
    return out
