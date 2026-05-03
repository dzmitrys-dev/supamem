"""Tests for Phase 11 path-prefix indexer enablement (FILT-01, D-PFX-02..06).

Covers:
- ``_path_prefixes(file_path)`` helper edge cases (D-PFX-02 + RESEARCH §2 Pitfall 1).
- ``Manifest.path_prefixes_migration`` reserved-key round-trip (mirrors Phase 9
  ``__validity_migration__`` byte-stable rollback convention).
- ``_index_records`` payload write of ``path_prefixes`` (D-PFX-02).
- ``_ensure_payload_indexes`` creates the keyword on-disk index (D-PFX-04) and
  preserves the Phase 9 ``valid_to`` + ``chunker`` indexes.
- ``_eager_path_prefixes_migration`` sweep semantics (D-PFX-06): per-point
  skip-guard, missing-file_path skip, recompute from ``payload['file_path']``.
- ``run_index`` gate flip semantics: stamp on success, leave None on failure.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import _cfg_with_temporal


def _fake_point(pid: int, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(id=pid, payload=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Task B1 — _path_prefixes helper edge cases (D-PFX-02 + RESEARCH Pitfall 1)
# ─────────────────────────────────────────────────────────────────────────────


def test_path_prefixes_basic():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("src/supamem/retrieval/filters.py") == [
        "src",
        "src/supamem",
        "src/supamem/retrieval",
        "src/supamem/retrieval/filters.py",
    ]


def test_path_prefixes_strip_dot_slash():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("./src/a.py") == ["src", "src/a.py"]


def test_path_prefixes_strip_leading_slash():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("/abs/x.py") == ["abs", "abs/x.py"]


def test_path_prefixes_root_file():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("README.md") == ["README.md"]


def test_path_prefixes_empty_returns_empty_list():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("") == []


def test_path_prefixes_preserves_case():
    from supamem.indexer import _path_prefixes

    assert _path_prefixes("Src/SupaMem/X.py") == [
        "Src",
        "Src/SupaMem",
        "Src/SupaMem/X.py",
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Task B1 — Manifest.path_prefixes_migration reserved-key (D-NULL-03 mirror)
# ─────────────────────────────────────────────────────────────────────────────


def test_manifest_path_prefixes_default_none():
    from supamem.indexer.manifest import Manifest

    m = Manifest()
    assert hasattr(m, "path_prefixes_migration")
    assert m.path_prefixes_migration is None


def test_manifest_path_prefixes_field_round_trips(tmp_path):
    from supamem.indexer.manifest import Manifest

    path = tmp_path / "manifest.json"
    m = Manifest()
    m.path_prefixes_migration = "0.3.1"
    m.save(path)

    raw = json.loads(path.read_text())
    assert "__path_prefixes_migration__" in raw
    assert raw["__path_prefixes_migration__"] == "0.3.1"

    loaded = Manifest.load(path)
    assert loaded.path_prefixes_migration == "0.3.1"


def test_manifest_path_prefixes_omitted_when_none(tmp_path):
    """Byte-stable rollback: key NOT emitted when field is None."""
    from supamem.indexer.manifest import Manifest

    path = tmp_path / "manifest.json"
    m = Manifest()
    assert m.path_prefixes_migration is None
    m.save(path)

    raw = json.loads(path.read_text())
    assert "__path_prefixes_migration__" not in raw


# ─────────────────────────────────────────────────────────────────────────────
# Task B2 — _index_records writes path_prefixes payload (D-PFX-02)
# ─────────────────────────────────────────────────────────────────────────────


def test_index_records_writes_path_prefixes_payload(tmp_path):
    """Each upserted point's payload contains path_prefixes derived from abs_path."""
    from supamem.indexer import _index_records
    from supamem.indexer.transcript import ChunkRecord

    src = tmp_path / "doc.md"
    src.write_text("hello world\n" * 50)  # ensure tokens > CHUNK_MIN_TOKENS

    client = MagicMock()
    dense = MagicMock()
    dense.embed = MagicMock(return_value=iter([[0.1] * 384]))
    sparse_obj = SimpleNamespace(indices=[1], values=[0.5])
    sparse = MagicMock()
    sparse.embed = MagicMock(return_value=iter([sparse_obj]))

    rec = ChunkRecord(text="hello world " * 100, metadata={"chunker": "markdown_header"})
    n = _index_records(
        client=client,
        dense=dense,
        sparse=sparse,
        path=src,
        records=[rec],
        sha="deadbeef",
        collection="test",
        is_transcript=False,
        classifier_rooms={},
    )
    assert n == 1
    assert client.upsert.call_count == 1
    points = client.upsert.call_args.kwargs.get("points") or client.upsert.call_args.args[1]
    payload = points[0].payload
    assert "path_prefixes" in payload
    abs_path = str(src.resolve())
    # path_prefixes preserves the exact abs_path final segment.
    assert payload["path_prefixes"][-1] == abs_path.lstrip("/")
    # Every prefix is a prefix of the final element.
    for p in payload["path_prefixes"][:-1]:
        assert payload["path_prefixes"][-1].startswith(p + "/")


