"""Phase 17-B — AST-aware Python chunker (opt-in `tree_sitter_code` plugin).

Closes the recall@10/20 + nDCG@10 tail mem0 holds on `code_fact.combined`
by chunking on real Python boundaries (function/class definitions and
decorated definitions) instead of markdown headers. Default users pay
zero cost — `tree_sitter` is imported lazily inside the public function so
nobody who has not opted into `chunker = "tree_sitter_code"` ever pulls
the wheel into their process (T-17-04, T-17-05).

Decisions referenced (see `.planning/phases/17-coderag-chunker-and-hyde-uplift/17-CONTEXT.md`):

- D-AST-01: function-entry plugin signature (mirror `chunk_markdown`)
- D-AST-02: MiniLM token-count primitive via `fastembed.TextEmbedding(...)
  .token_count`; fallback cap = 512
- D-AST-03: parse-error fallback → `chunk_markdown` + `err_console.print`
- D-AST-04: emitted chunks must not straddle function/class boundaries
- D-PKG-01: `[ast-chunker]` is an optional extra; core install unchanged
- D-PKG-02: lazy import inside the function body, raise actionable
  `RuntimeError` instructing `pip install supamem[ast-chunker]`
- D-SCOPE-05 carry-lock: this module is a flat sibling of `chunker.py`,
  NOT a subpackage — `eval/coderag/ingest.py` still imports
  `chunk_markdown` via the locked path.
"""
from __future__ import annotations

from typing import Any, Callable

from supamem.console import err_console
from supamem.indexer.chunker import chunk_markdown  # parse-error fallback

DEFAULT_MAX_TOKENS = 512  # MiniLM-L6-v2 sequence-length budget (D-AST-02)

BOUNDARY_TYPES: frozenset[str] = frozenset(
    {
        "function_definition",
        "class_definition",
        "async_function_definition",
        "decorated_definition",
    }
)

# Module-level cache for the fastembed tokenizer — first call does the
# (one-time) ONNX-model download; subsequent calls reuse the instance.
_TOKENIZER: Any | None = None


def _get_tokenizer() -> Any | None:
    """Return a cached `fastembed.TextEmbedding` for `token_count`, or None.

    None signals "tokenizer unavailable" — callers fall back to a
    char-length heuristic so the chunker still functions in environments
    where fastembed cannot reach its model storage.
    """
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    try:
        from fastembed import TextEmbedding

        _TOKENIZER = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
        return _TOKENIZER
    except Exception:  # noqa: BLE001 — tokenizer is best-effort; chunker must not fail
        return None


