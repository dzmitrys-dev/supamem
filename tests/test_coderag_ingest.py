"""Phase 15 Plan A Task A2 + Plan B Task B3 — coderag ingest tests.

Plan A scope: collection name + idempotent payload-index DDL.
Plan B scope: ingest body wires corpus walk → chunker → embedder → upsert
with payload `{repo, axis, document, file_path, doc_id}`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from qdrant_client.http import models as qmodels

from supamem.eval.coderag import ingest as ingest_mod
from supamem.eval.coderag.ingest import (
    coderag_collection_name,
    ensure_indexes,
    ingest,
)


def test_coderag_collection_name_is_supamem_eval_coderag() -> None:
    assert coderag_collection_name() == "supamem_eval_coderag"


def test_ensure_indexes_creates_both_repo_and_axis() -> None:
    client = MagicMock()
    ensure_indexes(client)

    assert client.create_payload_index.call_count == 2
    call_kwargs = [c.kwargs for c in client.create_payload_index.call_args_list]
    fields = sorted(kw["field_name"] for kw in call_kwargs)
    assert fields == ["axis", "repo"]
    for kw in call_kwargs:
        assert kw["collection_name"] == "supamem_eval_coderag"
        schema = kw["field_schema"]
        assert isinstance(schema, qmodels.KeywordIndexParams)
        assert schema.type == "keyword"
        assert schema.on_disk is True


def test_ensure_indexes_idempotent_on_existing_index(monkeypatch) -> None:
    client = MagicMock()
    calls: list[tuple] = []

    def _create(*args, **kwargs):
        calls.append(("call", kwargs.get("field_name")))
        if len(calls) >= 2:
            raise RuntimeError("already exists")

    client.create_payload_index.side_effect = _create

    warned: list[str] = []
    monkeypatch.setattr(
        ingest_mod.err_console,
        "print",
        lambda msg, *a, **kw: warned.append(str(msg)),
    )

    # Should NOT raise
    ensure_indexes(client)
    assert any("index create skipped" in m for m in warned), warned


# ---- Plan 15-B Task B3 — ingest body wiring tests --------------------------


class _FakeDense:
    def embed(self, texts):
        for _ in texts:
            yield [0.1] * 384


class _FakeSparse:
    def embed(self, texts):
        for _ in texts:
            yield SimpleNamespace(indices=[1, 2], values=[0.5, 0.7])


def _patch_embedders(monkeypatch):
    monkeypatch.setattr(ingest_mod, "build_dense_embedder", lambda *a, **kw: _FakeDense())
    monkeypatch.setattr(ingest_mod, "build_sparse_embedder", lambda *a, **kw: _FakeSparse())


def test_ingest_body_walks_corpus_and_upserts(tiny_repo, monkeypatch) -> None:
    """ingest() walks the corpus, embeds, and upserts to the isolated bench
    collection with payload carrying repo + axis + doc_id keys."""
    _patch_embedders(monkeypatch)

    client = MagicMock()
    cfg = SimpleNamespace(qdrant_url="http://localhost:6333", qdrant_api_key=None)

    n = ingest(cfg, [{"repo_slug": "tiny", "repo_root": tiny_repo}], client=client)
    assert n > 0
    assert client.upsert.called

    seen_payloads = []
    for call in client.upsert.call_args_list:
        for pt in call.kwargs["points"]:
            seen_payloads.append(pt.payload)
    assert seen_payloads
    for p in seen_payloads:
        assert p["repo"] == "tiny"
        assert p["axis"] in {"code_fact", "decision_rationale"}
        assert "doc_id" in p
        assert "document" in p
        assert "file_path" in p


def test_ingest_body_uses_isolated_collection(tiny_repo, monkeypatch) -> None:
    _patch_embedders(monkeypatch)
    client = MagicMock()
    cfg = SimpleNamespace(qdrant_url="http://localhost:6333", qdrant_api_key=None)
    ingest(cfg, [{"repo_slug": "tiny", "repo_root": tiny_repo}], client=client)
    for call in client.upsert.call_args_list:
        assert call.kwargs["collection_name"] == "supamem_eval_coderag"


def test_ingest_payload_doc_id_is_relative_path(tiny_repo, monkeypatch) -> None:
    _patch_embedders(monkeypatch)
    client = MagicMock()
    cfg = SimpleNamespace(qdrant_url="http://localhost:6333", qdrant_api_key=None)
    ingest(cfg, [{"repo_slug": "tiny", "repo_root": tiny_repo}], client=client)
    doc_ids = set()
    for call in client.upsert.call_args_list:
        for pt in call.kwargs["points"]:
            doc_ids.add(pt.payload["doc_id"])
    assert "src/foo.py" in doc_ids
    assert "src/bar.py" in doc_ids
    for did in doc_ids:
        assert not did.startswith("/"), did


def test_ingest_body_does_not_import_supamem_indexer_except_chunker() -> None:
    """D-SCOPE-05 carry-lock: only-allowed indexer import is chunk_markdown."""
    import ast
    from pathlib import Path

    src_path = Path(ingest_mod.__file__)
    tree = ast.parse(src_path.read_text())
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("supamem.indexer"):
                if mod == "supamem.indexer.chunker" and any(
                    a.name == "chunk_markdown" for a in node.names
                ):
                    continue
                bad.append(f"from {mod} import {[a.name for a in node.names]}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("supamem.indexer"):
                    bad.append(f"import {alias.name}")
    assert not bad, f"forbidden indexer imports: {bad}"
