"""Transcript chunker (Phase 06 lock).

Turn-pair drawers with fence-protecting pre-pass and tool-payload elision.
Constants are locked: changing CHUNK_SOFT_MAX_TOKENS_TRANSCRIPT or
TOOL_PAYLOAD_MAX_CHARS invalidates the v0.2.0 token-economy bench (Phase 10).
See ``.planning/phases/06-transcript-chunker-plugin/06-RESEARCH.md`` §R-03, §D-06.

Diverges from ``chunk_markdown`` (which returns ``list[str]``) by returning
``list[ChunkRecord]`` so per-pair metadata travels with the chunk to Qdrant
(D-20). The indexer dispatcher (Plan 06-03) MUST accept both shapes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from supamem.indexer.chunker import _token_count  # R-02 hard contract
from supamem.indexer.transcript.pair import TurnPair, extract_pairs
from supamem.indexer.transcript.parser import parse_jsonl_text  # B4 — owned by 06-01

CHUNK_SOFT_MAX_TOKENS_TRANSCRIPT = 600  # R-03
TOOL_PAYLOAD_MAX_CHARS = 2000  # D-06
CHUNK_FALLBACK_SIZE = 500
CHUNK_FALLBACK_OVERLAP = 40


@dataclass
class ChunkRecord:
    """One Q+A drawer chunk with metadata destined for Qdrant payload (D-20)."""

    text: str
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fence-protecting pre-pass (D-15, INGEST-03; sidesteps langchain #20823)
# ---------------------------------------------------------------------------

# Fence detection: ``` or ~~~ at line start, optionally with language tag.
_FENCE_RE = re.compile(r"^(?P<delim>```|~~~)(?P<info>[^\n]*)$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\x00FENCE(\d+)\x00")


def split_protecting_fences(text: str) -> tuple[str, list[str]]:
    """Replace fenced regions with placeholders. Returns ``(mangled_text, fences)``.

    The caller runs its length-based splitter on ``mangled_text``, then calls
    :func:`restore_fences` on each piece to put the verbatim fence content
    back. This avoids the langchain ``RecursiveCharacterTextSplitter``
    indentation-stripping bug (issue #20823, closed not-planned).

    D-16: nested same-delimiter fences raise ``ValueError``. Per CommonMark,
    a closing fence is a bare delimiter (no info string); a delimiter with a
    non-empty info string appearing while another fence is still open is a
    nested opener — unsupported in v1. Different-delim fences inside an open
    fence are treated as content (normal markdown).
    """
    fences: list[str] = []
    out: list[str] = []
    pos = 0
    matches = list(_FENCE_RE.finditer(text))
    i = 0
    while i < len(matches):
        opener = matches[i]
        delim = opener.group("delim")
        # Walk forward looking for a same-delim closer. A same-delim match
        # with a non-empty info string while still open is a nested opener
        # (D-16) — fail loud. A bare same-delim match closes.
        j = i + 1
        closer = None
        while j < len(matches):
            mj = matches[j]
            if mj.group("delim") == delim:
                if mj.group("info").strip():
                    line_no = text[: mj.start()].count("\n") + 1
                    raise ValueError(
                        f"nested same-delimiter fence at line {line_no}; "
                        f"D-16 unsupported"
                    )
                closer = mj
                break
            j += 1
        if closer is None:
            # Unclosed fence — D-16 fail-loud per INGEST-05.
            line_no = text[: opener.start()].count("\n") + 1
            raise ValueError(
                f"unclosed fence at line {line_no}; nested or unterminated "
                f"same-delimiter fence (D-16 unsupported)"
            )
        # Capture the full fence including delimiters and trailing newline.
        fence_text = text[opener.start() : closer.end()]
        out.append(text[pos : opener.start()])
        fences.append(fence_text)
        out.append(f"\x00FENCE{len(fences) - 1}\x00")
        pos = closer.end()
        i = j + 1
    out.append(text[pos:])
    return "".join(out), fences


def restore_fences(piece: str, fences: list[str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: fences[int(m.group(1))], piece)


# ---------------------------------------------------------------------------
# Drawer rendering helpers
# ---------------------------------------------------------------------------

def _render_user_content(content: str | list[dict]) -> str:
    """Render a user message ``content`` field — str or list of blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_result":
            tu_id = block.get("tool_use_id", "")
            inner = block.get("content", "")
            if isinstance(inner, list):
                inner_str = json.dumps(inner)
            else:
                inner_str = str(inner)
            err_marker = " is_error=true" if block.get("is_error") else ""
            parts.append(f"[tool_result id={tu_id}{err_marker}]\n{inner_str}")
        elif btype == "image":
            parts.append("[image]")
    return "\n".join(parts)


def _render_tool_use(block: dict, *, threshold: int) -> tuple[str, dict]:
    """Render a single ``tool_use`` block; emit synthesis stub if oversize.

    Returns ``(rendered_text, tool_use_meta)``. Meta carries
    ``{id, tool_name, status}`` per D-07; status defaults to "ok" and is
    patched later by :func:`_correlate_tool_statuses` if a follow-up
    ``tool_result.is_error`` is observed.
    """
    tu_id = block.get("id", "")
    name = block.get("name", "")
    raw_input = block.get("input", {})
    input_json = json.dumps(raw_input, ensure_ascii=False)
    if len(input_json) > threshold:
        size_kb = max(1, len(input_json) // 1024)
        rendered = f"[tool_use:{name} id={tu_id} input=…({size_kb}kB elided)]"
    else:
        rendered = f"[tool_use:{name} id={tu_id} input={input_json}]"
    return rendered, {"id": tu_id, "tool_name": name, "status": "ok"}


def _render_assistant_content(
    blocks: list[dict], *, tool_payload_max_chars: int
) -> tuple[str, list[dict]]:
    """Render an assistant message's content list.

    D-09 (W2): even if every block is a ``tool_use`` with no ``text`` block,
    we still emit the rendered text so the pair produces a chunk.
    """
    parts: list[str] = []
    tool_uses_meta: list[dict] = []
    if not isinstance(blocks, list):
        return "", tool_uses_meta
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            parts.append(f"[thinking]\n{block.get('text', '')}\n[/thinking]")
        elif btype == "tool_use":
            rendered, meta = _render_tool_use(
                block, threshold=tool_payload_max_chars
            )
            parts.append(rendered)
            tool_uses_meta.append(meta)
    return "\n".join(parts), tool_uses_meta


def _render_pair(
    pair: TurnPair, *, tool_payload_max_chars: int
) -> tuple[str, list[dict]]:
    """Render one :class:`TurnPair` to drawer text + tool_uses metadata."""
    sections: list[str] = []
    user_content = pair.user_event.get("message", {}).get("content", "")
    sections.append("### User\n" + _render_user_content(user_content))
    tool_uses_meta: list[dict] = []
    for assistant_evt in pair.assistant_events:
        a_content = assistant_evt.get("message", {}).get("content", [])
        rendered, metas = _render_assistant_content(
            a_content, tool_payload_max_chars=tool_payload_max_chars
        )
        # D-09 (W2): always emit the assistant header even if rendered is "".
        sections.append("### Assistant\n" + rendered)
        tool_uses_meta.extend(metas)
    return "\n\n".join(sections), tool_uses_meta


# ---------------------------------------------------------------------------
# W1 — Cross-pair tool_result status correlation (D-07)
# ---------------------------------------------------------------------------

def _correlate_tool_statuses(
    pairs: list[TurnPair], pairs_meta: list[list[dict]]
) -> None:
    """Patch ``pairs_meta[i][k]['status']`` from ``pairs[i+1]``'s tool_results.

    Default status is "ok"; we flip to "error" only if the next pair's user
    content carries a ``tool_result`` block with ``is_error == True`` for the
    matching ``tool_use_id``. tool_uses with no observable tool_result (e.g.,
    last pair, or async/streamed) keep "ok".
    """
    for i, _pair in enumerate(pairs):
        if i + 1 >= len(pairs):
            continue  # no follow-up user pair → status stays "ok"
        next_user = pairs[i + 1].user_event or {}
        next_content = next_user.get("message", {}).get("content")
        if not isinstance(next_content, list):
            continue
        results_by_id: dict[str, bool] = {}
        for blk in next_content:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                tu_id = blk.get("tool_use_id")
                if tu_id is None:
                    continue
                results_by_id[tu_id] = bool(blk.get("is_error", False))
        for tu_meta in pairs_meta[i]:
            tu_id = tu_meta.get("id")
            if tu_id in results_by_id:
                tu_meta["status"] = "error" if results_by_id[tu_id] else "ok"


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------

def chunk_transcript(
    text: str,
    *,
    source_path: Path,
    soft_max_tokens: int = CHUNK_SOFT_MAX_TOKENS_TRANSCRIPT,
    tool_payload_max_chars: int = TOOL_PAYLOAD_MAX_CHARS,
    **kwargs: Any,
) -> list[ChunkRecord]:
    """Chunk a Claude Code session JSONL into Q+A drawer ChunkRecords.

    NOTE: This chunker DIVERGES from ``chunk_markdown``'s contract — it
    returns ``list[ChunkRecord]``, NOT ``list[str]``, because per-chunk
    metadata is required (D-20). The indexer dispatcher
    (``indexer/__init__.py``) MUST accept both shapes — see Plan 06-03 for
    the adapter.

    Parameters
    ----------
    text:
        Full session JSONL text (in-memory).
    source_path:
        Origin path used as the parser ``label`` for diagnostic messages.
    soft_max_tokens:
        Per-chunk soft cap; oversize drawers split outside-fence regions.
    tool_payload_max_chars:
        D-06 elision threshold; ``tool_use.input`` JSON over this is
        replaced with a synthesis stub but ``tool_uses`` metadata still
        carries ``{id, tool_name, status}`` per D-07.
    """
    events = list(parse_jsonl_text(text, label=str(source_path)))
    pairs = extract_pairs(events)
    if not pairs:
        return []

    # Render all pairs first; W1 correlation needs all events visible.
    drawer_texts: list[str] = []
    pairs_meta: list[list[dict]] = []
    for pair in pairs:
        drawer_text, tu_meta = _render_pair(
            pair, tool_payload_max_chars=tool_payload_max_chars
        )
        drawer_texts.append(drawer_text)
        pairs_meta.append(tu_meta)

    _correlate_tool_statuses(pairs, pairs_meta)

    # Soft-import the recursive splitter (matches markdown chunker pattern).
    rc_splitter = None
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        rc_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_FALLBACK_SIZE * 4,  # ~tokens × 4 chars
            chunk_overlap=CHUNK_FALLBACK_OVERLAP * 4,
        )
    except ImportError:
        rc_splitter = None

    out: list[ChunkRecord] = []
    for idx, (pair, drawer_text) in enumerate(zip(pairs, drawer_texts)):
        # Validate fences eagerly so D-16 raises before chunk emission.
        mangled, fences = split_protecting_fences(drawer_text)

        meta = {
            "chunker": "transcript",
            "room": "transcript",
            "transcript": {
                "session_id": pair.user_event.get("sessionId", ""),
                "user_uuid": pair.user_uuid,
                "assistant_uuids": list(pair.assistant_uuids),
                "turn_index": pair.turn_index,
            },
            "tool_uses": pairs_meta[idx],
        }

        if _token_count(drawer_text) <= soft_max_tokens or rc_splitter is None:
            out.append(ChunkRecord(text=drawer_text, metadata=dict(meta)))
            continue

        for piece in rc_splitter.split_text(mangled):
            if not piece.strip():
                continue
            restored = restore_fences(piece, fences)
            out.append(ChunkRecord(text=restored, metadata=dict(meta)))
    return out
