"""End-to-end rerank-on integration test against the REAL mxbai model.

Gated on `SUPAMEM_INTEGRATION_RERANKER=1` to keep CI fast; opt-in run on
a single dev machine per release. RED skeleton in Wave 0; impl in Plan 08-03.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("SUPAMEM_INTEGRATION_RERANKER") != "1",
        reason="opt-in: set SUPAMEM_INTEGRATION_RERANKER=1",
    ),
    pytest.mark.xfail(
        reason="RED skeleton -- implementation lands in Plan 08-03",
        strict=False,
    ),
]


def test_rerank_on_rescores_tuned_hybrid_candidates():
    # Plan 08-03 fills this in: spin up Qdrant, index a tiny corpus,
    # run rerank-off and rerank-on, assert orderings differ and scores
    # come from the cross-encoder.
    raise NotImplementedError
