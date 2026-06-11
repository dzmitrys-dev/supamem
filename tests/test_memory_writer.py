"""Tests for supamem.memory_writer (v0.1.3+).

Covers slug generation, atomic write, idempotent path resolution, validation
errors, and partial-failure handling when Qdrant indexing fails.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from supamem.config import ResolvedConfig
from supamem.memory_writer import (
    AGENT_WRITE_DIRNAME,
    MAX_CONTENT_LEN,
    MAX_TOPIC_LEN,
    NAMESPACE_AGENT_WRITE,
    WriteResult,
    _index_single_doc,
    _point_id_for_slug,
    _resolve_write_root,
    _safe_target_path,
    _slugify,
    write_memory,
)


# ── slug & path resolution ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("Hello World", "hello-world"),
        ("Some/Weird Topic-1!", "some-weird-topic-1"),
        ("   ", "untitled"),
        ("", "untitled"),
        ("Multi   spaces", "multi-spaces"),
        ("a" * 200, "a" * 64),  # truncated to SLUG_MAX_LEN
    ],
)
def test_slugify(topic: str, expected: str) -> None:
    assert _slugify(topic) == expected


def test_point_id_is_deterministic() -> None:
    assert _point_id_for_slug("foo") == _point_id_for_slug("foo")
    assert _point_id_for_slug("foo") != _point_id_for_slug("bar")


def test_resolve_write_root_uses_first_dir_source(tmp_path: Path) -> None:
    cfg = ResolvedConfig(sources=[".claude/insights/", "docs/learned-facts.md"])
    (tmp_path / ".claude" / "insights").mkdir(parents=True)
    root = _resolve_write_root(cfg, tmp_path)
    assert root.name == AGENT_WRITE_DIRNAME
    assert root.parent.name == "insights"


def test_resolve_write_root_falls_back_when_no_dir_sources(tmp_path: Path) -> None:
    cfg = ResolvedConfig(sources=["docs/X.md"])  # only file sources
    root = _resolve_write_root(cfg, tmp_path)
    assert root.parent.name == "insights"


def test_safe_target_path_rejects_traversal(tmp_path: Path) -> None:
    write_root = tmp_path / "outside"
    with pytest.raises(ValueError, match="escapes project root"):
        _safe_target_path(write_root, "x", tmp_path / "project")


# ── write_memory happy path ─────────────────────────────────────────────────


def _patch_indexer_to_succeed(points_added: int = 1) -> "patch._patch":
    return patch(
        "supamem.memory_writer._index_single_doc", return_value=points_added
    )


def test_write_memory_creates_file_with_frontmatter(tmp_path: Path) -> None:
    cfg = ResolvedConfig(sources=[".claude/insights/"])
    (tmp_path / ".claude" / "insights").mkdir(parents=True)

    with _patch_indexer_to_succeed():
        res = write_memory(
            topic="Auth flow decisions",
            content="Use Auth0; refresh tokens via cookie.",
            description="Why we picked Auth0",
            tags=["auth", "security"],
            config=cfg,
            project_root=tmp_path,
        )

    target = tmp_path / ".claude" / "insights" / AGENT_WRITE_DIRNAME / "auth-flow-decisions.md"
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm_block, body = text.split("---\n\n", maxsplit=1)
    fm = yaml.safe_load(fm_block.strip("- \n"))
    assert fm["topic"] == "auth-flow-decisions"
    assert fm["name"] == "Auth flow decisions"
    assert fm["type"] == "agent-write"
    assert fm["description"] == "Why we picked Auth0"
    assert fm["tags"] == ["auth", "security"]
    assert "Use Auth0" in body
    assert res.indexed is True
    assert res.points_added == 1
    assert res.error is None
    assert "auth-flow-decisions" in res.summary


def test_write_memory_idempotent_on_topic(tmp_path: Path) -> None:
    cfg = ResolvedConfig(sources=[".claude/insights/"])
    (tmp_path / ".claude" / "insights").mkdir(parents=True)

    with _patch_indexer_to_succeed():
        r1 = write_memory(topic="t1", content="v1", config=cfg, project_root=tmp_path)
        r2 = write_memory(topic="t1", content="v2", config=cfg, project_root=tmp_path)

    assert r1.path == r2.path  # same slug → same path
    assert Path(r2.path).read_text(encoding="utf-8").endswith("v2\n")


def test_write_memory_atomic_write(tmp_path: Path) -> None:
    """The .tmp file should NOT remain on disk after a successful write."""
    cfg = ResolvedConfig(sources=[".claude/insights/"])
    (tmp_path / ".claude" / "insights").mkdir(parents=True)

    with _patch_indexer_to_succeed():
        res = write_memory(topic="hi", content="x", config=cfg, project_root=tmp_path)
    target = Path(res.path)
    leftover = target.with_suffix(target.suffix + ".tmp")
    assert target.exists()
    assert not leftover.exists()


# ── validation ──────────────────────────────────────────────────────────────


def test_write_memory_rejects_empty_topic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic required"):
        write_memory(topic="   ", content="x", project_root=tmp_path)


def test_write_memory_rejects_oversize_topic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic too long"):
        write_memory(topic="x" * (MAX_TOPIC_LEN + 1), content="x", project_root=tmp_path)


def test_write_memory_rejects_oversize_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="content too long"):
        write_memory(
            topic="t",
            content="x" * (MAX_CONTENT_LEN + 1),
            project_root=tmp_path,
        )


def test_write_memory_rejects_too_many_tags(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="too many tags"):
        write_memory(
            topic="t", content="x", tags=[f"t{i}" for i in range(11)], project_root=tmp_path
        )


# ── Qdrant write-create lifecycle ───────────────────────────────────────────


def test_index_single_doc_ensures_collection_before_upsert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_collection runs before upsert when collection is missing (Req-07)."""
    call_order: list[str] = []

    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    fake_client.create_collection.side_effect = lambda **_: call_order.append("create")
    fake_client.upsert.side_effect = lambda **_: call_order.append("upsert")

    class _SparseVec:
        indices = [1]
        values = [0.5]

    monkeypatch.setattr("qdrant_client.QdrantClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(
        "supamem.embedders.build_dense_embedder",
        lambda: MagicMock(embed=lambda batch: iter([[0.1] * 384 for _ in batch])),
    )
    monkeypatch.setattr(
        "supamem.embedders.build_sparse_embedder",
        lambda: MagicMock(embed=lambda batch: iter([_SparseVec() for _ in batch])),
    )
    monkeypatch.setattr(
        "supamem.indexer.chunker.chunk_markdown", lambda body: [body]
    )

    cfg = ResolvedConfig(collection="agent-test")
    target = tmp_path / "test.md"
    target.write_text("hello", encoding="utf-8")

    n = _index_single_doc(
        cfg,
        target_path=target,
        body="hello world",
        point_id="00000000-0000-0000-0000-000000000001",
    )
    assert n == 1
    assert call_order == ["create", "upsert"]


