"""Doc-drift guards.

Three contracts:

1. ``llms.txt`` mentions every public env var, config key, and MCP-surface
   change (AGENTS.md mandate). Phase 5 added the ``[supamem.mcp.caps]``
   keys; Phase 8 adds the ``supamem.reranker`` entry-point group, the
   ``--skip-models`` flag, and the ``SUPAMEM_CACHE_DIR`` env var;
   Phase 19 adds the MCP SDK 2.x (``MCPServer``) note and the
   ``[supamem.mcp]`` ``response_format`` / ``cache_ttl_ms`` config keys;
   Phase 19.1 adds the ``SUPAMEM_CONFIG`` MCP-entry emission, the
   ``--force-cursor-rules`` flag, the flat ``regress_baseline_*``
   aliases, and the ``--dry-run`` contract.

2. ``README.md`` + the 4 translations stay in lockstep — the
   ``synced-with: README.md @ <sha>`` marker on line 2 of each
   translation MUST exist, and the language-switcher line on line 1
   MUST link to all 5 README files (each translation localizes the
   leading word but keeps the link list intact).

3. Every documented ``install supamem`` command carries an explicit
   version pin (field report SM-5). PyPI's newest *stable* release
   (0.2.0) predates the whole 0.3.x/0.4.x pre-release line, so an
   unpinned install resolves BACKWARDS to a version older than the
   features the docs describe. The guard pins both halves: the pinned
   string must be present in all 6 files, and no unpinned install of
   the bare ``supamem`` name may survive anywhere in them.
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

# Every doc that teaches an install command (SM-5 guard surface).
INSTALL_DOC_FILES = ["README.md", *TRANSLATIONS, "llms.txt"]

# Minimum pinned-command occurrences per file. Each README teaches the
# install twice (60-second quickstart + the Install section); llms.txt
# carries the Distribution bullet.
MIN_PINS = {name: 2 for name in ["README.md", *TRANSLATIONS]} | {"llms.txt": 1}

# An install command naming the bare `supamem` distribution with NO
# version pin. The negative lookahead lets the pinned form
# (`supamem==0.4.0a2`) and the extras forms (`supamem[eval]`,
# `supamem[peers-mem0]`, `supamem[ast-chunker]`) through, while catching
# `pip install supamem`, `pipx install supamem`, and
# `uv tool install supamem` anywhere in the file — fenced block or prose.
UNPINNED_INSTALL_RE = re.compile(
    r"(?:uv tool install|pipx install|pip install)\s+'?supamem'?(?![=\[\w.\-])"
)

PYPROJECT_VERSION_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)


def _shipped_version() -> str:
    """The version ``pyproject.toml`` actually ships.

    The docs pin is asserted against THIS, not a hardcoded literal, so a
    version bump that forgets the docs fails the guard (Pitfall 5: a pin
    naming a version that was never published is worse than no pin).
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = PYPROJECT_VERSION_RE.search(text)
    assert match is not None, (
        "pyproject.toml has no top-level `version = \"...\"` line — the "
        "install-pin guard cannot determine the shipped version"
    )
    return match.group(1)


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


def test_llms_txt_mentions_phase19_1_surface() -> None:
    """Phase 19.1 — field-report fix surface MUST be advertised."""
    content = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")

    for needle in (
        "SUPAMEM_CONFIG",
        "--force-cursor-rules",
        "--dry-run",
        "regress_baseline_recall_at_5",
        "sweep_managed_blocks",
        "<project>/.claude/agents/",
    ):
        assert needle in content, (
            f"llms.txt must reference '{needle}' (Phase 19.1 public surface "
            f"— AGENTS.md llms.txt mandate)"
        )


def test_documented_install_commands_are_pinned() -> None:
    """SM-5 — every doc teaches a pinned install of the SHIPPED version."""
    pin = f"supamem=={_shipped_version()}"

    misses: list[str] = []
    for name in INSTALL_DOC_FILES:
        found = (REPO_ROOT / name).read_text(encoding="utf-8").count(pin)
        if found < MIN_PINS[name]:
            misses.append(f"{name}: {found} occurrence(s) of {pin!r}, need >= {MIN_PINS[name]}")
    assert not misses, (
        "documented install commands are not pinned to the version "
        "pyproject.toml ships (field report SM-5 — an unpinned install "
        "resolves backwards to stable 0.2.0):\n  " + "\n  ".join(misses)
    )


def test_no_unpinned_install_command_survives() -> None:
    """SM-5 — a bare `install supamem` must not survive in any doc.

    Extras (``supamem[eval]``) and pinned forms are allowed; only the
    unpinned bare distribution name is a finding.
    """
    offenders: list[str] = []
    for name in INSTALL_DOC_FILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            match = UNPINNED_INSTALL_RE.search(line)
            if match is not None:
                offenders.append(f"{name}:{lineno}: {match.group(0)!r}")
    assert not offenders, (
        "unpinned install command(s) found — these resolve BACKWARDS to "
        "PyPI stable 0.2.0, older than the documented features (SM-5):\n  "
        + "\n  ".join(offenders)
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