# ─────────────────────────────────────────────────────────────────────────────
# Task B2 — _ensure_payload_indexes (renamed) + path_prefixes index (D-PFX-04)
# ─────────────────────────────────────────────────────────────────────────────


def test_ensure_payload_indexes_creates_path_prefixes_index():
    from supamem.indexer import _ensure_payload_indexes

    client = MagicMock()
    cfg = _cfg_with_temporal()
    _ensure_payload_indexes(client, cfg)

    calls = client.create_payload_index.call_args_list
    matched = False
    for c in calls:
        if c.kwargs.get("field_name") == "path_prefixes":
            schema = c.kwargs.get("field_schema")
            # Verbose KeywordIndexParams form per D-PFX-04
            assert getattr(schema, "type", None) == "keyword"
            assert getattr(schema, "on_disk", None) is True
            matched = True
    assert matched, "must create_payload_index for 'path_prefixes' (KeywordIndexParams on_disk)"


def test_ensure_payload_indexes_keeps_existing_indexes():
    """Renaming must NOT regress Phase 9: valid_to + chunker indexes still created."""
    from qdrant_client.http import models as qmodels

    from supamem.indexer import _ensure_payload_indexes

    client = MagicMock()
    cfg = _cfg_with_temporal()
    _ensure_payload_indexes(client, cfg)
    calls = client.create_payload_index.call_args_list
    fields = [c.kwargs.get("field_name") for c in calls]
    assert "valid_to" in fields
    assert "chunker" in fields
    # And the schemas are still the Phase 9 enum form for those two.
    for c in calls:
        if c.kwargs.get("field_name") == "valid_to":
            assert c.kwargs.get("field_schema") == qmodels.PayloadSchemaType.DATETIME
        if c.kwargs.get("field_name") == "chunker":
            assert c.kwargs.get("field_schema") == qmodels.PayloadSchemaType.KEYWORD


# ─────────────────────────────────────────────────────────────────────────────
# Task B2 — _eager_path_prefixes_migration sweep (D-PFX-06)
# ─────────────────────────────────────────────────────────────────────────────


def test_eager_path_prefixes_migration_recomputes_from_file_path():
    from supamem.indexer import _eager_path_prefixes_migration

    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, {"file_path": "src/a.py"})],
        None,
    )
    cfg = _cfg_with_temporal()
    n = _eager_path_prefixes_migration(client, cfg)
    assert n == 1
    assert client.set_payload.call_count == 1
    call = client.set_payload.call_args
    assert call.kwargs.get("payload") == {"path_prefixes": ["src", "src/a.py"]}
    assert call.kwargs.get("wait") is True
    assert call.kwargs.get("points") == [1]


def test_eager_path_prefixes_migration_skips_already_migrated():
    """Per-point skip-guard: payload already has path_prefixes → continue (idempotent retry)."""
    from supamem.indexer import _eager_path_prefixes_migration

    client = MagicMock()
    client.scroll.return_value = (
        [
            _fake_point(
                1,
                {"file_path": "src/a.py", "path_prefixes": ["src", "src/a.py"]},
            ),
            _fake_point(2, {"file_path": "src/b.py"}),
        ],
        None,
    )
    cfg = _cfg_with_temporal()
    n = _eager_path_prefixes_migration(client, cfg)
    assert n == 1  # only point 2 migrated
    # Only one set_payload call, and it targets only id=2
    assert client.set_payload.call_count == 1
    call = client.set_payload.call_args
    assert call.kwargs.get("points") == [2]


def test_eager_path_prefixes_migration_skips_missing_file_path():
    """Pitfall 6: legacy points without payload['file_path'] are skipped."""
    from supamem.indexer import _eager_path_prefixes_migration

    client = MagicMock()
    client.scroll.return_value = (
        [_fake_point(1, {})],  # no file_path key
        None,
    )
    cfg = _cfg_with_temporal()
    n = _eager_path_prefixes_migration(client, cfg)
    assert n == 0
    assert client.set_payload.call_count == 0


def test_eager_path_prefixes_migration_groups_by_prefixes():
    """Multiple points with the SAME path_prefixes coalesce into ONE set_payload."""
    from supamem.indexer import _eager_path_prefixes_migration

    client = MagicMock()
    client.scroll.return_value = (
        [
            _fake_point(1, {"file_path": "src/a.py"}),
            _fake_point(2, {"file_path": "src/a.py"}),
            _fake_point(3, {"file_path": "src/b.py"}),
        ],
        None,
    )
    cfg = _cfg_with_temporal()
    n = _eager_path_prefixes_migration(client, cfg)
    assert n == 3
    # 2 distinct prefix tuples -> 2 set_payload calls
    assert client.set_payload.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Task B2 — run_index gate flip semantics (Pitfall 4 — never stamp on partial)
# ─────────────────────────────────────────────────────────────────────────────


