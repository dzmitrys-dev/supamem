"""v0.4.0a5 — embedder selection wiring, config gate, index-epoch gate, manifest GC.

The 2026-11-14 effectiveness study's embedder fix exposed that ``cfg.embedder``
was accepted-but-ignored: every funnel hardcoded the default model. These tests
pin the wiring (resolver → three funnels), the fail-closed name gate, the
cross-collection manifest epoch, and the dead-entry prune.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.embedders import DEFAULT_DENSE_MODEL, dense_model_id_for
from supamem.indexer.manifest import EPOCH_SUFFIX, Manifest


# ---- dense_model_id_for ------------------------------------------------------


def test_resolver_default_is_byte_identical() -> None:
    cfg = ResolvedConfig()
    assert cfg.embedder == "minilm"
    assert dense_model_id_for(cfg) == DEFAULT_DENSE_MODEL


def test_resolver_multilingual_plugin() -> None:
    cfg = ResolvedConfig(embedder="multilingual_minilm")
    assert (
        dense_model_id_for(cfg)
        == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )


def test_resolver_unknown_name_raises_lookup() -> None:
    cfg = ResolvedConfig(embedder="no_such_embedder")
    with pytest.raises(LookupError):
        dense_model_id_for(cfg)


def test_plugin_registered_as_entry_point() -> None:
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="supamem.embedder")}
    assert "multilingual_minilm" in names


def test_build_dense_embedder_follows_cfg() -> None:
    from supamem.embedders import build_dense_embedder

    model = dense_model_id_for(ResolvedConfig(embedder="multilingual_minilm"))
    be = build_dense_embedder(model)
    assert be.model == model
    assert be._impl is None  # lazy — construction never loads models


# ---- config gate --------------------------------------------------------------


def test_load_config_rejects_unknown_embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    cfg_file = tmp_path / "supamem.toml"
    cfg_file.write_text("[supamem]\nembedder = \"nope_minilm\"\ncollection = \"t\"\n")
    env = {
        **__import__("os").environ,
        "SUPAMEM_CONFIG": str(cfg_file),
        "SUPAMEM_NO_UPDATE_CHECK": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "200",
    }
    env.pop("FORCE_COLOR", None)
    r = subprocess.run(
        [sys.executable, "-m", "supamem", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 2
    assert "embedder" in (r.stderr + r.stdout)


def test_load_config_accepts_multilingual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.config import load_config

    cfg_file = tmp_path / "supamem.toml"
    cfg_file.write_text(
        '[supamem]\nembedder = "multilingual_minilm"\ncollection = "t-ml"\n'
        'qdrant_url = "http://localhost:6333"\n'
    )
    monkeypatch.setenv("SUPAMEM_CONFIG", str(cfg_file))
    cfg, _ = load_config()
    assert cfg.embedder == "multilingual_minilm"
    assert dense_model_id_for(cfg).endswith("MiniLM-L12-v2")


# ---- Manifest: per-entry epoch + prune -----------------------------------------


def test_manifest_prune_and_epoch_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    live = tmp_path / "live.md"
    live.write_text("# live\n")
    m = Manifest()
    m.active_epoch = "collA|modelA"
    m.update(str(live), "tuned", "sha1")
    m.update("/nonexistent/gone.md", "tuned", "sha2")
    m.save(p)
    assert "tuned" + EPOCH_SUFFIX in p.read_text()

    loaded = Manifest.load(p)
    assert loaded.entries[str(live)]["tuned" + EPOCH_SUFFIX] == "collA|modelA"
    assert loaded.active_epoch is None  # the run epoch itself is never persisted
    assert loaded.prune_missing() == 1
    assert str(live) in loaded.entries
    assert "/nonexistent/gone.md" not in loaded.entries


def test_epoch_switch_and_back_reindexes_both_ways() -> None:
    """The shared-manifest trap: A → B → A must re-embed at each switch."""
    m = Manifest()
    m.active_epoch = "collA|minilm"
    assert m.needs_index("/r/a.md", "h", "tuned")
    m.update("/r/a.md", "tuned", "h")
    assert not m.needs_index("/r/a.md", "h", "tuned")  # same epoch, same hash → skip

    m.active_epoch = "collB|multilingual"
    assert m.needs_index("/r/a.md", "h", "tuned")  # unchanged file, NEW collection → dirty
    m.update("/r/a.md", "tuned", "h")

    m.active_epoch = "collA|minilm"
    assert m.needs_index("/r/a.md", "h", "tuned")  # and back again → dirty


def test_alternating_projects_never_thrash() -> None:
    """Regression pin for the design bug caught in review: ONE global epoch made
    alternating index runs of two projects re-embed everything every time."""
    m = Manifest()
    m.active_epoch = "supamem-ferrohex|minilm"
    m.update("/ferrohex/docs/a.md", "tuned", "h1")
    m.active_epoch = "supamem-softchat|minilm"
    m.update("/softchat/.claude/rules/b.md", "tuned", "h2")
    # back to ferrohex: its own unchanged file must NOT be dirty
    m.active_epoch = "supamem-ferrohex|minilm"
    assert not m.needs_index("/ferrohex/docs/a.md", "h1", "tuned")
    m.active_epoch = "supamem-softchat|minilm"
    assert not m.needs_index("/softchat/.claude/rules/b.md", "h2", "tuned")


def test_pre_a5_entries_reindex_once(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    p.write_text('{"/r/a.md": {"prod": "", "tuned": "h"}}')
    m = Manifest.load(p)
    m.active_epoch = "collA|minilm"
    assert m.needs_index("/r/a.md", "h", "tuned")  # no epoch → unknown collection → dirty
    m.update("/r/a.md", "tuned", "h")
    assert not m.needs_index("/r/a.md", "h", "tuned")


def test_no_active_epoch_keeps_pre_a5_semantics() -> None:
    m = Manifest()
    m.update("/r/a.md", "tuned", "h")
    assert not m.needs_index("/r/a.md", "h", "tuned")
    assert m.needs_index("/r/a.md", "h2", "tuned")


def test_transcript_epoch() -> None:
    m = Manifest()
    m.active_epoch = "collA|minilm"
    assert m.transcript_needs_index("s", "u", "c")
    m.transcript_update("s", "u", "c")
    assert not m.transcript_needs_index("s", "u", "c")
    m.active_epoch = "collB|multilingual"
    assert m.transcript_needs_index("s", "u", "c")  # same message, new collection → re-embed


def test_transcript_epoch_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "manifest.json"
    m = Manifest()
    m.active_epoch = "collA|minilm"
    m.transcript_update("s", "u", "c")
    m.save(p)
    loaded = Manifest.load(p)
    assert loaded.transcripts["s"]["u"]["epoch"] == "collA|minilm"


# ---- run_index: epoch wiring + GC + embedder wiring ------------------------------


def _fake_qdrant_recorder() -> MagicMock:
    client = MagicMock()
    client.scroll.return_value = ([], None)
    return client


def test_run_index_sets_active_epoch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import supamem.indexer as idx

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n")
    monkeypatch.chdir(tmp_path)
    seen: dict[str, Any] = {}

    def fake_process(**kw: Any) -> tuple[int, int]:
        seen["epoch"] = kw["manifest"].active_epoch
        return (1, 0)

    client = _fake_qdrant_recorder()
    monkeypatch.setattr(idx, "QdrantClient", lambda **kw: client)
    monkeypatch.setattr(idx, "ensure_collection", lambda *a, **k: None)
    monkeypatch.setattr(idx, "build_dense_embedder", lambda *a, **k: MagicMock())
    monkeypatch.setattr(idx, "build_sparse_embedder", lambda: MagicMock())
    monkeypatch.setattr(idx, "_ensure_payload_indexes", lambda *a, **k: None)
    monkeypatch.setattr(idx, "_gc_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(idx, "_process_one_source", fake_process)

    cfg = ResolvedConfig(
        sources=["docs"],
        collection="collB",
        cache_dir=str(tmp_path / "cache"),
        qdrant_url="http://localhost:6333",
        embedder="multilingual_minilm",
    )
    assert idx.run_index(target="tuned", config=cfg) == 0
    assert seen["epoch"] == "collB|sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def test_run_index_prunes_dead_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import supamem.indexer as idx

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n")
    monkeypatch.chdir(tmp_path)
    client = _fake_qdrant_recorder()
    monkeypatch.setattr(idx, "QdrantClient", lambda **kw: client)
    monkeypatch.setattr(idx, "ensure_collection", lambda *a, **k: None)
    monkeypatch.setattr(idx, "build_dense_embedder", lambda *a, **k: MagicMock())
    monkeypatch.setattr(idx, "build_sparse_embedder", lambda: MagicMock())
    monkeypatch.setattr(idx, "_ensure_payload_indexes", lambda *a, **k: None)
    monkeypatch.setattr(idx, "_gc_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(idx, "_process_one_source", lambda **kw: (1, 0))

    cfg = ResolvedConfig(
        sources=["docs"],
        collection="collG",
        cache_dir=str(tmp_path / "cache"),
        qdrant_url="http://localhost:6333",
    )
    mp = idx._manifest_path(cfg)
    mp.parent.mkdir(parents=True, exist_ok=True)
    seed = Manifest()
    seed.update("/private/tmp/fhx-spikes/old.md", "tuned", "sha")
    seed.transcript_update("sess-1", "msg-1", "h1")  # reserved __transcripts__ key
    seed.save(mp)

    assert idx.run_index(target="tuned", config=cfg) == 0
    reloaded = Manifest.load(mp)
    assert "/private/tmp/fhx-spikes/old.md" not in reloaded.entries
    # Reserved keys survive the prune untouched.
    assert reloaded.transcripts.get("sess-1", {}).get("msg-1", {}).get("content_hash") == "h1"


def test_run_index_wires_embedder_model_into_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import supamem.indexer as idx

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n")
    monkeypatch.chdir(tmp_path)
    client = _fake_qdrant_recorder()
    monkeypatch.setattr(idx, "QdrantClient", lambda **kw: client)
    monkeypatch.setattr(idx, "ensure_collection", lambda *a, **k: None)
    models: list[str] = []

    def fake_dense(model: str = DEFAULT_DENSE_MODEL) -> MagicMock:
        models.append(model)
        return MagicMock()

    monkeypatch.setattr(idx, "build_dense_embedder", fake_dense)
    monkeypatch.setattr(idx, "build_sparse_embedder", lambda: MagicMock())
    monkeypatch.setattr(idx, "_ensure_payload_indexes", lambda *a, **k: None)
    monkeypatch.setattr(idx, "_gc_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(idx, "_process_one_source", lambda **kw: (1, 0))

    cfg = ResolvedConfig(
        sources=["docs"],
        collection="collH",
        cache_dir=str(tmp_path / "cache"),
        qdrant_url="http://localhost:6333",
        embedder="multilingual_minilm",
    )
    assert idx.run_index(target="tuned", config=cfg) == 0
    assert models and models[-1].endswith("MiniLM-L12-v2")


def test_retrieval_backend_uses_cfg_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """The QUERY side must embed in the same space as the INDEX side."""
    import supamem.retrieval.tuned_hybrid as th

    cfg = ResolvedConfig(
        collection="t-ml", embedder="multilingual_minilm", qdrant_url="http://localhost:6333"
    )
    backend = th.TunedHybridBackend(config=cfg)
    loaded: dict[str, Any] = {}

    class FakeTextEmbedding:
        def __init__(self, model: str, **kw: Any) -> None:
            loaded["dense"] = model

    class FakeSparse:
        def __init__(self, model: str, **kw: Any) -> None:
            loaded["sparse"] = model

    monkeypatch.setattr(th, "TextEmbedding", FakeTextEmbedding)
    monkeypatch.setattr(th, "SparseTextEmbedding", FakeSparse)
    monkeypatch.setattr(th, "_SPARSE_AVAILABLE", True)
    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda **kw: MagicMock()
    )
    backend._ensure()
    assert loaded["dense"].endswith("MiniLM-L12-v2")
    assert loaded["sparse"] == "Qdrant/bm25"
