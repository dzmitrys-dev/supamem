"""Doc-drift guards.

Two contracts:

1. ``llms.txt`` mentions every public env var, config key, and MCP-surface
   change (AGENTS.md mandate). Phase 5 added the ``[supamem.mcp.caps]``
   keys; Phase 8 adds the ``supamem.reranker`` entry-point group, the
   ``--skip-models`` flag, and the ``SUPAMEM_CACHE_DIR`` env var;
   Phase 19 adds the MCP SDK 2.x (``MCPServer``) note and the
   ``[supamem.mcp]`` ``response_format`` / ``cache_ttl_ms`` config keys.

2. ``README.md`` + the 4 translations stay in lockstep — the
   ``synced-with: README.md @ <sha>`` marker on line 2 of each
   translation MUST exist, and the language-switcher line on line 1
   MUST link to all 5 README files (each translation localizes the
   leading word but keeps the link list intact).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TRANSLATIONS = [
    "README.zh-CN.md",
    "README.es.md",
    "README.ja.md",
    "README.ru.md",
]

SYNCED_RE = re.compile(r"<!-- synced-with: README\.md @ ([a-f0-9]{6,}) -->")

# Every README's first line MUST link to all five files (one English
# canonical + four translations). The leading "Languages:" / "语言:" /
# "Idiomas:" / etc word is localized — we assert the link tail is
# byte-identical across all five.
LANG_SWITCHER_TAIL = (
    "[English](README.md) · [简体中文](README.zh-CN.md) · "
    "[Español](README.es.md) · [日本語](README.ja.md) · "
    "[Русский](README.ru.md)"
)


def test_llms_txt_mentions_caps() -> None:
    llms_path = REPO_ROOT / "llms.txt"
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


def test_llms_txt_mentions_phase8_surface() -> None:
    """Phase 8 — code-aware reranker public surface MUST be advertised."""
    content = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")

    for needle in (
        "supamem.reranker",
        "--skip-models",
        "SUPAMEM_CACHE_DIR",
        "[retrieval.reranker]",
        "mxbai-rerank-base-v2",
    ):
        assert needle in content, (
            f"llms.txt must reference '{needle}' (Phase 8 public surface — "
            f"AGENTS.md llms.txt mandate)"
        )


def test_llms_txt_mentions_phase19_surface() -> None:
    """Phase 19 — MCP SDK 2.x + response-format/cache public surface MUST
    be advertised."""
    content = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")

    for needle in (
        "response_format",
        "cache_ttl_ms",
        "MCPServer",
    ):
        assert needle in content, (
            f"llms.txt must reference '{needle}' (Phase 19 public surface — "
            f"AGENTS.md llms.txt mandate)"
        )


def test_language_switcher_tail_consistent() -> None:
    """All 5 READMEs share the identical link list on line 1."""
    misses: list[str] = []
    for name in ["README.md", *TRANSLATIONS]:
        first = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()[0]
        if LANG_SWITCHER_TAIL not in first:
            misses.append(f"{name} line-1: {first!r}")
    assert not misses, (
        "language-switcher link-list drift on first line of one or more "
        "READMEs:\n  " + "\n  ".join(misses)
    )


def test_dual_memory_rule_uses_real_mcp_tool_names() -> None:
    """D-LOCK-07 — dual-memory.md must reference real MCP tool names, not the
    phantom `qdrant-find` CLI that never existed."""
    rule = (
        REPO_ROOT / "src" / "supamem" / "share" / "rules" / "dual-memory.md"
    ).read_text(encoding="utf-8")
    assert "mcp__supamem__qdrant_find" in rule
    assert "mcp__supamem__dual_memory_search" in rule
    # The phantom CLI must be gone — but allow the explanatory call-out
    # ("a `qdrant-find` shell command, that CLI never existed") which mentions
    # it precisely to disclaim it. Test for the absence of CALL-SITE usage:
    bad_patterns = [
        'qdrant-find "',     # quoted command form
        "$ qdrant-find ",     # shell prompt form
        "`qdrant-find` ",     # back-tick wrapped command form (with trailing space)
    ]
    for pat in bad_patterns:
        assert pat not in rule, (
            f"call-site qdrant-find pattern still present: {pat!r}"
        )
    # Patcher disclosure
    assert "unpatch-agents" in rule
    assert "--skip-patch-agents" in rule


def test_translations_have_synced_marker() -> None:
    """Each translation carries a synced-with SHA on line 2."""
    misses: list[str] = []
    for name in TRANSLATIONS:
        lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
        line2 = lines[2] if len(lines) > 2 else ""
        if not SYNCED_RE.search(line2):
            misses.append(f"{name} line-3: {line2!r}")
    assert not misses, (
        "synced-with marker missing on line 3 of one or more translations:\n  "
        + "\n  ".join(misses)
    )