def test_run_index_skips_sweep_when_gate_stamped(tmp_path, monkeypatch):
    """Manifest.path_prefixes_migration set → sweep NOT called on subsequent run."""
    from supamem.indexer.manifest import Manifest

    manifest_file = tmp_path / "manifest.json"
    m = Manifest()
    m.path_prefixes_migration = "0.3.1"
    m.validity_migration = "0.3.1"  # also stamped → no validity sweep
    m.save(manifest_file)

    sweep_calls: list = []

    def _fake_sweep(client, cfg, **kw):
        sweep_calls.append((client, cfg))
        return 0

    (tmp_path / "doc.md").write_text("# heading\n\nbody\n")

    # Patch out heavy bits so run_index can boot.
    fake_qdrant = MagicMock()
    fake_qdrant.get_collections.return_value = MagicMock()
    fake_qdrant.scroll.return_value = ([], None)

    with patch("supamem.indexer.QdrantClient", return_value=fake_qdrant), \
         patch("supamem.indexer.build_dense_embedder", return_value=MagicMock()), \
         patch("supamem.indexer.build_sparse_embedder", return_value=MagicMock()), \
         patch(
             "supamem.indexer._eager_path_prefixes_migration",
             side_effect=_fake_sweep,
         ), \
         patch("supamem.indexer._manifest_path", return_value=manifest_file):
        from supamem.indexer import run_index

        cfg = _cfg_with_temporal()
        run_index(target="tuned", sources=[str(tmp_path)], config=cfg)

    assert len(sweep_calls) == 0


def test_run_index_runs_sweep_and_stamps_when_gate_none(tmp_path):
    """Manifest.path_prefixes_migration None → sweep called once; manifest stamped."""
    from supamem.indexer.manifest import Manifest

    manifest_file = tmp_path / "manifest.json"
    # validity already migrated to isolate path_prefixes gate behavior
    m = Manifest()
    m.validity_migration = "0.3.1"
    m.save(manifest_file)

    # ``run_index`` short-circuits when no .md/.jsonl sources are found —
    # plant a minimal markdown file so the boot block (including our gate)
    # is exercised.
    (tmp_path / "doc.md").write_text("# heading\n\nbody\n")

    fake_qdrant = MagicMock()
    fake_qdrant.get_collections.return_value = MagicMock()
    fake_qdrant.scroll.return_value = ([], None)

    sweep_returns = [42]

    with patch("supamem.indexer.QdrantClient", return_value=fake_qdrant), \
         patch("supamem.indexer.build_dense_embedder", return_value=MagicMock()), \
         patch("supamem.indexer.build_sparse_embedder", return_value=MagicMock()), \
         patch(
             "supamem.indexer._eager_path_prefixes_migration",
             return_value=sweep_returns[0],
         ) as sweep_mock, \
         patch("supamem.indexer._manifest_path", return_value=manifest_file):
        from supamem.indexer import run_index

        cfg = _cfg_with_temporal()
        run_index(target="tuned", sources=[str(tmp_path)], config=cfg)
        assert sweep_mock.call_count == 1

    loaded = Manifest.load(manifest_file)
    assert loaded.path_prefixes_migration is not None


def test_run_index_does_not_stamp_on_failure(tmp_path):
    """Sweep raises → manifest.path_prefixes_migration remains None (Pitfall 4)."""
    from supamem.indexer.manifest import Manifest

    manifest_file = tmp_path / "manifest.json"
    m = Manifest()
    m.validity_migration = "0.3.1"
    m.save(manifest_file)

    (tmp_path / "doc.md").write_text("# heading\n\nbody\n")

    fake_qdrant = MagicMock()
    fake_qdrant.get_collections.return_value = MagicMock()
    fake_qdrant.scroll.return_value = ([], None)

    with patch("supamem.indexer.QdrantClient", return_value=fake_qdrant), \
         patch("supamem.indexer.build_dense_embedder", return_value=MagicMock()), \
         patch("supamem.indexer.build_sparse_embedder", return_value=MagicMock()), \
         patch(
             "supamem.indexer._eager_path_prefixes_migration",
             side_effect=RuntimeError("boom"),
         ), \
         patch("supamem.indexer._manifest_path", return_value=manifest_file):
        from supamem.indexer import run_index

        cfg = _cfg_with_temporal()
        run_index(target="tuned", sources=[str(tmp_path)], config=cfg)

    loaded = Manifest.load(manifest_file)
    assert loaded.path_prefixes_migration is None


@pytest.mark.parametrize("input_, expected", [
    ("./src/a.py", ["src", "src/a.py"]),
    ("/abs/x.py", ["abs", "abs/x.py"]),
])
def test_path_prefixes_lstrip_does_not_overstrip(input_, expected):
    """RESEARCH Pitfall 1: ``lstrip("./")`` is character-class-based; verify
    it does not eat legitimate leading characters in our supported inputs."""
    from supamem.indexer import _path_prefixes

    assert _path_prefixes(input_) == expected
