"""Plan 17-B2 Task 1 (RED) — chunker entry-point dispatch tests.

Locks the wiring contract for D-WIRE-01..02:
- ``eval/runner.py`` resolves the chunker via
  ``importlib.metadata.entry_points(group="supamem.chunker")`` keyed on
  ``cfg.chunker``.
- ``coderag.ingest.ingest()`` accepts a ``chunker_fn`` kwarg that
  overrides the module-top ``chunk_markdown`` fallback.
- D-SCOPE-05 carry-lock stays green: ``coderag/ingest.py`` does NOT
  add ``importlib.metadata`` or new ``supamem.indexer.*`` imports.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_runner_loads_chunker_via_entry_points(monkeypatch) -> None:
    """``_resolve_chunker('tree_sitter_code')`` returns the entry-point's
    loaded callable, identity-equal to ``ep.load()``."""
    from supamem.eval import runner as runner_mod

    eps = [e for e in entry_points(group="supamem.chunker") if e.name == "tree_sitter_code"]
    assert eps, "tree_sitter_code entry-point must be registered (Plan 17-B)"
    expected_fn = eps[0].load()

    resolved = runner_mod._resolve_chunker("tree_sitter_code")
    assert resolved is expected_fn


def test_runner_chunker_dispatch_default() -> None:
    """Empty / unset / 'markdown_header' resolves to ``chunk_markdown``."""
    from supamem.eval import runner as runner_mod
    from supamem.indexer.chunker import chunk_markdown

    assert runner_mod._resolve_chunker("") is chunk_markdown
    assert runner_mod._resolve_chunker("markdown_header") is chunk_markdown


def test_unknown_chunker_name_raises() -> None:
    """Unknown chunker name raises SystemExit with an actionable message
    naming the bad value."""
    from supamem.eval import runner as runner_mod

    with pytest.raises(SystemExit):
        runner_mod._resolve_chunker("definitely_not_a_chunker")


def test_ingest_accepts_chunker_fn_kwarg(monkeypatch, tmp_path) -> None:
    """``ingest(..., chunker_fn=fn)`` uses the injected callable for
    chunking, NOT the module-top ``chunk_markdown``."""
    from supamem.eval.coderag import ingest as ingest_mod

    # Create one repo with one file in a fake corpus walk.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    one_file = repo_root / "x.md"
    one_file.write_text("HELLO WORLD\n", encoding="utf-8")

    monkeypatch.setattr(ingest_mod, "walk_corpus", lambda _root: [one_file])

    class _FakeDense:
        def embed(self, texts):
            for _ in texts:
                yield [0.1] * 384

    class _FakeSparse:
        def embed(self, texts):
            for _ in texts:
                yield SimpleNamespace(indices=[1], values=[0.5])

    monkeypatch.setattr(ingest_mod, "build_dense_embedder", lambda: _FakeDense())
    monkeypatch.setattr(ingest_mod, "build_sparse_embedder", lambda: _FakeSparse())

    client = MagicMock()
    client.get_collections.return_value = SimpleNamespace(collections=[])

    sentinel_called: list[str] = []

    def custom_chunker(text: str) -> list[str]:
        sentinel_called.append(text)
        return ["CUSTOM-A", "CUSTOM-B"]

    n = ingest_mod.ingest(
        SimpleNamespace(),
        [{"repo_slug": "fake/repo", "repo_root": repo_root}],
        client=client,
        chunker_fn=custom_chunker,
    )

    assert n == 2
    assert sentinel_called == ["HELLO WORLD\n"]
    upserted_points = [c.kwargs["points"] for c in client.upsert.call_args_list]
    flat = [p for batch in upserted_points for p in batch]
    docs = sorted(p.payload["document"] for p in flat)
    assert docs == ["CUSTOM-A", "CUSTOM-B"]


def test_ingest_default_chunker_fn_falls_back_to_chunk_markdown(monkeypatch, tmp_path) -> None:
    """Default ``ingest(...)`` (no chunker_fn) calls module-top ``chunk_markdown``."""
    from supamem.eval.coderag import ingest as ingest_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    one_file = repo_root / "y.md"
    one_file.write_text("FALLBACK SOURCE\n", encoding="utf-8")

    monkeypatch.setattr(ingest_mod, "walk_corpus", lambda _root: [one_file])

    class _FakeDense:
        def embed(self, texts):
            for _ in texts:
                yield [0.1] * 384

    class _FakeSparse:
        def embed(self, texts):
            for _ in texts:
                yield SimpleNamespace(indices=[1], values=[0.5])

    monkeypatch.setattr(ingest_mod, "build_dense_embedder", lambda: _FakeDense())
    monkeypatch.setattr(ingest_mod, "build_sparse_embedder", lambda: _FakeSparse())

    seen: list[str] = []

    def fake_chunk_markdown(text: str) -> list[str]:
        seen.append(text)
        return ["DEFAULTED"]

    monkeypatch.setattr(ingest_mod, "chunk_markdown", fake_chunk_markdown)

    client = MagicMock()
    client.get_collections.return_value = SimpleNamespace(collections=[])

    n = ingest_mod.ingest(
        SimpleNamespace(),
        [{"repo_slug": "fake/repo", "repo_root": repo_root}],
        client=client,
    )

    assert n == 1
    assert seen == ["FALLBACK SOURCE\n"]
    upserted_points = [c.kwargs["points"] for c in client.upsert.call_args_list]
    flat = [p for batch in upserted_points for p in batch]
    assert [p.payload["document"] for p in flat] == ["DEFAULTED"]


def test_d_scope_05_carry_lock_still_passes() -> None:
    """Re-run the existing D-SCOPE-05 carry-lock test to ensure
    ``coderag/ingest.py`` adds NO new ``supamem.indexer.*`` or
    ``importlib.metadata`` imports under this plan."""
    import ast
    from pathlib import Path

    from supamem.eval.coderag import ingest as ingest_mod

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
            if mod == "importlib.metadata" or mod.startswith("importlib.metadata."):
                bad.append(f"from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("supamem.indexer"):
                    bad.append(f"import {alias.name}")
                if alias.name.startswith("importlib.metadata"):
                    bad.append(f"import {alias.name}")
    assert not bad, f"D-SCOPE-05 carry-lock breach: {bad}"
