"""Single Qdrant Filter construction site for the ``where`` parameter (Phase 7 D-03).

The retrieval ``where`` contract (D-02): multiple keys = AND (joined under
``must=``); single string value = ``MatchValue`` (exact equality); list value
= ``MatchAny`` (OR within the list); empty/None input = no filter.

This module is the SINGLE construction site so dense + sparse Prefetch arms
in ``tuned_hybrid`` cannot drift, and so Phase 9 (valid_to) / Phase 11
(filtered_dense) extend the same dispatcher rather than reimplementing it.
"""
from __future__ import annotations

from typing import Optional, Union

from qdrant_client.http import models as qmodels

WhereDict = dict[str, Union[str, list[str]]]


def build_qdrant_filter(where: Optional[WhereDict]) -> Optional[qmodels.Filter]:
    """Translate a user-facing ``where`` dict into a ``qmodels.Filter``.

    Returns ``None`` when ``where`` is empty or ``None`` so callers can
    skip filter wiring entirely. Preserves insertion order of ``where``
    keys in the resulting ``must`` list.
    """
    if not where:
        return None
    must: list[qmodels.FieldCondition] = []
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
