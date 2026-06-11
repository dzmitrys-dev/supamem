"""Tests for the indexer dispatcher (Plan 06-03 Task 2 + Task 3).

Locks Pattern 2 (dispatcher accepts both ``list[str]`` and ``list[ChunkRecord]``),
the ``*.jsonl`` routing branch, fail-loud W4 contract on malformed transcript
records, and the B3 / D-22 progress-bar gating (skipped under NO_COLOR or
non-tty stdout).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.indexer import (
    _expand_sources,
    _normalize_chunks,
    _progress_enabled,
    run_index,
)
from supamem.indexer.transcript import ChunkRecord


# ───── _normalize_chunks (Pattern 2) ───────────────────────────────────────


def test_normalize_chunks_string_list() -> None:
    out = _normalize_chunks(
        ["a", "b"], default_metadata={"chunker": "markdown_header"}
    )
    assert len(out) == 2
    assert all(isinstance(r, ChunkRecord) for r in out)
    assert out[0].text == "a"
    assert out[0].metadata == {"chunker": "markdown_header"}
    assert out[1].text == "b"


def test_normalize_chunks_chunkrecord_list() -> None:
    rec = ChunkRecord(text="x", metadata={"chunker": "transcript"})
    out = _normalize_chunks([rec], default_metadata={"chunker": "should-not-leak"})
    assert out is not None
    assert len(out) == 1
    assert out[0].metadata == {"chunker": "transcript"}


def test_normalize_chunks_empty() -> None:
    assert _normalize_chunks([], default_metadata={"chunker": "x"}) == []


# ───── _expand_sources includes *.jsonl ────────────────────────────────────


def test_expand_sources_includes_jsonl(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(
        '{"type":"user","uuid":"u1","sessionId":"s1"}\n', encoding="utf-8"
    )
    (tmp_path / "c.txt").write_text("c\n", encoding="utf-8")
    out = _expand_sources([str(tmp_path)])
    suffixes = sorted(p.suffix for p in out)
    assert suffixes == [".jsonl", ".md"]
    assert not any(p.suffix == ".txt" for p in out)


def test_expand_sources_md_only_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# b\n", encoding="utf-8")
    out = _expand_sources([str(tmp_path)])
    assert len(out) == 2
    assert all(p.suffix == ".md" for p in out)


def test_expand_sources_jsonl_file_directly(tmp_path: Path) -> None:
    f = tmp_path / "session.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    out = _expand_sources([str(f)])
    assert out == [f.resolve()]


# ───── Dispatcher routing + dedupe ─────────────────────────────────────────


_FIXTURE = Path(__file__).parent / "fixtures" / "transcripts" / "simple_session.jsonl"


def _wire_mocks(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    fake_dense = MagicMock()
    fake_dense.embed = lambda batch: iter([[0.1] * 384 for _ in batch])

    class _SparseVec:
        def __init__(self) -> None:
            self.indices = [1, 2, 3]
            self.values = [0.5, 0.4, 0.3]

    fake_sparse = MagicMock()
    fake_sparse.embed = lambda batch: iter([_SparseVec() for _ in batch])

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(
        indexer_mod, "QdrantClient", lambda *a, **k: fake_client, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_dense_embedder", lambda *a, **k: fake_dense, raising=False
    )
    monkeypatch.setattr(
        indexer_mod, "build_sparse_embedder", lambda *a, **k: fake_sparse, raising=False
    )
    return fake_client, fake_dense, fake_sparse


def test_run_index_ensures_collection_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing collection → create_collection once before upsert (Req-07)."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    fake_client, _, _ = _wire_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(md)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    rc = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc == 0
    fake_client.create_collection.assert_called_once()


def test_run_index_forbidden_collection_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy reserved collection names blocked before any upsert (Req-06)."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    fake_client, _, _ = _wire_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(md)],
        cache_dir=str(tmp_path / "cache"),
        collection="dev_memory",
    )
    with pytest.raises(RuntimeError, match="forbidden"):
        run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    fake_client.upsert.assert_not_called()


def test_dispatch_routes_jsonl_to_chunk_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*.jsonl source MUST go through chunk_transcript; *.md MUST go through chunk_markdown."""
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    fake_client, _, _ = _wire_mocks(monkeypatch)

    transcript_calls: list[Any] = []
    markdown_calls: list[Any] = []

    import supamem.indexer as indexer_mod
    real_chunk_transcript = indexer_mod.chunk_transcript
    real_chunk_markdown = indexer_mod.chunk_markdown

    def _spy_chunk_transcript(text: str, **kw: Any) -> Any:
        transcript_calls.append(kw.get("source_path"))
        return real_chunk_transcript(text, **kw)

    def _spy_chunk_markdown(body: str) -> Any:
        markdown_calls.append(body[:30])
        return real_chunk_markdown(body)

    monkeypatch.setattr(indexer_mod, "chunk_transcript", _spy_chunk_transcript)
    monkeypatch.setattr(indexer_mod, "chunk_markdown", _spy_chunk_markdown)

    cfg = ResolvedConfig(
        sources=[str(tmp_path)],
        cache_dir=str(tmp_path / "cache"),
        collection="t",
    )
    rc = run_index(target="tuned", force=True, sources=[str(tmp_path)], config=cfg)
    assert rc == 0
    assert len(transcript_calls) == 1
    assert len(markdown_calls) == 1
    assert fake_client.upsert.called


