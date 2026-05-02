"""Single Qdrant Filter construction site for the ``where`` parameter (Phase 7 D-03).

The retrieval ``where`` contract (D-02): multiple keys = AND (joined under
``must=``); single string value = ``MatchValue`` (exact equality); list value
= ``MatchAny`` (OR within the list); empty/None input = no where clauses.

This module is the SINGLE construction site so dense + sparse Prefetch arms
in ``tuned_hybrid`` cannot drift, and so Phase 9 (valid_to) / Phase 11
(filtered_dense) extend the same dispatcher rather than reimplementing it.

Phase 9 (valid_to) extends this dispatcher with an always-on temporal
sub-filter built via ``IsEmptyCondition`` (NOT ``IsNullCondition`` —
Qdrant#5342: ``IsNull`` does NOT match missing payload fields, only explicit
nulls; using ``IsNull`` here would silently filter out every legacy
pre-Phase-9 point). Indexer-side scroll callers and diagnostic count probes
opt out via ``temporal=False`` (D-FILTER-02).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

from qdrant_client.http import models as qmodels

WhereDict = dict[str, Union[str, list[str]]]


def build_qdrant_filter(
    where: Optional[WhereDict],
    *,
    temporal: bool = True,
    now: Optional[datetime] = None,
) -> Optional[qmodels.Filter]:
    """Build a Qdrant Filter combining always-on temporal validity with optional ``where``.

    Phase 9 D-FILTER-01..03 + D-NULL-01 (corrected to IsEmpty per RESEARCH §R-1).

    The temporal sub-filter is ``should=[IsEmpty(valid_to), valid_to > now]`` —
    a NESTED Filter inside the outer ``must=`` list, AND-ing with any
    caller-provided where clauses.

    Args:
        where: User-facing where dict (D-02 contract). ``None`` or empty = no
            where clauses applied.
        temporal: When True (default), prepend the temporal sub-filter so all
            retrieval backends (``tuned_hybrid`` both arms, ``dense``, ``bm25``,
            ``qdrant_find``, ``dual_memory_search``) inherit it for free.
            Indexer-side scroll callers (``_reclassify_sweep``,
            ``_close_validity_window``, ``_eager_validity_migration``,
            ``_gc_sweep``) and ``doctor`` temporal-panel count probes pass
            ``temporal=False`` to bypass it (D-FILTER-02).
        now: Optional ``datetime`` injection seam for deterministic tests.
            Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        ``Filter(must=[...])`` when at least one clause exists; ``None`` when
        both ``temporal=False`` AND ``where`` is empty/None (preserves the
        unfiltered fast path for diagnostic callers).
    """
    must: list = []

    if temporal:
        now_iso = (now or datetime.now(timezone.utc)).isoformat()
        must.append(
            qmodels.Filter(
                should=[
                    qmodels.IsEmptyCondition(
                        is_empty=qmodels.PayloadField(key="valid_to")
                    ),
                    qmodels.FieldCondition(
                        key="valid_to",
                        range=qmodels.DatetimeRange(gt=now_iso),
                    ),
                ]
            )
        )

    if where:
        for key, val in where.items():
            if isinstance(val, list):
                must.append(
                    qmodels.FieldCondition(
                        key=key, match=qmodels.MatchAny(any=list(val))
                    )
                )
            else:
                must.append(
                    qmodels.FieldCondition(
                        key=key, match=qmodels.MatchValue(value=val)
                    )
                )

    return qmodels.Filter(must=must) if must else None
