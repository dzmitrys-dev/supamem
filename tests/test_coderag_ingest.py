"""Phase 15 Plan A Task A2 — coderag ingest collection-name + idempotent
payload-index DDL tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
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


def test_ingest_raises_not_implemented_in_plan_a() -> None:
    with pytest.raises(NotImplementedError, match="Plan 15-B"):
        ingest(object(), [])
