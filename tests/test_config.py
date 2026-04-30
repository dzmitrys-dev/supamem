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
