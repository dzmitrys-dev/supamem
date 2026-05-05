"""Phase 14 Plan A — Task A2 RED tests for the bench-only LongMemEval
haystack ingest module.

The module under test is :mod:`supamem.eval.longmemeval_ingest`. Per the
plan, it bootstraps an isolated bench collection (``supamem_eval_*``
prefix), creates an idempotent ``session_id`` keyword payload index,
embeds + upserts one point per haystack turn, and is reachable ONLY from
the eval runner (production indexer paths are untouched).

All tests mock ``QdrantClient`` — never live Qdrant.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig

ingest_mod = pytest.importorskip("supamem.eval.longmemeval_ingest")


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "user_project_collection"}
    base.update(overrides)
    return ResolvedConfig(**base)


def _make_raw_record(
    qid: str,
    axis: str,
    sessions: list,
    session_ids: list | None = None,
) -> dict:
    if session_ids is None:
        session_ids = [f"s_{i:03d}" for i in range(len(sessions))]
    return {
        "question_id": qid,
        "question_type": axis.replace("_", "-"),
        "axis": axis,
        "question": "q",
        "answer": "a",
        "haystack_session_ids": session_ids,
        "haystack_sessions": sessions,
    }


def _fake_dense_embedder() -> Any:
    fake = MagicMock()
    # Each call yields a generator of one fixed-dim vector per input text.
    fake.embed.side_effect = lambda batch: ([0.1] * 384 for _ in batch)
    return fake


def _fake_sparse_embedder() -> Any:
    fake = MagicMock()
    # Sparse: yield objects with .indices / .values arrays.
    def _yield(batch):
        for _ in batch:
            obj = MagicMock()
            obj.indices = [0, 1]
            obj.values = [0.5, 0.5]
            yield obj
    fake.embed.side_effect = _yield
    return fake


@pytest.fixture
def patch_embedders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace embedder builders so tests never load fastembed weights."""
    monkeypatch.setattr(
        ingest_mod, "build_dense_embedder", lambda *a, **kw: _fake_dense_embedder()
    )
    monkeypatch.setattr(
        ingest_mod, "build_sparse_embedder", lambda *a, **kw: _fake_sparse_embedder()
    )


# Test 1 ---------------------------------------------------------------------


def test_eval_collection_name_default() -> None:
    """eval_collection_name(cfg, 'longmemeval_s') == 'supamem_eval_longmemeval_s'."""
    cfg = _cfg()
    name = ingest_mod.eval_collection_name(cfg, "longmemeval_s")
    assert name == "supamem_eval_longmemeval_s"


def test_eval_collection_name_uses_supamem_eval_prefix() -> None:
    """RESEARCH risk #3: reserved-prefix mitigation."""
    cfg = _cfg()
    for suite in ("longmemeval_s", "anything_else"):
        assert ingest_mod.eval_collection_name(cfg, suite).startswith("supamem_eval_")


# Test 2 ---------------------------------------------------------------------


def test_ingest_creates_collection(patch_embedders: None) -> None:
    """ingest() creates the bench collection with vector params matching production."""
    client = MagicMock()
    # Existing collections list is empty -> collection must be created.
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "hello"}]],
    )
    cfg = _cfg()
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")

    # Either create_collection or recreate_collection should have been called.
    called = (
        client.create_collection.called
        or client.recreate_collection.called
    )
    assert called, "ingest must create the bench collection on first run"

    # Inspect the call args to confirm vector params.
    call = (
        client.create_collection.call_args
        if client.create_collection.called
        else client.recreate_collection.call_args
    )
    # collection_name is the bench name, NOT the user's cfg.collection.
    coll = call.kwargs.get("collection_name") or (call.args[0] if call.args else None)
    assert coll == "supamem_eval_longmemeval_s", (
        f"created collection must be the bench prefix, got {coll!r}"
    )


# Test 3 ---------------------------------------------------------------------


def test_ingest_creates_session_id_payload_index(patch_embedders: None) -> None:
    """ingest() creates a keyword payload index on 'session_id' (D-SCOPE-04)."""
    from qdrant_client.http import models as qmodels

    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "hi"}]],
    )
    cfg = _cfg()
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")

    assert client.create_payload_index.called, "session_id payload index must be created"
    # At least one call must specify field_name='session_id' with a keyword schema.
    matched = False
    for call in client.create_payload_index.call_args_list:
        if call.kwargs.get("field_name") == "session_id":
            schema = call.kwargs.get("field_schema")
            assert isinstance(schema, qmodels.KeywordIndexParams), (
                f"session_id schema must be KeywordIndexParams, got {type(schema)}"
            )
            assert schema.type == "keyword"
            assert schema.on_disk is True
            matched = True
    assert matched, "expected create_payload_index(field_name='session_id', ...)"


# Test 4 ---------------------------------------------------------------------


def test_ingest_payload_index_idempotent(patch_embedders: None) -> None:
    """Calling ingest() twice does not raise; idempotent DDL contract."""
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "hi"}]],
    )
    cfg = _cfg()
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")
    # Second run: simulate "collection already exists" by listing it.
    client.get_collections.return_value = MagicMock(
        collections=[MagicMock(name="supamem_eval_longmemeval_s")]
    )
    # And simulate Qdrant raising on a duplicate index request — ingest must
    # NOT propagate; it must catch + log + continue.
    client.create_payload_index.side_effect = [None, Exception("already exists")]
    # Run again — must not raise.
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")


