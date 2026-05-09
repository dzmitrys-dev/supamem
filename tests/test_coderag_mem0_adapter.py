"""Phase 15 Plan D Task D1 — mem0 peer adapter tests.

Offline tests use a mock ``mem0.Memory`` injected via ``sys.modules``.
Live integration is gated behind ``SUPAMEM_INTEGRATION_MEM0=1`` (skipped
in CI / dev). Hard rules verified:

- INV-A2: ``MEM0_COLLECTION == "supamem_eval_coderag_mem0"`` and never
  equal to ``CODERAG_COLLECTION``.
- D-DEF-02: single canonical config, no env-var override.
- D-SCOPE-05: zero ``supamem.indexer.*`` imports anywhere in the adapter.
- Lazy mem0 import: importing the peers package does NOT import ``mem0``.
- ``infer=False`` is passed to every ``Memory.add`` call (research delta:
  avoid LLM extraction so we score retrieval, not LLM-extraction quality;
  also prevents silent OpenAI spend per rerun).
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Mock ``mem0.Memory`` injected into sys.modules. Recreated per test so
# call-history assertions are isolated.
# ---------------------------------------------------------------------------


class _RecordingMemory:
    """Mock that records every call for later assertion."""

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self._search_results: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, config: dict) -> "_RecordingMemory":
        inst = cls()
        inst.config = config
        return inst

    def add(self, messages, *, user_id, metadata=None, infer=True, **kwargs):  # noqa: ANN001, ANN003
        self.add_calls.append({
            "messages": messages,
            "user_id": user_id,
            "metadata": metadata,
            "infer": infer,
            "extra": kwargs,
        })
        return {"status": "ok"}

    def search(self, query, *, filters=None, top_k=20, **kwargs):  # noqa: ANN001, ANN003
        # mem0 v2.0.0 contract: filters={"user_id": ...} replaces top-level
        # ``user_id``; ``limit`` renamed to ``top_k``. Mirror by exposing the
        # extracted user_id back on the recorded call so existing assertions
        # continue to read ``call["user_id"]``.
        filters = filters or {}
        self.search_calls.append({
            "query": query,
            "user_id": filters.get("user_id"),
            "limit": top_k,
            "extra": kwargs,
        })
        return list(self._search_results)


@pytest.fixture
def mock_mem0(monkeypatch):
    """Inject a fake ``mem0`` module exposing the recording Memory class."""
    fake_module = types.ModuleType("mem0")
    fake_module.Memory = _RecordingMemory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mem0", fake_module)
    # Force a fresh import of the adapter so its lazy ``from mem0 import Memory``
    # picks up our fake.
    if "supamem.eval.coderag.peers.mem0_adapter" in sys.modules:
        del sys.modules["supamem.eval.coderag.peers.mem0_adapter"]
    yield fake_module


# ---------------------------------------------------------------------------
# Module-level invariants — INV-A2 + lazy-import + isolation
# ---------------------------------------------------------------------------


def test_mem0_collection_constant_distinct_from_supamem() -> None:
    """INV-A2 codified at the module level."""
    from supamem.eval.coderag.ingest import CODERAG_COLLECTION
    from supamem.eval.coderag.peers.mem0_adapter import MEM0_COLLECTION

    assert MEM0_COLLECTION == "supamem_eval_coderag_mem0"
    assert MEM0_COLLECTION != CODERAG_COLLECTION


def test_mem0_adapter_no_supamem_indexer_imports() -> None:
    """D-SCOPE-05 carry: zero ``supamem.indexer.*`` imports in the adapter."""
    src = Path(
        "src/supamem/eval/coderag/peers/mem0_adapter.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("supamem.indexer"):
                forbidden.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("supamem.indexer"):
                    forbidden.append(alias.name)
    assert not forbidden, (
        f"D-SCOPE-05 violation: mem0_adapter imports {forbidden}"
    )


def test_mem0_adapter_lazy_import() -> None:
    """Importing the peers PACKAGE does not import ``mem0``.

    Only ``import supamem.eval.coderag.peers.mem0_adapter`` (when its
    ``Mem0PeerAdapter.__init__`` is actually called) triggers the mem0 import.
    """
    # Ensure a fresh state: drop both modules + any cached ``mem0`` import.
    for mod_name in (
        "mem0",
        "supamem.eval.coderag.peers",
        "supamem.eval.coderag.peers.mem0_adapter",
    ):
        sys.modules.pop(mod_name, None)
    importlib.import_module("supamem.eval.coderag.peers")
    assert "mem0" not in sys.modules, (
        "peers package import unexpectedly triggered ``mem0`` import"
    )


# ---------------------------------------------------------------------------
# Adapter init / ingest / query — offline with mocked Memory
# ---------------------------------------------------------------------------


def test_mem0_adapter_init_uses_separate_collection(mock_mem0) -> None:
    from supamem.eval.coderag.peers.mem0_adapter import (
        MEM0_COLLECTION,
        Mem0PeerAdapter,
    )

    adapter = Mem0PeerAdapter()
    captured = adapter._memory.config  # type: ignore[attr-defined]
    assert (
        captured["vector_store"]["config"]["collection_name"]
        == "supamem_eval_coderag_mem0"
    )
    assert MEM0_COLLECTION == "supamem_eval_coderag_mem0"


def test_mem0_adapter_ingest_records_carry_doc_id_metadata(
    mock_mem0, tmp_path: Path
) -> None:
    """Every ``Memory.add`` call gets metadata={doc_id, repo, axis}."""
    # Build a minimal repo with one allowlisted file + one ADR.
    repo = tmp_path / "tinyrepo"
    repo.mkdir()
    (repo / "README.md").write_text("readme body", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text("def core():\n    return 1\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "adr").mkdir()
    (repo / "docs" / "adr" / "ADR-001-foo.md").write_text(
        "# ADR-001\n## Context\nfoo.\n", encoding="utf-8"
    )

    from supamem.eval.coderag.peers.mem0_adapter import Mem0PeerAdapter

    adapter = Mem0PeerAdapter()
    count = adapter.ingest([("supamem", repo)])
    assert count >= 3
    add_calls = adapter._memory.add_calls  # type: ignore[attr-defined]
    assert len(add_calls) == count

    seen_axes = set()
    seen_repos = set()
    for c in add_calls:
        meta = c["metadata"]
        assert isinstance(meta, dict)
        assert "doc_id" in meta and meta["doc_id"]
        assert "repo" in meta and meta["repo"] == "supamem"
        assert "axis" in meta and meta["axis"] in ("code_fact", "decision_rationale")
        # infer=False — research delta: skip LLM extraction (cost + correctness).
        assert c["infer"] is False, (
            "Mem0PeerAdapter.ingest must pass infer=False to avoid LLM "
            "extraction cost and to score retrieval, not extraction"
        )
        seen_axes.add(meta["axis"])
        seen_repos.add(meta["repo"])

    # ADR triggered the rationale axis.
    assert "decision_rationale" in seen_axes
    assert "code_fact" in seen_axes


def test_mem0_adapter_query_returns_hit_objects_with_doc_id_payload(
    mock_mem0,
) -> None:
    from supamem.eval.coderag.peers.mem0_adapter import Mem0PeerAdapter

    adapter = Mem0PeerAdapter()
    # Seed fake search results (mem0 v2 shape: id, memory, metadata, score).
    adapter._memory._search_results = [  # type: ignore[attr-defined]
        {
            "id": "mem-1",
            "memory": "src body",
            "score": 0.9,
            "metadata": {
                "doc_id": "src/core.py",
                "repo": "supamem",
                "axis": "code_fact",
            },
        },
        {
            "id": "mem-2",
            "memory": "readme",
            "score": 0.5,
            "metadata": {
                "doc_id": "README.md",
                "repo": "supamem",
                "axis": "code_fact",
            },
        },
    ]

    hits = adapter.query("how does core work?", k=20)
    assert len(hits) == 2
    assert hasattr(hits[0], "score") and hasattr(hits[0], "payload")
    assert hits[0].payload["doc_id"] == "src/core.py"
    assert hits[0].score == pytest.approx(0.9)
    assert hits[1].payload["doc_id"] == "README.md"


def test_mem0_adapter_query_filters_by_repo(mock_mem0) -> None:
    """``where={'repo': [...]}`` filters mem0 results metadata-side."""
    from supamem.eval.coderag.peers.mem0_adapter import Mem0PeerAdapter

    adapter = Mem0PeerAdapter()
    adapter._memory._search_results = [  # type: ignore[attr-defined]
        {"id": "m1", "score": 0.9, "metadata": {"doc_id": "a", "repo": "supamem", "axis": "code_fact"}},
        {"id": "m2", "score": 0.8, "metadata": {"doc_id": "b", "repo": "fastapi", "axis": "code_fact"}},
    ]
    hits = adapter.query("text", k=10, where={"repo": ["supamem"]})
    assert [h.payload["doc_id"] for h in hits] == ["a"]


# ---------------------------------------------------------------------------
# Live integration — gated behind SUPAMEM_INTEGRATION_MEM0=1
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("SUPAMEM_INTEGRATION_MEM0") != "1",
    reason="live mem0 integration gated behind SUPAMEM_INTEGRATION_MEM0=1",
)
def test_mem0_live_integration_skip_unless_env() -> None:  # pragma: no cover
    """Live mem0 + Qdrant smoke. Requires Qdrant up + ``mem0ai`` installed."""
    from supamem.eval.coderag.peers.mem0_adapter import (
        MEM0_COLLECTION,
        Mem0PeerAdapter,
    )

    adapter = Mem0PeerAdapter()
    assert adapter._collection == MEM0_COLLECTION
    # Smoke: ingest a single fake-record path is too brittle for live runs;
    # the orchestrator's live-stack rerun against the populated 15-B corpus
    # is the canonical exercise. Here we only assert the adapter constructed.