def test_memory_writer_forbidden_collection_blocked(tmp_path: Path) -> None:
    """Forbidden legacy collection → indexed=False, file still written (Req-06)."""
    cfg = ResolvedConfig(sources=[".claude/insights/"], collection="dev_memory")
    (tmp_path / ".claude" / "insights").mkdir(parents=True)

    res = write_memory(topic="t", content="x", config=cfg, project_root=tmp_path)

    assert res.indexed is False
    assert res.error is not None
    assert "forbidden" in res.error.lower()
    assert "dev_memory" in res.error
    assert Path(res.path).exists()


# ── partial failure ─────────────────────────────────────────────────────────


def test_write_memory_returns_indexed_false_on_qdrant_failure(tmp_path: Path) -> None:
    """File is still written; agent can retry indexing later."""
    cfg = ResolvedConfig(sources=[".claude/insights/"])
    (tmp_path / ".claude" / "insights").mkdir(parents=True)

    with patch(
        "supamem.memory_writer._index_single_doc",
        side_effect=ConnectionError("qdrant unreachable"),
    ):
        res = write_memory(topic="t", content="x", config=cfg, project_root=tmp_path)

    assert res.indexed is False
    assert res.points_added == 0
    assert res.error is not None
    assert "qdrant unreachable" in res.error
    assert Path(res.path).exists()  # file persisted despite index failure


# ── result type ─────────────────────────────────────────────────────────────


def test_write_result_is_dataclass() -> None:
    res = WriteResult(
        summary="ok", path="/tmp/x.md", topic="t", slug="t", indexed=True, points_added=1
    )
    assert res.indexed is True
    assert res.error is None


def test_namespace_uuid_is_stable() -> None:
    """If this assertion ever fires, you've changed the namespace and broken
    idempotency for every existing agent-write across all projects."""
    assert str(NAMESPACE_AGENT_WRITE) == "0e6c4d3f-3a8c-5b8b-9f2e-7c8b4a4f1d72"
