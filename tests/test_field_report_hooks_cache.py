"""Field report 2026-11-06 (cl-harness session 2): the Claude Code edit hook never saw a path, model files
lived in the OS temp dir, MCP searches were never logged and the audit log had two locations.

Each test pins one defect; all of them failed before the fix.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "test_hook"}
    base.update(overrides)
    return ResolvedConfig(**base)


# ---- 1. the edit hook reads the path from the PreToolUse stdin payload ----------------------------------


def test_resolve_file_path_reads_tool_input_from_stdin() -> None:
    from supamem.hooks.claude_code import resolve_file_path

    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "/repo/src/shooting.cs"}})
    assert resolve_file_path(None, payload) == Path("/repo/src/shooting.cs")
    nb = json.dumps({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/repo/src/a.ipynb"}})
    assert resolve_file_path(None, nb) == Path("/repo/src/a.ipynb")


def test_resolve_file_path_prefers_an_explicit_cli_path_and_survives_garbage() -> None:
    from supamem.hooks.claude_code import resolve_file_path

    assert resolve_file_path(Path("/x/src/a.py"), "{}") == Path("/x/src/a.py")
    assert resolve_file_path(None, "not json") is None
    assert resolve_file_path(None, "") is None
    assert resolve_file_path(None, json.dumps({"tool_input": {"file_path": 7}})) is None


def test_dispatch_without_cli_path_uses_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import supamem.hooks.claude_code as hook
    from supamem.hooks import dispatch

    seen: list[Path] = []
    monkeypatch.setattr(hook, "run", lambda file_path, config: seen.append(file_path) or 0)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"file_path": "/r/src/k.ts"}})))
    assert dispatch(client="claude-code", file_path=None, config=_cfg()) == 0
    assert seen == [Path("/r/src/k.ts")]


def test_installer_registers_the_hook_without_the_undocumented_env_var() -> None:
    from supamem.install.claude_code import HOOKS_OVERLAY

    cmd = HOOKS_OVERLAY["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "CLAUDE_FILE_PATH" not in cmd
    assert cmd == "supamem hook claude-code"


def test_installer_rewrites_a_legacy_registration_in_place() -> None:
    from supamem.install.claude_code import _settings_with_hook

    legacy = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [{"type": "command", "command": 'supamem hook claude-code --file-path "$CLAUDE_FILE_PATH"', "timeout": 30}],
                },
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool"}]},
            ]
        }
    }
    out = _settings_with_hook(legacy)
    cmds = [h["command"] for e in out["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert cmds.count("supamem hook claude-code") == 1
    assert not any("CLAUDE_FILE_PATH" in c for c in cmds)
    assert "other-tool" in cmds


# ---- 2. model files live in the persistent supamem cache, never in the OS temp dir ----------------------


def test_fastembed_cache_dir_is_persistent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import platformdirs

    from supamem.embedders.cache import fastembed_cache_dir

    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    monkeypatch.delenv("SUPAMEM_CACHE_DIR", raising=False)
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda name: str(tmp_path / name))
    d = Path(fastembed_cache_dir())
    assert d == tmp_path / "supamem" / "fastembed"
    assert d.is_dir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(tmp_path / "root"))
    assert Path(fastembed_cache_dir()) == tmp_path / "root" / "fastembed"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "override"))
    assert Path(fastembed_cache_dir()) == tmp_path / "override"


def test_every_fastembed_model_gets_the_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import supamem.retrieval.tuned_hybrid as th

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    calls: list[dict[str, Any]] = []

    def fake(name: str, **kw: Any) -> object:
        calls.append({"name": name, **kw})
        return object()

    monkeypatch.setattr(th, "TextEmbedding", fake)
    monkeypatch.setattr(th, "SparseTextEmbedding", fake)
    monkeypatch.setattr(th, "_SPARSE_IMPORT_ERROR", None, raising=False)
    import qdrant_client

    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **kw: MagicMock())
    backend = th.TunedHybridBackend(config=_cfg())
    backend._ensure()
    assert len(calls) == 2
    assert all(c.get("cache_dir") == str(tmp_path) for c in calls)

    import fastembed

    from supamem.embedders.bm25 import BM25Embedder
    from supamem.embedders.minilm import MiniLMEmbedder

    calls.clear()
    monkeypatch.setattr(fastembed, "TextEmbedding", fake)
    monkeypatch.setattr(fastembed, "SparseTextEmbedding", fake)
    BM25Embedder()._ensure()
    MiniLMEmbedder()._ensure()
    assert len(calls) == 2
    assert all(c.get("cache_dir") == str(tmp_path) for c in calls)


def test_offline_env_makes_every_model_cache_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from supamem.embedders.cache import fastembed_kwargs

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert fastembed_kwargs() == {"cache_dir": str(tmp_path), "local_files_only": False}
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert fastembed_kwargs() == {"cache_dir": str(tmp_path), "local_files_only": True}


# ---- 3. the edit hook never downloads models on the edit path ---------------------------------------------


def test_edit_hook_runs_hf_offline(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import os

    import supamem.hooks.claude_code as hook

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen: dict[str, str | None] = {}

    def backend(cfg: Any) -> Any:
        seen["offline"] = os.environ.get("HF_HUB_OFFLINE")
        b = MagicMock()
        b.query.return_value = []
        return b

    monkeypatch.setattr(hook, "_get_backend", backend)
    monkeypatch.setattr(hook, "_touch_marker", lambda *a, **k: None)
    assert hook.run(Path("/repo/src/shooting.cs"), _cfg()) == 0
    assert seen["offline"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ  # scoped to the query, not left behind
    capsys.readouterr()


# ---- 4. MCP searches are logged with the counter's real signature -------------------------------------------


def test_mcp_search_is_logged_as_search_mcp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import asyncio

    import supamem.mcp_server as mod
    import supamem.stats.counter as counter
    from supamem.retrieval.types import RetrievedChunk

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(counter, "bump", lambda *a, **k: calls.append((a, k)))
    fake = MagicMock()
    fake.query.return_value = [RetrievedChunk(id="1", text="alpha body", score=0.9, source_path="a.md")]
    monkeypatch.setattr(mod, "_get_backend", lambda cfg: fake)
    asyncio.run(mod.dual_memory_search(query="hi", top_k=1, config=_cfg()))
    assert len(calls) == 1
    args, kwargs = calls[0]
    bound = dict(zip(("kind", "source", "tokens", "latency_ms"), args)) | kwargs
    assert bound["kind"] == "search" and bound["source"] == "mcp"
    assert bound["tokens"] > 0 and bound["latency_ms"] >= 0

    calls.clear()
    fake.query.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        asyncio.run(mod.dual_memory_search(query="hi", top_k=1, config=_cfg()))
    args, kwargs = calls[0]
    bound = dict(zip(("kind", "source", "tokens", "latency_ms"), args)) | kwargs
    assert bound["kind"] == "search_error" and bound["source"] == "mcp"


# ---- 5. one audit log: the writer and every reader agree on platformdirs ------------------------------------


def test_counter_writes_where_the_readers_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SUPAMEM_AUDIT_DIR", raising=False)  # exercise the real resolution (platformdirs patched)
    monkeypatch.delenv("SUPAMEM_CACHE_DIR", raising=False)
    import platformdirs

    import supamem.stats.counter as counter
    from supamem.hooks.session_start import _probe_audit_path as ss_audit
    from supamem.live import _resolve_audit_path as live_audit

    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda name: str(tmp_path / "plat" / name))
    monkeypatch.setattr(counter, "_LEGACY_CACHE_DIR", tmp_path / "legacy")
    counter.bump("search", "mcp", tokens=3, latency_ms=1.0)
    written = tmp_path / "plat" / "supamem" / "audit.jsonl"
    assert written.exists()
    assert Path(ss_audit()) == written
    assert Path(live_audit(None)) == written


def test_counter_migrates_the_legacy_audit_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SUPAMEM_AUDIT_DIR", raising=False)  # exercise the real resolution (platformdirs patched)
    monkeypatch.delenv("SUPAMEM_CACHE_DIR", raising=False)
    import platformdirs

    import supamem.stats.counter as counter

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "audit.jsonl").write_text('{"ts":"2026-11-01T00:00:00+00:00","kind":"rerank","source":"x","tokens":1,"latency_ms":1.0}\n')
    (legacy / "aggregates.json").write_text("{}")
    (legacy / "manifest.json").write_text("{}")  # the indexer's file stays where the indexer reads it
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda name: str(tmp_path / "plat" / name))
    monkeypatch.setattr(counter, "_LEGACY_CACHE_DIR", legacy)
    counter.bump("search", "mcp", tokens=3, latency_ms=1.0)
    new = tmp_path / "plat" / "supamem"
    lines = (new / "audit.jsonl").read_text().strip().splitlines()
    assert [json.loads(x)["kind"] for x in lines] == ["rerank", "search"]
    assert not (legacy / "audit.jsonl").exists()
    assert (legacy / "manifest.json").exists()


def test_install_and_repair_prefetch_the_embedders_online(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fastembed

    from supamem.install import _maybe_prepare_models

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")  # even if the shell is offline-flagged, repair is the fetch point
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(fastembed, "TextEmbedding", lambda name, **kw: calls.append({"name": name, **kw}))
    monkeypatch.setattr(fastembed, "SparseTextEmbedding", lambda name, **kw: calls.append({"name": name, **kw}))
    import supamem.rerankers as rr

    monkeypatch.setattr(rr, "prepare", lambda *a, **k: None)
    _maybe_prepare_models(skip_models=False)
    assert len(calls) == 2
    assert all(c["cache_dir"] == str(tmp_path) and c["local_files_only"] is False for c in calls)


def test_audit_dir_override_never_adopts_the_legacy_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import supamem.stats.counter as counter

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "audit.jsonl").write_text("{}\n")
    monkeypatch.setattr(counter, "_LEGACY_CACHE_DIR", legacy)
    monkeypatch.setenv("SUPAMEM_AUDIT_DIR", str(tmp_path / "explicit"))
    counter.bump("search", "mcp", tokens=1, latency_ms=1.0)
    assert (legacy / "audit.jsonl").exists()
    assert len((tmp_path / "explicit" / "audit.jsonl").read_text().splitlines()) == 1
