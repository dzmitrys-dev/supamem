"""filtered_dense — scoped+capped wrapper over TunedHybridBackend (Phase 11 FILT-01/02).

Despite the historical name kept for ROADMAP/REQUIREMENTS literal compliance,
this is a HYBRID wrapper, not a dense-only path. It composes the existing
``where`` filter dispatcher (Phase 7 D-03 / Phase 9 D-FILTER-01 / Phase 11
D-PFX-03 + D-VT-01..02) with a backend-level per-hit preview cap.

NEVER touches ``tuned_hybrid.py`` — byte-identical guarantee for the
unwrapped path is the FILT-02 lock (D-FD-04).

Pitfall 7 (RESEARCH-A §5) — the MCP server (``mcp_server.py:218-238``) MUST
keep reading ``h.text`` for transport-cap truncation, NEVER ``h.preview``.
The backend-level preview here is independent of and composes ON TOP OF the
transport-level cap (D-PREV-03). Two caps; one raw input (``h.text``); never
composed. Re-routing the MCP server to read ``h.preview`` creates the
double-ellipsis bug — the regression test
``test_pitfall_7_mcp_server_anti_edit`` locks this invariant.
"""
from __future__ import annotations

from typing import Optional

from supamem.config import ResolvedConfig
from supamem.retrieval.filters import WhereDict
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk


class FilteredDenseBackend:
    """Wrapper around TunedHybridBackend that populates RetrievedChunk.preview.

    Composition (D-FD-01): ``self._inner.query(text, k, where=where)`` → for
    each returned chunk, set ``chunk.preview`` to a truncated excerpt of
    ``chunk.text``. Truncation rule mirrors ``mcp_server.py:227`` byte-for-byte
    (D-PREV-02): ``text[: max(0, cap - 1)] + "…"`` when ``len(text) > cap``,
    otherwise the full text.

    ``preview_chars = 0`` disables truncation entirely (preview becomes the
    full document text — useful for MCP transports that want the backend to
    surface full content with the transport-level cap as the only ceiling).
    """

    name = "filtered_dense"

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config
        # Composition, NOT subclassing — keeps tuned_hybrid.py byte-identical
        # (D-FD-04) and lets future per-arm changes land in the inner module
        # without rippling here.
        self._inner = TunedHybridBackend(config=config)
        # D-PREV-01 — preview_chars >= 0 (validated at config load time);
        # 0 disables truncation (preview becomes the full text).
        self._preview_chars: int = int(
            getattr(config, "retrieval_filtered_dense_preview_chars", 240)
        )

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: Optional[WhereDict] = None,
    ) -> list[RetrievedChunk]:
        hits = self._inner.query(text, k, where=where)
        cap = self._preview_chars
        # D-PREV-01 cap=0 disable branch — preview = full text (no ellipsis).
        if cap <= 0:
            return [h.model_copy(update={"preview": h.text}) for h in hits]
        out: list[RetrievedChunk] = []
        for h in hits:
            t = h.text or ""
            # Mirror mcp_server.py:227 byte-for-byte: reserve one codepoint
            # for the ellipsis so total preview length stays <= cap.
            if len(t) > cap:
                preview = t[: max(0, cap - 1)] + "…"
            else:
                preview = t
            # frozen=True forbids mutation; model_copy creates a new instance
            # so the inner backend's chunks are never mutated by reference.
            out.append(h.model_copy(update={"preview": preview}))
        return out

    @classmethod
    def kind(cls) -> str:
        return "filtered_dense"


__all__ = ["FilteredDenseBackend"]
