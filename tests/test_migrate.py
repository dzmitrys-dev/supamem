"""Tests for ``supamem.migrate.run_migrate`` (Plan 80.6-09)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.migrate import (
    MigrationPath,
    diff_schema,
    run_migrate,
    snapshot_collection,
)


def _fake_collection_info(*, dim: int = 384, has_sparse: bool = True) -> Any:
    """Build a MagicMock matching qdrant_client's CollectionInfo shape enough for diff_schema."""
    info = MagicMock()
    info.config.params.vectors = {"dense": MagicMock(size=dim)}
    info.config.params.sparse_vectors = {"sparse": MagicMock()} if has_sparse else None
    return info


def _fake_client(
    *, source_dim: int = 384, source_has_sparse: bool = True, target_exists: bool = False
) -> MagicMock:
    client = MagicMock()
    client.get_collection.return_value = _fake_collection_info(
        dim=source_dim, has_sparse=source_has_sparse
    )
    cols: list[Any] = [MagicMock(name=f"name={n}") for n in (["src"] + (["tgt"] if target_exists else []))]
    for c, n in zip(cols, ["src"] + (["tgt"] if target_exists else [])):
        c.name = n
    client.get_collections.return_value = MagicMock(collections=cols)
    return client


# ── diff_schema ───────────────────────────────────────────────────────────


def test_diff_schema_detects_dim_mismatch() -> None:
    client = _fake_client(source_dim=768)
    out = diff_schema(client, "src", target_dense_dim=384, target_has_sparse=True)
    assert out["compatible"] is False
    assert out["vector_dim"] == (768, 384)


def test_diff_schema_detects_missing_sparse() -> None:
    client = _fake_client(source_has_sparse=False)
    out = diff_schema(client, "src", target_dense_dim=384, target_has_sparse=True)
    assert out["compatible"] is False
    assert out["has_sparse"] == (False, True)


def test_diff_schema_compatible_when_matched() -> None:
    client = _fake_client()
    out = diff_schema(client, "src", target_dense_dim=384, target_has_sparse=True)
    assert out["compatible"] is True


# ── run_migrate ───────────────────────────────────────────────────────────


def test_run_migrate_coexist_creates_new_collection() -> None:
    client = _fake_client()
    rc = run_migrate(client, "src", "supamem-tgt", path="coexist")
    assert rc == 0
    client.create_collection.assert_called_once()
    args, kwargs = client.create_collection.call_args
    assert kwargs.get("collection_name") == "supamem-tgt"
    client.delete_collection.assert_not_called()


def test_run_migrate_migrate_requires_yes() -> None:
    client = _fake_client()
    with pytest.raises(RuntimeError, match="--yes"):
        run_migrate(client, "src", "supamem-tgt", path="migrate", yes=False)


def test_run_migrate_migrate_snapshots_first() -> None:
    """The 'migrate' path must call create_snapshot before any delete."""
    client = _fake_client()

    call_order: list[str] = []
    client.create_snapshot.side_effect = lambda **_: call_order.append("snapshot") or MagicMock(name="snap-id")
    client.delete_collection.side_effect = lambda **_: call_order.append("delete")
    client.create_collection.side_effect = lambda **_: call_order.append("create")
    client.scroll.return_value = ([], None)  # no points to migrate

    run_migrate(client, "src", "supamem-tgt", path="migrate", yes=True)
    if "delete" in call_order:
        assert call_order.index("snapshot") < call_order.index("delete")
    else:
        assert "snapshot" in call_order


def test_run_migrate_adopt_as_is_no_writes() -> None:
    client = _fake_client()
    rc = run_migrate(client, "src", "supamem-tgt", path="adopt-as-is")
    assert rc == 0
    client.create_collection.assert_not_called()
    client.upsert.assert_not_called()
    client.delete_collection.assert_not_called()


def test_run_migrate_idempotent_target_exists() -> None:
    """Pre-existing target with matching schema → no destructive ops."""
    client = _fake_client(target_exists=True)
    # Make get_collection return the same compatible schema for both queries.
    client.get_collection.return_value = _fake_collection_info()

    rc = run_migrate(client, "src", "supamem-tgt", path="coexist")
    assert rc == 0
    client.create_collection.assert_not_called()


def test_run_migrate_refuses_to_overwrite_unowned() -> None:
    """A collection NOT prefixed with 'supamem-' is unowned — extra guard for migrate path."""
    client = _fake_client()
    with pytest.raises(RuntimeError, match=r"unowned|supamem-"):
        run_migrate(client, "src", "legacy-target", path="migrate", yes=True)


def test_snapshot_collection_invokes_qdrant_api() -> None:
    """snapshot_collection must call client.create_snapshot."""
    client = MagicMock()
    snap = MagicMock()
    snap.name = "snap-id-123"
    client.create_snapshot.return_value = snap
    out = snapshot_collection(client, "src")
    assert out == "snap-id-123"
    client.create_snapshot.assert_called_once()


def test_migration_path_literal_values() -> None:
    """Document the three valid paths."""
    valid: set[MigrationPath] = {"coexist", "migrate", "adopt-as-is"}
    assert "coexist" in valid
    assert "migrate" in valid
    assert "adopt-as-is" in valid
