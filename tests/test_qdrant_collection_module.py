"""Unit tests for ``supamem.qdrant_collection`` shared lifecycle module (Plan 18-A)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from supamem.config import ResolvedConfig
from supamem.qdrant_collection import (
    DEFAULT_VECTOR_SIZE,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    CollectionMissingError,
    assert_collection_exists_for_read,
    ensure_collection,
    hybrid_vectors_config,
    validate_writable_collection,
)


def _cfg(**overrides: Any) -> ResolvedConfig:
    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "test_hybrid_collection"),
        **overrides,
    )


def test_ensure_collection_creates_when_missing() -> None:
    """Empty get_collections → create_collection once with hybrid config; returns True."""
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])

    created = ensure_collection(client, "bench_coll")

    assert created is True
    client.create_collection.assert_called_once()
    call = client.create_collection.call_args
    assert call.kwargs["collection_name"] == "bench_coll"
    assert DENSE_VECTOR_NAME in call.kwargs["vectors_config"]
    assert SPARSE_VECTOR_NAME in call.kwargs["sparse_vectors_config"]


def test_ensure_collection_idempotent() -> None:
    """Collection already present → create_collection NOT called; returns False."""
    client = MagicMock()
    existing = MagicMock()
    existing.name = "bench_coll"
    client.get_collections.return_value = MagicMock(collections=[existing])

    created = ensure_collection(client, "bench_coll")

    assert created is False
    client.create_collection.assert_not_called()


def test_ensure_collection_surfaces_get_collections_failure() -> None:
    """get_collections raises → warn via err_console, still attempts create."""
    client = MagicMock()
    client.get_collections.side_effect = ConnectionError("qdrant down")

    with patch("supamem.qdrant_collection.err_console.print") as mock_print:
        created = ensure_collection(client, "new_coll")

    assert created is True
    mock_print.assert_called_once()
    assert "get_collections failed" in mock_print.call_args[0][0]
    client.create_collection.assert_called_once()


def test_validate_writable_forbidden_raises() -> None:
    """dev_memory without allow_legacy_collection → RuntimeError matching forbidden."""
    cfg = _cfg(collection="dev_memory")
    with pytest.raises(RuntimeError, match="forbidden"):
        validate_writable_collection(cfg)


def test_validate_writable_legacy_opt_in() -> None:
    """allow_legacy_collection=True → no raise for dev_memory."""
    cfg = _cfg(collection="dev_memory")
    cfg.allow_legacy_collection = True
    validate_writable_collection(cfg)


def test_assert_collection_exists_for_read_raises_actionable() -> None:
    """get_collection 404 → CollectionMissingError with remediation text."""
    client = MagicMock()
    exc = UnexpectedResponse(
        status_code=404,
        reason_phrase="Not Found",
        content=b"",
        headers={},
    )
    client.get_collection.side_effect = exc

    with pytest.raises(CollectionMissingError, match="supamem index|supamem init"):
        assert_collection_exists_for_read(client, "missing_coll")


def test_assert_collection_exists_for_read_passes() -> None:
    """get_collection succeeds → no raise."""
    client = MagicMock()
    assert_collection_exists_for_read(client, "present_coll")
    client.get_collection.assert_called_once_with("present_coll")


def test_hybrid_vectors_config_matches_init_schema() -> None:
    """Dense 384 COSINE + sparse IDF matches init.create_collection schema."""
    vectors_config, sparse_vectors_config = hybrid_vectors_config()

    dense = vectors_config[DENSE_VECTOR_NAME]
    assert dense.size == DEFAULT_VECTOR_SIZE == 384
    assert dense.distance == qmodels.Distance.COSINE

    sparse = sparse_vectors_config[SPARSE_VECTOR_NAME]
    assert sparse.modifier == qmodels.Modifier.IDF