def test_payload_carries_chunkrecord_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ChunkRecord.metadata propagates into the Qdrant payload (D-20)."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    fake_client, _, _ = _wire_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(jsonl)],
        cache_dir=str(tmp_path / "cache"),
        collection="t",
    )
    rc = run_index(target="tuned", force=True, sources=[str(jsonl)], config=cfg)
    assert rc == 0
    assert fake_client.upsert.called
    points = fake_client.upsert.call_args.kwargs.get("points") or fake_client.upsert.call_args.args[1]
    assert points
    p0 = points[0]
    payload = p0.payload
    assert payload.get("chunker") == "transcript"
    assert payload.get("room") == "transcript"
    assert "transcript" in payload
    assert isinstance(payload["transcript"], dict)
    assert "tool_uses" in payload
    assert isinstance(payload["tool_uses"], list)


def test_per_message_dedupe_skips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Index twice; second run upserts nothing (D-25, D-27, INGEST-04)."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    fake_client, _, _ = _wire_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(jsonl)],
        cache_dir=str(tmp_path / "cache"),
        collection="t",
    )
    rc1 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc1 == 0
    first_calls = fake_client.upsert.call_count
    assert first_calls >= 1

    fake_client.reset_mock()
    rc2 = run_index(target="tuned", force=False, sources=[str(jsonl)], config=cfg)
    assert rc2 == 0
    assert fake_client.upsert.call_count == 0, "second run should skip all messages"


# ───── W4 fail-loud on malformed ChunkRecord ───────────────────────────────


