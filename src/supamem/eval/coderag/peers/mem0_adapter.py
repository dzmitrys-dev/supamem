"""mem0 peer adapter for the coderag suite.

Plan 15-D Task D1.

Critical locks (verified by tests + grep gates in 15-D-PLAN acceptance):

- A-D-DEF-02 + INV-A2: mem0 owns its OWN Qdrant collection
  (``supamem_eval_coderag_mem0``). NEVER writes into
  ``supamem_eval_coderag`` — that is the supamem-side bench collection
  whose schema mem0 would corrupt (Pitfall 7: mem0 owns its chunking,
  embedding, and payload shape).
- D-DEF-02: single canonical default config; NO tuning matrix; NO
  env-var override. The config is a Python literal so the surface that
  produced numbers is reproducible from the source tree.
- D-SCOPE-05 carry: this module imports nothing from
  ``supamem.indexer.*``. The corpus walk is reused via
  :func:`supamem.eval.coderag.corpus.walk_corpus`; mem0 owns
  chunking/embedding/payload schema.
- ``infer=False`` on every ``Memory.add``: research delta — without it
  we score mem0's LLM-extraction quality (and pay for OpenAI per rerun)
  instead of retrieval quality.
- Lazy ``mem0`` import inside :class:`Mem0PeerAdapter.__init__`: keeps
  ``import supamem.eval.coderag.peers`` cheap and dependency-light when
  ``mem0ai`` is not installed.

Search-result shape (verified at 2026-05-06 against mem0ai docs via
Context7 ``/mem0ai/mem0``): ``{id, memory, metadata, score}``. We read
``metadata`` and ``score``; the ``memory`` text is unused (we only need
``doc_id`` for gold-set scoring).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supamem.console import err_console
from supamem.eval.coderag.corpus import walk_corpus

# A-D-DEF-02 + INV-A2: pinned to keep mem0's writes off the supamem
# bench collection. Tests verify ``MEM0_COLLECTION != CODERAG_COLLECTION``.
MEM0_COLLECTION = "supamem_eval_coderag_mem0"

# D-DEF-02: single canonical default config (Python literal, no env-var
# override). Mirrors the supamem bench-side embedder choice
# (``all-MiniLM-L6-v2`` — the default fastembed dense model) so the peer
# row's retrieval quality is comparable, not an apples-to-oranges embedder
# bake-off.
#
# 16-E gap-closure: ``llm.provider="ollama"`` was added so mem0 v2.0.1's
# eager LlmFactory.create(...) at Memory.__init__ does NOT instantiate an
# OpenAI client (which would require OPENAI_API_KEY at construction even
# though every ``add()`` call passes ``infer=False``). The OllamaLLM init
# is purely local — Client(host=...) opens no socket — so construction
# succeeds even when Ollama isn't running. ``infer=False`` short-circuits
# before any LLM provider call, so the eval-only path never invokes the
# model. Reproducibility contract: the canonical default is now Ollama +
# ``llama3.2:3b`` (matches dev workstation); switching providers requires
# a source-tree commit, not an env-var flip (D-DEF-02 preserved).
# MiniLM-L6-v2 produces 384-dim vectors. mem0's qdrant adapter has its OWN
# ``embedding_model_dims`` parameter (default 1536, OpenAI shape) that drives
# collection creation INDEPENDENTLY of the ``embedder`` config — must match
# the embedder's actual dim or every search returns ``UnexpectedResponse``.
_MINILM_DIM = 384

MEM0_CONFIG: dict[str, Any] = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "localhost",
            "port": 6333,
            "collection_name": MEM0_COLLECTION,
            "embedding_model_dims": _MINILM_DIM,
        },
    },
    "embedder": {
        "provider": "huggingface",
        "config": {
            "model": "sentence-transformers/all-MiniLM-L6-v2",
        },
    },
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "llama3.2:3b",
            "ollama_base_url": "http://localhost:11434",
        },
    },
}

USER_ID = "coderag"


@dataclass
class Mem0Hit:
    """Adapter Hit shape compatible with ``runner._build_run``.

    Mirrors the duck-typed ``hit.score`` + ``hit.payload["doc_id"]``
    contract that ``_run_coderag`` consumes from the supamem-side backend.
    """

    score: float
    payload: dict[str, Any]


def _detect_axis(rel_path: str) -> str:
    """Mirror ``ingest._detect_axis`` so mem0 ingest tags axis identically.

    We do NOT import the supamem-side helper to keep this module decoupled
    from the supamem ingest module's surface (that surface's evolution must
    not silently shift mem0's ingest behavior).
    """
    if rel_path.startswith("docs/adr/") and rel_path.endswith(".md"):
        return "decision_rationale"
    return "code_fact"


class Mem0PeerAdapter:
    """mem0 peer adapter exposing supamem-runner-compatible ingest + query.

    Owns its OWN Qdrant collection (``supamem_eval_coderag_mem0``).
    Constructed lazily — the ``from mem0 import Memory`` is inside
    ``__init__`` so importing this module without ``mem0ai`` installed
    never raises (ImportError surfaces only when someone actually
    constructs the adapter).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        from copy import deepcopy  # noqa: PLC0415

        from mem0 import Memory  # lazy: optional dep — peers-mem0 extras
        from qdrant_client import QdrantClient  # noqa: PLC0415

        cfg = deepcopy(config or MEM0_CONFIG)

        # mem0 v2.0.x's qdrant adapter does NOT expose a ``timeout`` config
        # knob; the default httpx timeout (~5s) is too short for the bulk-
        # ingest path where each ``add()`` lemmatizes + embeds + writes.
        # Inject a pre-built QdrantClient via the adapter's ``client=``
        # parameter (see qdrant.py:30-58 — ``client`` short-circuits the
        # internal QdrantClient(**params) construction).
        vs_cfg = cfg["vector_store"]["config"]
        if "client" not in vs_cfg:
            host = vs_cfg.get("host", "localhost")
            port = vs_cfg.get("port", 6333)
            vs_cfg["client"] = QdrantClient(host=host, port=port, timeout=60)

        self._memory = Memory.from_config(cfg)
        self._collection = cfg["vector_store"]["config"]["collection_name"]
        # INV-A2 runtime gate. If the caller passed a custom ``config`` with a
        # different ``collection_name``, refuse to construct — mem0 must not
        # write into the supamem bench collection (or anywhere except its own).
        assert self._collection == MEM0_COLLECTION, (
            f"INV-A2: mem0 collection MUST be {MEM0_COLLECTION!r}, "
            f"got {self._collection!r}"
        )

    def ingest(self, repos: Iterable[tuple[str, Path]]) -> int:
        """Ingest the SAME source documents as the supamem-side ingest.

        Reuses :func:`walk_corpus` so the file-allowlist + exclude-glob
        contract (15-B Task B1, Pitfall 1 mitigation) is shared. mem0 owns
        chunking, embedding, and payload schema — we just hand it the raw
        text + metadata.

        ``infer=False`` skips mem0's LLM-extraction step: we want to score
        retrieval quality, not extraction quality, and the live integration
        rerun must not silently rack up OpenAI cost per run.

        Returns the number of records added.
        """
        count = 0
        for slug, repo_root in repos:
            for path in walk_corpus(repo_root):
                rel = path.relative_to(repo_root).as_posix()
                axis = _detect_axis(rel)
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    err_console.print(
                        f"[supamem.warn]coderag-mem0: skip non-utf8 file "
                        f"{slug}:{rel}[/supamem.warn]"
                    )
                    continue
                self._memory.add(
                    messages=[{"role": "user", "content": text}],
                    user_id=USER_ID,
                    metadata={"doc_id": rel, "repo": slug, "axis": axis},
                    infer=False,
                )
                count += 1
        err_console.print(
            f"[supamem.info]coderag-mem0: ingested {count} records into "
            f"{MEM0_COLLECTION}[/supamem.info]"
        )
        return count

    def query(
        self,
        text: str,
        *,
        k: int = 20,
        where: dict[str, Any] | None = None,
    ) -> list[Mem0Hit]:
        """Search mem0 → list[Mem0Hit] in runner-compatible shape.

        ``where={"repo": [...]}`` is honored as a metadata-side filter
        (best-effort: applied to results AFTER mem0 returns them, since
        D-DEF-02 forbids per-call filter tuning beyond the canonical
        config). The runner's three-column-axis reporting drives the
        ``where`` argument; mem0 sees the full pool and we filter
        post-hoc.
        """
        # mem0 v2.0.0 breaking changes: search() rejects ``user_id`` as a
        # top-level kwarg (must go in ``filters``), renamed ``limit`` →
        # ``top_k``, AND wraps the hit list in a ``{"results": [...]}`` envelope
        # (was a bare list in v1). ``add()`` is unchanged — entity IDs stay as
        # first-class kwargs on the write side.
        raw = self._memory.search(
            query=text,
            filters={"user_id": USER_ID},
            top_k=k,
        )
        # v2 envelope: {"results": [...]}; tolerate v1 bare-list for resilience.
        if isinstance(raw, dict):
            results = raw.get("results") or []
        else:
            results = raw or []
        hits: list[Mem0Hit] = []
        repo_filter: list[str] | None = None
        if where and "repo" in where:
            repo_filter = list(where["repo"])
        for r in results:
            meta = r.get("metadata") or {}
            if repo_filter is not None and meta.get("repo") not in repo_filter:
                continue
            hits.append(
                Mem0Hit(
                    score=float(r.get("score", 0.0)),
                    payload={
                        "doc_id": meta.get("doc_id"),
                        "repo": meta.get("repo"),
                        "axis": meta.get("axis"),
                    },
                )
            )
        return hits


__all__ = [
    "MEM0_COLLECTION",
    "MEM0_CONFIG",
    "USER_ID",
    "Mem0Hit",
    "Mem0PeerAdapter",
]
