"""Doc-drift guard for ``llms.txt`` (Phase 05 — MCP response caps).

AGENTS.md mandates that ``llms.txt`` mention every public env var, config
key, and MCP-surface change. This test enforces that the three new
``[supamem.mcp.caps]`` keys appear in ``llms.txt`` after Wave 3 lands.

Red phase: fails because Wave 3 is what edits ``llms.txt`` — this guard
locks the contract so the doc edit cannot be skipped.
"""
from __future__ import annotations

from pathlib import Path


def test_llms_txt_mentions_caps() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    llms_path = repo_root / "llms.txt"
    assert llms_path.is_file(), (
        f"llms.txt missing at {llms_path} — AGENTS.md mandates it ships "
        f"with every release"
    )

    content = llms_path.read_text(encoding="utf-8")

    assert "mcp.caps" in content, (
        "llms.txt must reference the new [supamem.mcp.caps] config table "
        "(AGENTS.md llms.txt mandate)"
    )

    for key in ("max_top_k", "max_query_chars", "max_preview_chars"):
        assert key in content, (
            f"llms.txt must mention the '{key}' config key under "
            f"[supamem.mcp.caps] so client agents see the cap surface"
        )