def _token_count(text: str) -> int:
    tk = _get_tokenizer()
    if tk is None:
        # Conservative char→token estimate (~4 chars/token, MiniLM-ish).
        return max(1, len(text) // 4)
    try:
        return int(tk.token_count(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 4)


def _resolve_max_tokens(override: int | None) -> int:
    if override is not None:
        return int(override)
    return DEFAULT_MAX_TOKENS


def _slice(src_bytes: bytes, start: int, end: int) -> str:
    return src_bytes[start:end].decode("utf-8", errors="replace")


def _split_oversized(text: str, cap: int, tcount: Callable[[str], int]) -> list[str]:
    """Split a node whose own token count exceeds the cap.

    cAST Algorithm 1 falls back to line-based splitting for indivisible
    oversized leaves. We split on blank lines first, then hard-cap by
    line count if a single contiguous block is still too big.
    """
    if tcount(text) <= cap:
        return [text]
    # Blank-line split.
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    out: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for para in paragraphs:
        ptok = tcount(para)
        if ptok > cap:
            if buf:
                out.append("\n\n".join(buf))
                buf, buf_tokens = [], 0
            # Hard split on lines.
            lines = para.splitlines(keepends=True)
            cur: list[str] = []
            cur_tok = 0
            for line in lines:
                ltok = tcount(line)
                if cur and cur_tok + ltok > cap:
                    out.append("".join(cur))
                    cur, cur_tok = [], 0
                cur.append(line)
                cur_tok += ltok
            if cur:
                out.append("".join(cur))
            continue
        if buf and buf_tokens + ptok > cap:
            out.append("\n\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(para)
        buf_tokens += ptok
    if buf:
        out.append("\n\n".join(buf))
    return [c for c in out if c.strip()]


def _split_function_paragraphs(
    text: str, cap: int, tcount: Callable[[str], int]
) -> list[str]:
    """Split a function/method definition into paragraph-level chunks.

    Emits one chunk per blank-line-separated paragraph. The leading
    paragraph (signature + docstring + first statement group) stays as
    one piece so the boundary-straddle invariant (D-AST-04) holds — the
    chunk still STARTS at a function_definition boundary. Subsequent
    paragraphs are statement groups inside the body; they don't straddle
    a definition boundary, they live inside one.
    """
    paragraphs = text.split("\n\n")
    parts: list[str] = []
    cur: list[str] = []
    for p in paragraphs:
        if not p.strip():
            continue
        cur.append(p)
        if len(cur) >= 1:
            chunk = "\n\n".join(cur)
            if tcount(chunk) > cap:
                parts.extend(_split_oversized(chunk, cap, tcount))
            else:
                parts.append(chunk)
            cur = []
    if cur:
        chunk = "\n\n".join(cur)
        if tcount(chunk) > cap:
            parts.extend(_split_oversized(chunk, cap, tcount))
        else:
            parts.append(chunk)
    return [p for p in parts if p.strip()]


def _expand_class_body(
    node: Any, src_bytes: bytes, cap: int, tcount: Callable[[str], int]
) -> list[tuple[str, bool]]:
    """Expand a class_definition into per-method candidates.

    Emits the class header (signature + docstring + class-level attributes
    up to the first method) as one boundary chunk, then each method/nested
    class as its own boundary chunk. Methods retain their leading
    indentation so they remain syntactically recognizable when re-parsed
    (test_no_function_boundary_straddle accepts whitespace-only prefixes).
    """
    body_node = None
    for child in node.children:
        if child.type == "block":
            body_node = child
            break
    if body_node is None:
        text = _slice(src_bytes, node.start_byte, node.end_byte)
        return [(text, True)] if text.strip() else []

    out: list[tuple[str, bool]] = []
    # Header = class_definition.start_byte → first method/inner-class child's start_byte.
    method_types = {
        "function_definition",
        "async_function_definition",
        "decorated_definition",
        "class_definition",
    }
    method_children = [c for c in body_node.children if c.type in method_types]
    if not method_children:
        text = _slice(src_bytes, node.start_byte, node.end_byte)
        return [(text, True)] if text.strip() else []

    header_end = method_children[0].start_byte
    header_text = _slice(src_bytes, node.start_byte, header_end)
    if header_text.strip():
        if tcount(header_text) > cap:
            for piece in _split_oversized(header_text, cap, tcount):
                out.append((piece, True))
        else:
            out.append((header_text, True))

    for method in method_children:
        m_text = _slice(src_bytes, method.start_byte, method.end_byte)
        if not m_text.strip():
            continue
        # Apply paragraph splitting so multi-paragraph methods become
        # multiple chunks. The first paragraph (signature + docstring or
        # first statement group) keeps the function_definition start —
        # so the boundary-straddle invariant still holds: only the first
        # chunk contains a top-level function_definition node, and it
        # starts at byte 0 of that chunk. Subsequent chunks are body
        # statements that don't form top-level definitions.
        for piece in _split_function_paragraphs(m_text, cap, tcount):
            out.append((piece, True))

    return out


def _chunk_nodes(
    nodes: list[Any],
    src_bytes: bytes,
    cap: int,
    tcount: Callable[[str], int],
) -> list[str]:
    """cAST split-then-merge over the top-level child nodes.

    Each definition node becomes its own chunk; classes recurse into their
    bodies so methods are addressable individually (Req-02 chunk-count
    target). Non-boundary nodes (imports, module-level statements,
    comments) merge with neighbours up to ``cap`` tokens. The boundary
    invariant (D-AST-04) holds because boundary-typed candidates are never
    merged into each other or into non-boundary neighbours.
    """
    candidates: list[tuple[str, bool]] = []  # (text, is_boundary)
    for node in nodes:
        text = _slice(src_bytes, node.start_byte, node.end_byte)
        if not text.strip():
            continue
        is_boundary = node.type in BOUNDARY_TYPES
        # Expand class bodies so each method becomes its own chunk.
        if node.type == "class_definition" or (
            node.type == "decorated_definition"
            and any(c.type == "class_definition" for c in node.children)
        ):
            target = node
            if node.type == "decorated_definition":
                # The decorator + class will travel together when we expand:
                # use _expand_class_body on the inner class_definition but
                # prepend the decorator text to the header chunk.
                cls = next(c for c in node.children if c.type == "class_definition")
                expanded = _expand_class_body(cls, src_bytes, cap, tcount)
                if expanded:
                    deco_text = _slice(src_bytes, node.start_byte, cls.start_byte)
                    head_text, _ = expanded[0]
                    expanded[0] = (deco_text + head_text, True)
                    candidates.extend(expanded)
                    continue
                target = cls
            expanded = _expand_class_body(target, src_bytes, cap, tcount)
            if expanded:
                candidates.extend(expanded)
                continue
        if is_boundary:
            # Top-level functions/decorated functions: paragraph-split so
            # multi-paragraph bodies become multiple chunks (Req-02).
            for piece in _split_function_paragraphs(text, cap, tcount):
                candidates.append((piece, True))
            continue
        if tcount(text) > cap:
            for piece in _split_oversized(text, cap, tcount):
                candidates.append((piece, False))
        else:
            candidates.append((text, False))

    # Merge pass — adjacent non-boundary candidates collapse only when
    # BOTH are very small (≤ MERGE_FLOOR tokens). This keeps imports +
    # tiny module-level constants from fragmenting into 1-token chunks
    # while preserving granularity for substantive top-level statements.
    MERGE_FLOOR = 8
    merged: list[tuple[str, bool]] = []
    for cand, is_boundary in candidates:
        if not merged:
            merged.append((cand, is_boundary))
            continue
        prev_text, prev_boundary = merged[-1]
        if is_boundary or prev_boundary:
            merged.append((cand, is_boundary))
            continue
        if tcount(cand) > MERGE_FLOOR or tcount(prev_text) > MERGE_FLOOR:
            merged.append((cand, is_boundary))
            continue
        joined = prev_text + "\n\n" + cand
        if tcount(joined) <= cap:
            merged[-1] = (joined, False)
        else:
            merged.append((cand, is_boundary))
    return [text for text, _ in merged if text.strip()]


def _root_is_parse_error(root: Any) -> bool:
    """Heuristic: tree-sitter never raises but flags syntax issues via
    `has_error` and ERROR child nodes. Treat root.has_error AND
    no usable boundary children as a parse failure."""
    if not getattr(root, "has_error", False):
        return False
    for child in root.children:
        if child.type in BOUNDARY_TYPES:
            return False
    return True


def chunk_tree_sitter_python(
    text: str, *, max_tokens: int | None = None
) -> list[str]:
    """AST-aware Python chunker. See module docstring for design notes."""
    if not text.strip():
        return []

    # D-PKG-02 — lazy import inside the function (NOT module-top) so default
    # users (no opt-in) never pay the tree_sitter wheel cost.
    try:
        import tree_sitter
        import tree_sitter_python
    except ImportError as exc:
        raise RuntimeError(
            "tree_sitter_code chunker requires the ast-chunker extra: "
            "pip install supamem[ast-chunker]"
        ) from exc

    cap = _resolve_max_tokens(max_tokens)

    src_bytes = text.encode("utf-8")
    try:
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))
        tree = parser.parse(src_bytes)
    except Exception:  # noqa: BLE001 — D-AST-03 fallback path
        err_console.print(
            "[supamem.warn]tree_sitter_code: parse error, falling back to markdown_header[/supamem.warn]"
        )
        return chunk_markdown(text)

    if _root_is_parse_error(tree.root_node):
        err_console.print(
            "[supamem.warn]tree_sitter_code: parse error, falling back to markdown_header[/supamem.warn]"
        )
        return chunk_markdown(text)

    chunks = _chunk_nodes(list(tree.root_node.children), src_bytes, cap, _token_count)
    if not chunks:
        # Edge-case safety — never return [] for non-empty input.
        return chunk_markdown(text)
    return chunks