def test_dispatcher_raises_on_missing_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic ChunkRecord with no session_id must raise ValueError."""
    jsonl = tmp_path / "broken.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")  # any non-empty file; we monkeypatch
    _wire_mocks(monkeypatch)

    bad = ChunkRecord(
        text="hello",
        metadata={
            "chunker": "transcript",
            "room": "transcript",
            "transcript": {
                "user_uuid": "u1",
                "assistant_uuids": [],
                "turn_index": 0,
            },
            "tool_uses": [],
        },
    )

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(
        indexer_mod, "chunk_transcript", lambda text, **kw: [bad]
    )

    cfg = ResolvedConfig(
        sources=[str(jsonl)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    with pytest.raises(ValueError, match="missing session_id or message_uuid"):
        run_index(target="tuned", force=True, sources=[str(jsonl)], config=cfg)


def test_dispatcher_raises_on_missing_user_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic ChunkRecord with no user_uuid must raise ValueError."""
    jsonl = tmp_path / "broken.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    _wire_mocks(monkeypatch)

    bad = ChunkRecord(
        text="hello",
        metadata={
            "chunker": "transcript",
            "room": "transcript",
            "transcript": {
                "session_id": "s1",
                "assistant_uuids": [],
                "turn_index": 0,
            },
            "tool_uses": [],
        },
    )

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(
        indexer_mod, "chunk_transcript", lambda text, **kw: [bad]
    )

    cfg = ResolvedConfig(
        sources=[str(jsonl)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    with pytest.raises(ValueError, match="missing session_id or message_uuid"):
        run_index(target="tuned", force=True, sources=[str(jsonl)], config=cfg)


# ───── B3 progress-bar gating ──────────────────────────────────────────────


def test_progress_bar_disabled_under_no_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NO_COLOR=1 → progress bar skipped; no Rich escape sequences leak."""
    monkeypatch.setenv("NO_COLOR", "1")
    md = tmp_path / "doc.md"
    md.write_text("# Header\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    _wire_mocks(monkeypatch)

    cfg = ResolvedConfig(
        sources=[str(md)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    rc = run_index(target="tuned", force=True, sources=[str(md)], config=cfg)
    assert rc == 0

    assert _progress_enabled() is False
    out = capsys.readouterr().out
    # No ANSI escape sequence prefix should leak
    assert "\x1b[" not in out


def test_progress_bar_renders_when_terminal_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the enabled path; assert Progress(...) is constructed and updated."""
    monkeypatch.delenv("NO_COLOR", raising=False)

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "_progress_enabled", lambda: True)

    md_a = tmp_path / "a.md"
    md_a.write_text("# A\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")
    md_b = tmp_path / "b.md"
    md_b.write_text("# B\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    _wire_mocks(monkeypatch)

    progress_constructions: list[Any] = []
    update_calls: list[dict] = []

    real_progress_cls = indexer_mod.Progress

    class _SpyProgress(real_progress_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            progress_constructions.append(kwargs.get("console"))
            super().__init__(*args, **kwargs)

        def update(self, task_id: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            update_calls.append(dict(kwargs))
            return super().update(task_id, **kwargs)

    monkeypatch.setattr(indexer_mod, "Progress", _SpyProgress)

    cfg = ResolvedConfig(
        sources=[str(tmp_path)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    rc = run_index(target="tuned", force=True, sources=[str(tmp_path)], config=cfg)
    assert rc == 0
    assert len(progress_constructions) >= 1
    assert len(update_calls) >= 2  # one per source


def test_progress_bar_chunks_field_increments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``chunks`` field passed to progress.update must be monotonically non-decreasing."""
    monkeypatch.delenv("NO_COLOR", raising=False)

    import supamem.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "_progress_enabled", lambda: True)

    md_a = tmp_path / "a.md"
    md_a.write_text("# A\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")
    md_b = tmp_path / "b.md"
    md_b.write_text("# B\n" + " ".join(["lorem"] * 30) + "\n", encoding="utf-8")

    _wire_mocks(monkeypatch)

    update_chunks: list[int] = []
    real_progress_cls = indexer_mod.Progress

    class _SpyProgress(real_progress_cls):  # type: ignore[misc, valid-type]
        def update(self, task_id: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            if "chunks" in kwargs:
                update_chunks.append(int(kwargs["chunks"]))
            return super().update(task_id, **kwargs)

    monkeypatch.setattr(indexer_mod, "Progress", _SpyProgress)

    cfg = ResolvedConfig(
        sources=[str(tmp_path)], cache_dir=str(tmp_path / "cache"), collection="t"
    )
    rc = run_index(target="tuned", force=True, sources=[str(tmp_path)], config=cfg)
    assert rc == 0
    assert update_chunks
    # Monotonically non-decreasing
    for prev, cur in zip(update_chunks, update_chunks[1:]):
        assert cur >= prev
