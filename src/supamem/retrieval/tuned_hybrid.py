"""Arm 6: ``tuned_hybrid`` — Qdrant native sparse-dense FusionQuery (D-25 lock).

Composition (Phase 80.1 D-19 / D-25):
    T-1 chunker (markdown_header) +
    T-2 RRF fusion over (dense MiniLM, sparse BM25) +
    T-4 recency boost +
    T-5 cosine dedup +
    T-8 token-budget truncation.

Ported verbatim from ``softchat/scripts/eval/adapters/tuned_hybrid.py`` with
two adaptations: the collection name now comes from ``ResolvedConfig`` (not
an env var module-level read), and the forbidden-collection guard runs at
instance construction (not import) so config-driven runtimes can opt in.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from qdrant_client.http import models as qmodels

from supamem.config import ResolvedConfig
from supamem.qdrant_collection import assert_collection_exists_for_read
from supamem.retrieval.filters import WhereDict, build_qdrant_filter
from supamem.retrieval.types import RetrievedChunk

try:
    from fastembed import SparseTextEmbedding
    _SPARSE_AVAILABLE = True
    _SPARSE_IMPORT_ERROR: BaseException | None = None
except Exception as exc:  # pragma: no cover
    SparseTextEmbedding = None  # type: ignore[assignment, misc]
    _SPARSE_AVAILABLE = False
    _SPARSE_IMPORT_ERROR = exc

try:
    from fastembed import TextEmbedding
except Exception:  # pragma: no cover
    TextEmbedding = None  # type: ignore[assignment, misc]

# Locked Phase 80.1 retrieval params — changing any of these invalidates the
# −78.5% token bench against `baseline_union`.
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
PREFETCH_LIMIT = 20  # each side fetches 20 → fusion picks top-k from union
DEDUP_COSINE_THRESHOLD = 0.97
TOKEN_BUDGET = 1500

# Collections we refuse to write into without explicit opt-in.
_FORBIDDEN_COLLECTIONS = {"dev_memory", "dev_memory_tuned"}

DEFAULT_DENSE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _approx_token_count(text: str) -> int:
    """Cheap token estimate for the T-8 budget loop."""
    return max(1, len(text) // 4)


def _recency_multiplier(updated_at: str | None) -> float:
    """T-4: small boost for recently-updated docs (cap +10%)."""
    if not updated_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 1.0
    age_days = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
    return 1.0 + 0.1 * math.exp(-age_days / 30.0)


def _decay_multiplier(*, age_days: float, alpha: float, half_life_days: float) -> float:
    """Phase 9 D-DECAY-01 — transcript-only decay multiplier.

    Pure math: ``alpha + (1 - alpha) * 0.5 ** (age_days / half_life_days)``.

    Worked example (locked TEMP-03 defaults α=0.7, hl=14d):
    - age = 0  → 1.0
    - age = 14 → 0.85
    - age = 28 → 0.775
    - age → ∞ → α (0.7 floor)

    Used by:
    - :func:`_apply_transcript_decay` production path
    - ``tests/test_retrieval_temporal.py::test_decay_multiplier_values`` directly
    """
    age_days = max(0.0, float(age_days))
    return alpha + (1.0 - alpha) * (0.5 ** (age_days / half_life_days))


def _apply_transcript_decay(
    candidates: list[tuple[str, float, dict, list[float] | None]],
    *,
    enabled: bool,
    alpha: float,
    half_life_days: float,
) -> None:
    """Phase 9 D-COMPOSE-09 — mutate candidate scores in-place for transcripts only.

    When ``enabled`` is False this is a no-op — code/ADR/doc rankings stay
    BYTE-IDENTICAL to the no-decay path (TEMP-03 success criterion #3).

    When ``enabled`` is True, iterate ``candidates`` and for any chunk whose
    ``payload.get("chunker") == "transcript"`` AND has a parsable ISO-8601
    ``payload["valid_from"]``, multiply ``score`` by
    :func:`_decay_multiplier`. Malformed ``valid_from`` strings are caught
    (Threat-V7) and the raw score is preserved — retrieval never crashes on
    payload tampering.

    After processing, the list is re-sorted by score descending (decay can
    change relative ordering across the transcript subset; non-transcript
    rows are stable per Python's stable sort).

    The list is mutated in place via index reassignment (tuples are
    immutable). The same helper is exercised by the production
    :meth:`TunedHybridBackend.query` call site AND by
    ``tests/test_retrieval_temporal.py`` byte-identity / transcript-only /
    malformed-payload tests so the test path matches production exactly.
    """
    if not enabled:
        return
    now_utc = datetime.now(timezone.utc)
    for i, (doc_id, score, payload, vec) in enumerate(candidates):
        if payload.get("chunker") != "transcript":
            continue
        vf = payload.get("valid_from")
        if not vf:
            continue
        try:
            ts = datetime.fromisoformat(str(vf).replace("Z", "+00:00"))
        except ValueError:
            # Threat-V7: malformed valid_from → keep raw score, never crash.
            continue
        age_days = max(0.0, (now_utc - ts).total_seconds() / 86400.0)
        mult = _decay_multiplier(
            age_days=age_days, alpha=alpha, half_life_days=half_life_days
        )
        candidates[i] = (doc_id, score * mult, payload, vec)
    candidates.sort(key=lambda t: t[1], reverse=True)


class TunedHybridBackend:
    """Qdrant native sparse-dense FusionQuery (RRF) — supamem D-25 lock."""

    name = "tuned_hybrid"

    def __init__(self, *, config: ResolvedConfig, minimal_setup: bool = False) -> None:
        if (
            config.collection in _FORBIDDEN_COLLECTIONS
            and not getattr(config, "allow_legacy_collection", False)
        ):
            raise RuntimeError(
                f"supamem: collection={config.collection!r} is forbidden — "
                "this name is reserved for legacy production paths. Pick a "
                "different collection or set "
                "ResolvedConfig.allow_legacy_collection=True to opt in."
            )
        self.config = config
        self._client: Any | None = None
        self._dense: Any | None = None
        self._sparse: Any | None = None
        # D-POOL: cache the reranker plugin instance on the backend so a
        # process-long retrieval session reuses one model load. load_reranker
        # constructs a fresh wrapper per call; without this cache the
        # wrapper's _ensure() lazy-load reloads the model every query.
        self._reranker: Any | None = None
        self._reranker_name: str | None = None
        self._minimal_setup = minimal_setup

    def _ensure(self):
        if not _SPARSE_AVAILABLE:
            raise RuntimeError(
                "supamem.tuned_hybrid requires fastembed sparse support "
                "(SparseTextEmbedding). Install fastembed[nlp] (or upgrade to "
                "fastembed>=0.8 where extras are no-ops). "
                f"Original import error: {_SPARSE_IMPORT_ERROR!r}"
            )
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(
                url=self.config.qdrant_url,
                api_key=self.config.qdrant_api_key or None,
                check_compatibility=False,
                timeout=60,
            )
        if self._dense is None:
            self._dense = TextEmbedding(DEFAULT_DENSE_MODEL)
        if self._sparse is None:
            self._sparse = SparseTextEmbedding(DEFAULT_SPARSE_MODEL)
        return self._client, self._dense, self._sparse

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: Optional[WhereDict] = None,
    ) -> list[RetrievedChunk]:
        client, dense, sparse = self._ensure()
        assert_collection_exists_for_read(client, self.config.collection)
        dense_q = [float(x) for x in next(dense.embed([text]))]
        sparse_q = next(sparse.embed([text]))

        # Phase 8 (D-COMPOSE-03): rerank-on/off branch. ``"off"`` returns
        # ``None`` from the loader without iterating entry-points so the
        # off-path is byte-identical to pre-Phase-8 (RERANK-01).
        from supamem.rerankers import load_reranker  # noqa: PLC0415

        reranker_name = getattr(self.config, "reranker_name", "off")
        # D-POOL: reuse cached reranker if name unchanged. load_reranker
        # constructs a fresh wrapper each call — caching here keeps the
        # underlying model loaded for the lifetime of the backend.
        if reranker_name == "off":
            reranker = None
        elif (
            self._reranker is not None and self._reranker_name == reranker_name
        ):
            reranker = self._reranker
        else:
            try:
                reranker = load_reranker(reranker_name, self.config)
            except LookupError:
                # Fail-soft: treat unknown reranker as off; ResolvedConfig's
                # validation gate (load_config) is the canonical fail-closed
                # surface — at backend.query() time we never abort retrieval.
                reranker = None
            self._reranker = reranker
            self._reranker_name = reranker_name

        # D-POOL-01: widen prefetch only when reranker is on.
        prefetch_limit = (
            getattr(self.config, "reranker_prefetch_per_arm", PREFETCH_LIMIT)
            if reranker
            else PREFETCH_LIMIT
        )

        # D-03: build Filter ONCE; thread the SAME object to both Prefetch
        # arms AND top-level query_filter (defense-in-depth per RESEARCH
        # §Pattern 3 — applying it to only one arm causes RRF fusion to
        # drown filtered hits in unfiltered ones, RESEARCH Pitfall 5).
        qf = build_qdrant_filter(where)

        resp = client.query_points(
            collection_name=self.config.collection,
            prefetch=[
                qmodels.Prefetch(
                    query=dense_q,
                    using=DENSE_VECTOR_NAME,
                    limit=prefetch_limit,
                    filter=qf,
                ),
                qmodels.Prefetch(
                    query=qmodels.SparseVector(
                        indices=[int(i) for i in sparse_q.indices],
                        values=[float(v) for v in sparse_q.values],
                    ),
                    using=SPARSE_VECTOR_NAME,
                    limit=prefetch_limit,
                    filter=qf,
                ),
            ],
            query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
            query_filter=qf,
            limit=max(k * 2, 10),
            with_payload=True,
            with_vectors=True,
        )

        # Build raw candidates preserving payload + vec for downstream T-5 dedup.
        raw: list[tuple[str, float, dict, list[float] | None]] = []
        for hit in resp.points or []:
            payload = hit.payload or {}
            vec: list[float] | None = None
            if hit.vector:
                if isinstance(hit.vector, dict):
                    raw_v = hit.vector.get(DENSE_VECTOR_NAME)
                else:
                    raw_v = hit.vector
                if raw_v is not None:
                    try:
                        vec = [float(x) for x in raw_v]
                    except (TypeError, ValueError):
                        vec = None
            raw.append((str(hit.id), float(hit.score), payload, vec))

        adjusted: list[tuple[str, float, dict, list[float] | None]]
        if reranker:
            # D-COMPOSE-01/02: rerank REPLACES score, T-4 recency SKIPPED,
            # T-5 dedup + T-8 budget run AFTER on the reranked ordering.
            pre_rerank = [
                RetrievedChunk(
                    id=doc_id,
                    text=str(payload.get("document") or ""),
                    score=score,
                    payload=payload,
                    source_path=payload.get("source") or payload.get("file_path"),
                    file_path=payload.get("file_path"),
                )
                for doc_id, score, payload, _vec in raw
            ]
            # Latency telemetry (D-CPU-02): wrap the rerank call for the
            # doctor panel's p50/p95 surface. Failures swallowed (non-essential).
            import time as _time  # noqa: PLC0415

            t0 = _time.perf_counter()
            try:
                reranked = reranker.rerank(text, pre_rerank)
            except Exception as _rerank_exc:
                # Plugin failure → fall through to off-path semantics
                # (T-RERANK-INVAR mitigation: never silently drop hits).
                # Surface exception class+message so plugin authors and
                # bench runs can see WHY rerank failed, not just that it did.
                import traceback as _tb  # noqa: PLC0415

                from supamem.console import err_console  # noqa: PLC0415

                err_console.print(
                    f"[supamem.warn]reranker raised "
                    f"({type(_rerank_exc).__name__}: {_rerank_exc}) "
                    "— falling back to off-branch"
                )
                err_console.print(_tb.format_exc())
                reranker = None
                reranked = []
            elapsed_ms = (_time.perf_counter() - t0) * 1000.0

            # T-RERANK-INVAR: list MUST NOT grow.
            if reranker is not None and len(reranked) > len(pre_rerank):
                from supamem.console import err_console  # noqa: PLC0415

                err_console.print(
                    "[supamem.warn]reranker returned more chunks than provided; "
                    "falling back to off-branch behavior"
                )
                reranker = None

            if reranker is not None:
                # Record latency (Welford + deque). Failures swallowed.
                try:
                    from supamem.stats.counter import bump  # noqa: PLC0415

                    bump("rerank", "rerank_latency_ms", 0, elapsed_ms)
                except Exception:
                    pass
                vec_by_id = {doc_id: vec for doc_id, _s, _p, vec in raw}
                payload_by_id = {doc_id: pl for doc_id, _s, pl, _v in raw}
                adjusted = [
                    (
                        c.id,
                        float(c.score),
                        payload_by_id.get(c.id, c.payload or {}),
                        vec_by_id.get(c.id),
                    )
                    for c in reranked
                ]
                # Reranker owns the ordering — no resort.

        if not reranker:
            # Pre-Phase-8 path: T-4 recency multiplier + sort.
            adjusted = []
            for doc_id, score, payload, vec in raw:
                mult = _recency_multiplier(payload.get("updated_at"))
                adjusted.append((doc_id, score * mult, payload, vec))
            adjusted.sort(key=lambda t: t[1], reverse=True)

        # ── Phase 9 D-COMPOSE-09 — transcript-only post-rerank decay ──────────
        # Runs AFTER (rerank | T-4) and BEFORE T-5 dedup, OUTSIDE both
        # branches (D-COMPOSE-10 orthogonal pass — Phase 8 D-COMPOSE-01
        # rerank-skips-T-4 stays preserved verbatim above). Code/ADR/doc
        # scores left BYTE-IDENTICAL when knob OFF (TEMP-03 success
        # criterion #3). Same helper used by tests/test_retrieval_temporal.py
        # so the test path is byte-equivalent to production.
        _apply_transcript_decay(
            adjusted,
            enabled=getattr(
                self.config, "recency_per_source_transcript_enabled", False
            ),
            alpha=getattr(
                self.config, "recency_per_source_transcript_alpha", 0.7
            ),
            half_life_days=getattr(
                self.config, "recency_per_source_transcript_half_life_days", 14.0
            ),
        )

        # T-5 cosine dedup + T-8 token-budget truncation.
        kept_vecs: list[list[float]] = []
        out: list[RetrievedChunk] = []
        cumulative_tokens = 0
        for doc_id, score, payload, vec in adjusted:
            if vec and any(
                _cosine(vec, kv) >= DEDUP_COSINE_THRESHOLD for kv in kept_vecs
            ):
                continue
            text_body = str(payload.get("document") or "")
            tokens = _approx_token_count(text_body)
            if cumulative_tokens + tokens > TOKEN_BUDGET and out:
                break
            cumulative_tokens += tokens
            if vec:
                kept_vecs.append(vec)
            out.append(
                RetrievedChunk(
                    id=doc_id,
                    text=text_body,
                    score=score,
                    source_path=payload.get("source") or payload.get("file_path"),
                    file_path=payload.get("file_path"),
                    payload=payload,
                    rerank_score=score if reranker else None,
                )
            )
            if len(out) >= k:
                break
        return out
