"""End-to-end rerank-on integration test against the REAL mxbai model.

Gated on ``SUPAMEM_INTEGRATION_RERANKER=1`` to keep CI fast; opt-in run on
a single dev machine per release. Plan 08-03 fills in the body.

Preconditions on a dev machine:
- Qdrant running on the URL named in ``SUPAMEM_QDRANT_URL`` (defaults to
  ``http://localhost:6333``).
- Model already cached (run ``supamem install`` once first), or the test
  pays the ~1 GB cold-fetch tax.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SUPAMEM_INTEGRATION_RERANKER") != "1",
    reason="opt-in: set SUPAMEM_INTEGRATION_RERANKER=1",
)


_FIXTURE_CORPUS = [
    ("d1", "def parse_args(): return argparse.ArgumentParser().parse_args()"),
    ("d2", "class Reranker: def rerank(self, q, c): return c"),
    ("d3", "import torch.nn as nn"),
    ("d4", "Authentication uses bearer tokens via the Authorization header."),
    ("d5", "The cache lives under ~/.cache/supamem on Linux."),
    ("d6", "function defines class composition over inheritance"),
    ("d7", "function-style class definitions for plugin protocols"),
    ("d8", "Yesterday I went grocery shopping and it was raining."),
    ("d9", "The protocol class declares a `rerank` method signature."),
    ("d10", "Python 3.12 introduced PEP 695 generic syntax."),
]


def _index_corpus(backend, collection: str, docs: list[tuple[str, str]]) -> None:
    """Index a tiny corpus through the indexer (writes to Qdrant)."""
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
    from fastembed import SparseTextEmbedding, TextEmbedding

    client = QdrantClient(url=backend.config.qdrant_url, check_compatibility=False)
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config={"dense": qmodels.VectorParams(size=384, distance=qmodels.Distance.COSINE)},
            sparse_vectors_config={"sparse": qmodels.SparseVectorParams()},
        )
    dense = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    sparse = SparseTextEmbedding("Qdrant/bm25")
    points = []
    for doc_id, text in docs:
        d_vec = list(next(dense.embed([text])))
        s_vec = next(sparse.embed([text]))
        points.append(
            qmodels.PointStruct(
                id=doc_id,
                vector={
                    "dense": [float(x) for x in d_vec],
                    "sparse": qmodels.SparseVector(
                        indices=[int(i) for i in s_vec.indices],
                        values=[float(v) for v in s_vec.values],
                    ),
                },
                payload={"document": text, "source": f"{doc_id}.md"},
            )
        )
    client.upsert(collection_name=collection, points=points, wait=True)


def test_rerank_on_rescores_tuned_hybrid_candidates() -> None:
    """rerank-on output orderings differ from rerank-off; rerank_score is set."""
    from supamem.config import ResolvedConfig
    from supamem.retrieval.tuned_hybrid import TunedHybridBackend

    qdrant_url = os.environ.get("SUPAMEM_QDRANT_URL", "http://localhost:6333")
    collection = f"rerank_pipeline_test_{uuid.uuid4().hex[:8]}"

    cfg_off = ResolvedConfig(
        qdrant_url=qdrant_url, collection=collection, reranker_name="off"
    )
    backend_off = TunedHybridBackend(config=cfg_off)
    _index_corpus(backend_off, collection, _FIXTURE_CORPUS)
    out_off = backend_off.query("function defines class", k=8)

    cfg_on = ResolvedConfig(
        qdrant_url=qdrant_url, collection=collection, reranker_name="mxbai_v2"
    )
    backend_on = TunedHybridBackend(config=cfg_on)
    out_on = backend_on.query("function defines class", k=8)

    # Cleanup the test collection.
    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=qdrant_url, check_compatibility=False).delete_collection(collection)
    except Exception:
        pass

    assert [c.id for c in out_off] != [c.id for c in out_on], (
        "rerank-on must reorder at least one pair vs rerank-off"
    )
    for c in out_on:
        assert c.rerank_score is not None, (
            f"rerank-on chunk {c.id} missing rerank_score"
        )