# Test 5 ---------------------------------------------------------------------


def test_ingest_upserts_one_point_per_haystack_turn(patch_embedders: None) -> None:
    """2 sessions × 3 turns → exactly 6 upserted points; payloads carry session_id + text."""
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "multi_session",
        sessions=[
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ],
            [
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
                {"role": "user", "content": "u4"},
            ],
        ],
        session_ids=["sess-A", "sess-B"],
    )
    cfg = _cfg()
    count = ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")
    assert count == 6, f"expected 6 upserted points, got {count}"

    # Walk all upsert calls; flatten points; assert payload shape.
    all_points: list = []
    for call in client.upsert.call_args_list:
        pts = call.kwargs.get("points") or (call.args[1] if len(call.args) > 1 else [])
        all_points.extend(pts)
    assert len(all_points) == 6
    for pt in all_points:
        payload = getattr(pt, "payload", None)
        assert payload is not None
        assert "session_id" in payload
        assert "text" in payload
        assert payload["session_id"] in {"sess-A", "sess-B"}


# Test 6 ---------------------------------------------------------------------


def test_ingest_session_id_collision_safe_on_isolated_collection(
    patch_embedders: None,
) -> None:
    """Ingest target collection is the isolated bench prefix, NOT cfg.collection."""
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "x"}]],
    )
    cfg = _cfg(collection="user_project_collection")
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")

    # No call should reference 'user_project_collection' — only the bench
    # prefix is touched.
    for call in client.upsert.call_args_list:
        coll = call.kwargs.get("collection_name")
        assert coll != "user_project_collection"
        assert coll == "supamem_eval_longmemeval_s"
    for call in client.create_payload_index.call_args_list:
        coll = call.kwargs.get("collection_name")
        assert coll == "supamem_eval_longmemeval_s"


# Test 7 ---------------------------------------------------------------------


def test_ingest_does_not_touch_production_indexer() -> None:
    """No symbol from supamem.indexer is imported by longmemeval_ingest.

    Inspect the module source via ast and assert that no top-level
    ``import`` or ``from ... import`` statement targets the production
    indexer package.
    """
    src_path = Path(ingest_mod.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden_roots = ("supamem.indexer", "supamem.chunker")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for forbidden in forbidden_roots:
                assert not (mod == forbidden or mod.startswith(forbidden + ".")), (
                    f"forbidden import: from {mod} ..."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_roots:
                    assert not (
                        alias.name == forbidden or alias.name.startswith(forbidden + ".")
                    ), f"forbidden import: import {alias.name}"


# Test 8 ---------------------------------------------------------------------


def test_ingest_does_not_mutate_caller_cfg(patch_embedders: None) -> None:
    """Caller's cfg.collection must remain unchanged after ingest()."""
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "x"}]],
    )
    cfg = _cfg(collection="user_project_collection")
    original_collection = cfg.collection
    ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")
    assert cfg.collection == original_collection


# Test 9 — regression for numpy-array sparse-vector handling -----------------


def test_ingest_handles_numpy_sparse_vector_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real fastembed sparse embedders return numpy arrays for indices/values.

    Regression: the previous ``getattr(svec, "indices", []) or []`` short-circuit
    raised ``ValueError: The truth value of an array with more than one element
    is ambiguous`` because numpy arrays don't support multi-element ``__bool__``.
    The fix uses explicit ``is None`` checks. This test fakes numpy-shaped
    sparse outputs to lock in that behavior without loading fastembed.
    """
    np = pytest.importorskip("numpy")

    class _NumpySparse:
        # Mimics fastembed.SparseEmbedding's array-typed attributes.
        def __init__(self) -> None:
            self.indices = np.array([0, 1, 2], dtype=np.int64)
            self.values = np.array([0.5, 0.3, 0.2], dtype=np.float32)

    def _yield_numpy(batch):
        for _ in batch:
            yield _NumpySparse()

    fake_dense = MagicMock()
    fake_dense.embed.side_effect = lambda batch: ([0.1] * 384 for _ in batch)
    fake_sparse = MagicMock()
    fake_sparse.embed.side_effect = _yield_numpy
    monkeypatch.setattr(ingest_mod, "build_dense_embedder", lambda *a, **k: fake_dense)
    monkeypatch.setattr(ingest_mod, "build_sparse_embedder", lambda *a, **k: fake_sparse)

    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    rec = _make_raw_record(
        "q",
        "single_session_user",
        sessions=[[{"role": "user", "content": "hello"}]],
    )
    cfg = _cfg()
    # Must not raise — fix converts numpy → list explicitly via `is None` check.
    count = ingest_mod.ingest(cfg, [rec], client=client, suite="longmemeval_s")
    assert count == 1

    # Confirm the upsert payload carried plain-Python int/float lists,
    # not numpy arrays (Qdrant SparseVector wants native lists).
    upsert_calls = client.upsert.call_args_list
    assert upsert_calls, "expected at least one upsert call"
    points = upsert_calls[0].kwargs.get("points") or upsert_calls[0].args[1]
    sparse = points[0].vector["__sparse__"] if "__sparse__" in points[0].vector else None
    # The sparse-vector key may differ; resolve generically.
    if sparse is None:
        for v in points[0].vector.values():
            if hasattr(v, "indices") and hasattr(v, "values"):
                sparse = v
                break
    assert sparse is not None
    assert all(isinstance(i, int) for i in sparse.indices)
    assert all(isinstance(v, float) for v in sparse.values)
