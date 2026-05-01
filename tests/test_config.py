"""Config-layer tests for Phase 5 MCP response caps (Wave 1).

These tests cover the three new ``ResolvedConfig.mcp_caps_*`` fields and the
two-level ``[supamem.mcp.caps]`` TOML table dispatch added to
``_NESTED_TABLES`` / ``_apply_nested`` in ``src/supamem/config.py``.

Cross-references:
- D-09 / D-10 in ``.planning/phases/05-mcp-response-caps/05-CONTEXT.md``
- Open Q1 (Option B) in ``.planning/phases/05-mcp-response-caps/05-RESEARCH.md``
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem.config import ResolvedConfig, load_config


def test_mcp_caps_defaults() -> None:
    """``ResolvedConfig`` exposes the three caps at documented defaults (D-09)."""
    cfg = ResolvedConfig()
    assert cfg.mcp_caps_max_top_k == 25
    assert cfg.mcp_caps_max_query_chars == 250
    assert cfg.mcp_caps_max_preview_chars == 200


def test_mcp_caps_toml_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``[supamem.mcp.caps]`` table values flow into all three flat fields and
    ``ConfigChain`` records ``supamem_toml`` source attribution."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.mcp.caps]\n"
        "max_top_k = 10\n"
        "max_query_chars = 500\n"
        "max_preview_chars = 80\n",
        encoding="utf-8",
    )
    cfg, chain = load_config(tmp_path)
    assert cfg.mcp_caps_max_top_k == 10
    assert cfg.mcp_caps_max_query_chars == 500
    assert cfg.mcp_caps_max_preview_chars == 80
    assert chain.mcp_caps_max_top_k == "supamem_toml"
    assert chain.mcp_caps_max_query_chars == "supamem_toml"
    assert chain.mcp_caps_max_preview_chars == "supamem_toml"


def test_mcp_caps_toml_partial_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial override: only ``max_top_k`` set; other two stay at defaults
    with ``default`` source. Mixed-source row in the chain is the regression
    guard for the dotted-path drill not over-writing missing fields."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.mcp.caps]\nmax_top_k = 7\n",
        encoding="utf-8",
    )
    cfg, chain = load_config(tmp_path)
    assert cfg.mcp_caps_max_top_k == 7
    assert cfg.mcp_caps_max_query_chars == 250
    assert cfg.mcp_caps_max_preview_chars == 200
    assert chain.mcp_caps_max_top_k == "supamem_toml"
    assert chain.mcp_caps_max_query_chars == "default"
    assert chain.mcp_caps_max_preview_chars == "default"


def test_transcript_defaults() -> None:
    """``ResolvedConfig`` exposes the six transcript fields at documented defaults (D-30)."""
    cfg = ResolvedConfig()
    assert cfg.transcript_default_root == "~/.claude/projects/"
    assert cfg.transcript_since_days == 180
    assert cfg.transcript_tool_payload_max_chars == 2000
    assert cfg.transcript_chunk_soft_max_tokens == 600
    assert cfg.transcript_include_paths_glob == []
    assert cfg.transcript_exclude_paths_glob == []


def test_transcript_toml_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[supamem.transcript]`` table values flow into all six flat fields and
    ``ConfigChain`` records ``supamem_toml`` source attribution (D-30, D-32)."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.transcript]\n"
        'default_root = "/custom/sessions/"\n'
        "since_days = 30\n"
        "tool_payload_max_chars = 4000\n"
        "chunk_soft_max_tokens = 800\n"
        'include_paths_glob = ["**/projects/**"]\n'
        'exclude_paths_glob = ["**/secret/**"]\n',
        encoding="utf-8",
    )
    cfg, chain = load_config(tmp_path)
    assert cfg.transcript_default_root == "/custom/sessions/"
    assert cfg.transcript_since_days == 30
    assert cfg.transcript_tool_payload_max_chars == 4000
    assert cfg.transcript_chunk_soft_max_tokens == 800
    assert cfg.transcript_include_paths_glob == ["**/projects/**"]
    assert cfg.transcript_exclude_paths_glob == ["**/secret/**"]
    assert chain.transcript_default_root == "supamem_toml"
    assert chain.transcript_since_days == "supamem_toml"
    assert chain.transcript_tool_payload_max_chars == "supamem_toml"
    assert chain.transcript_chunk_soft_max_tokens == "supamem_toml"
    assert chain.transcript_include_paths_glob == "supamem_toml"
    assert chain.transcript_exclude_paths_glob == "supamem_toml"


def test_existing_eval_table_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-level ``[supamem.eval]`` table keeps working — regression guard
    for the ``_NESTED_TABLES`` two-level shape extension."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.eval]\n"
        'goldens_path = "custom/goldens.jsonl"\n'
        "baseline_recall_at_5 = 0.75\n",
        encoding="utf-8",
    )
    cfg, chain = load_config(tmp_path)
    assert cfg.goldens_path == "custom/goldens.jsonl"
    assert cfg.regress_baseline_recall_at_5 == 0.75
    assert chain.goldens_path == "supamem_toml"
    assert chain.regress_baseline_recall_at_5 == "supamem_toml"
    # And caps remain at defaults when no [supamem.mcp.caps] block exists.
    assert cfg.mcp_caps_max_top_k == 25
    assert chain.mcp_caps_max_top_k == "default"


def test_classifier_rooms_default_order() -> None:
    """Phase 7 D-14 / D-15 — defaults ship in D-01a priority order."""
    cfg = ResolvedConfig()
    keys = list(cfg.classifier_rooms.keys())
    assert keys == [
        "tests",
        "types",
        "migrations",
        "config",
        "scripts",
        "docs",
        "frontend",
        "backend",
    ]
    assert cfg.classifier_rooms["tests"] == [
        "tests",
        "test",
        "__tests__",
        "spec",
        "specs",
    ]
    assert cfg.classifier_rooms["backend"] == [
        "src",
        "backend",
        "api",
        "server",
        "lib",
    ]


def test_classifier_rooms_loads_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[supamem.classifier.rooms]`` table flows into ``classifier_rooms``,
    preserving user TOML key order (Pitfall 4 — first-match-wins requires
    insertion-order preservation through the merge path)."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "[supamem.classifier.rooms]\n"
        'backend = ["src"]\n'
        'tests = ["tests"]\n',
        encoding="utf-8",
    )
    cfg, chain = load_config(tmp_path)
    assert list(cfg.classifier_rooms.keys()) == ["backend", "tests"]
    assert cfg.classifier_rooms == {"backend": ["src"], "tests": ["tests"]}
    assert chain.classifier_rooms == "supamem_toml"


def test_classifier_rooms_provenance_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``[supamem.classifier.rooms]`` block → defaults + ``default`` source."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg, chain = load_config(tmp_path)
    assert chain.classifier_rooms == "default"
    assert list(cfg.classifier_rooms.keys())[0] == "tests"
    assert list(cfg.classifier_rooms.keys())[-1] == "backend"
