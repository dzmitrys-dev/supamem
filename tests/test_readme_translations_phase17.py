"""Phase 17 Plan I — README + 4 translations doc-shape tests for v0.3.0a7.

Per AGENTS.md "README Translations": all 5 README files must stay in
lockstep. Code blocks, badges, file paths, and CLI commands stay in
canonical English; only prose/headings/explanatory text get translated.

Phase 17 surface to assert on the English canonical README:
  - `tree_sitter_code` chunker plugin (opt-in via `[ast-chunker]` extra)
  - `tuned_hybrid_hyde` retrieval plugin (opt-in; Ollama-backed)
  - `ast-chunker` optional extra
  - ADR-0002 §9 deep-link

The synced-with SHA test is a soft check — it skips gracefully when the
working tree has uncommitted README.md changes (the test runs AFTER the
sync-bump commit lands; pre-bump it would FAIL, which is the trigger for
the sync-bump step).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

README_EN = REPO_ROOT / "README.md"
TRANSLATIONS = [
    REPO_ROOT / "README.zh-CN.md",
    REPO_ROOT / "README.es.md",
    REPO_ROOT / "README.ja.md",
    REPO_ROOT / "README.ru.md",
]

ALL_READMES = [README_EN, *TRANSLATIONS]


@pytest.fixture(scope="module")
def readme_en() -> str:
    return README_EN.read_text(encoding="utf-8")


def test_readme_mentions_ast_chunker_plugin(readme_en: str) -> None:
    """Phase 17 Req-02: README references the tree_sitter_code chunker plugin."""
    assert "tree_sitter_code" in readme_en, (
        "README.md must mention the `tree_sitter_code` chunker plugin "
        "(opt-in AST chunker shipped in v0.3.0a7)"
    )


def test_readme_mentions_hyde_retrieval_plugin(readme_en: str) -> None:
    """Phase 17 Req-03: README references the tuned_hybrid_hyde retrieval plugin."""
    assert "tuned_hybrid_hyde" in readme_en, (
        "README.md must mention the `tuned_hybrid_hyde` retrieval plugin "
        "(opt-in HyDE-style query rewriter shipped in v0.3.0a7)"
    )


def test_readme_mentions_ast_chunker_extra(readme_en: str) -> None:
    """Phase 17 Req-02: README references the [ast-chunker] optional extra."""
    assert "ast-chunker" in readme_en, (
        "README.md must mention the `ast-chunker` optional extra "
        "(`pip install supamem[ast-chunker]`)"
    )


def test_readme_links_adr_section_9(readme_en: str) -> None:
    """README must deep-link the new ADR-0002 §9 Phase 17 uplift comparison."""
    has_link = (
        "0002-coderag-eval-philosophy.md#9-phase-17-uplift-comparison" in readme_en
        or "ADR-0002 §9" in readme_en
    )
    assert has_link, (
        "README.md must deep-link ADR-0002 §9 "
        "(Phase 17 uplift comparison; new in v0.3.0a7)"
    )


def test_readme_mentions_defaults_unchanged_in_03x(readme_en: str) -> None:
    """README must disclose the opt-in-only / defaults-unchanged verdict.

    Phase 17 D-LAT-01: HyDE violates the 5000 ms p95 hard ceiling on 4/5
    cells against the live corpus, so the plugins ship opt-in only and
    defaults are unchanged in the 0.3.x line. Users need to see this
    BEFORE deciding to flip the config.
    """
    body_lower = readme_en.lower()
    has_disclosure = (
        "defaults unchanged" in body_lower
        or "defaults are unchanged" in body_lower
        or "opt-in only" in body_lower
        or "opt-in-only" in body_lower
    )
    assert has_disclosure, (
        "README.md must disclose that Phase 17 plugins are opt-in only and "
        "defaults are unchanged in the 0.3.x line (D-LAT-01 verdict)"
    )


_LANG_SWITCHER_LINKS = (
    "[English](README.md)",
    "[简体中文](README.zh-CN.md)",
    "[Español](README.es.md)",
    "[日本語](README.ja.md)",
    "[Русский](README.ru.md)",
)


def test_translations_share_line_1_language_switcher_links() -> None:
    """All 5 READMEs' line 1 must contain the same 5 language-switcher links.

    Per AGENTS.md README Translations: the leading `**Languages:**` /
    `**语言:**` / `**Idiomas:**` / `**言語:**` / `**Языки:**` label is
    intentionally translated; the link list itself stays in lockstep.
    """
    for readme in ALL_READMES:
        first_line = readme.read_text(encoding="utf-8").splitlines()[0]
        for link in _LANG_SWITCHER_LINKS:
            assert link in first_line, (
                f"{readme.name} line 1 missing language-switcher link {link!r}; "
                f"got {first_line!r}"
            )


_SYNCED_WITH_RE = re.compile(r"^<!--\s*synced-with:\s*README\.md\s*@\s*[a-f0-9]{6,}\s*-->$")


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_have_synced_with_marker_in_header(translation: Path) -> None:
    """Synced-with marker must appear in the header (first 5 lines).

    Existing repo convention places it on line 3 (after a blank
    separator) — accept lines 2 OR 3.
    """
    head_lines = translation.read_text(encoding="utf-8").splitlines()[:5]
    matched = any(_SYNCED_WITH_RE.match(line) for line in head_lines)
    assert matched, (
        f"{translation.name} header (first 5 lines) must contain a "
        f"`<!-- synced-with: README.md @ <sha> -->` marker; got "
        f"{head_lines!r}"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_carry_tree_sitter_code_literal(translation: Path) -> None:
    """Per AGENTS.md: code blocks/CLI identifiers stay canonical English."""
    body = translation.read_text(encoding="utf-8")
    assert "tree_sitter_code" in body, (
        f"{translation.name} missing canonical plugin name `tree_sitter_code`"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_carry_tuned_hybrid_hyde_literal(translation: Path) -> None:
    """Per AGENTS.md: code blocks/CLI identifiers stay canonical English."""
    body = translation.read_text(encoding="utf-8")
    assert "tuned_hybrid_hyde" in body, (
        f"{translation.name} missing canonical plugin name `tuned_hybrid_hyde`"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_carry_ast_chunker_extra_literal(translation: Path) -> None:
    """Per AGENTS.md: `pip install supamem[ast-chunker]` stays canonical English."""
    body = translation.read_text(encoding="utf-8")
    assert "ast-chunker" in body, (
        f"{translation.name} missing canonical optional-extra literal `ast-chunker`"
    )


def test_translations_share_identical_synced_with_sha() -> None:
    """All 4 translations must carry an IDENTICAL synced-with SHA.

    Verified by `grep -h ... | sort -u | wc -l == 1` per the plan
    acceptance criterion.
    """
    seen: set[str] = set()
    for translation in TRANSLATIONS:
        head_lines = translation.read_text(encoding="utf-8").splitlines()[:5]
        for line in head_lines:
            if _SYNCED_WITH_RE.match(line):
                seen.add(line.strip())
                break
        else:
            pytest.fail(f"{translation.name} has no synced-with marker in first 5 lines")
    assert len(seen) == 1, (
        f"All 4 translations must share an IDENTICAL synced-with marker line; "
        f"saw {len(seen)} distinct values: {seen!r}"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_synced_with_sha_matches_last_readme_commit(translation: Path) -> None:
    """The `synced-with` SHA on each translation must match the SHA of the
    most recent commit that touched README.md.

    Per AGENTS.md README Translations sync flow: the README.md content
    commit lands first; the sync-bump runs the sed one-liner in a
    SEPARATE follow-up commit. At Task 1 commit time this test FAILS
    (translations still pin the v0.3.0a6 SHA `7c5b4ad`); Task 2 closes
    the gate by bumping the SHA to point at Task 2 step 1's commit.

    Soft skip when (a) git is unavailable, (b) README.md has
    uncommitted changes (pre-sync-bump state).
    """
    try:
        diff_status = subprocess.check_output(
            ["git", "status", "--porcelain", "README.md"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if diff_status:
            pytest.skip(
                f"README.md has uncommitted changes ({diff_status!r}); "
                "sync-bump runs in a separate post-commit step per AGENTS.md."
            )
        readme_sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%h", "--abbrev=7", "--", "README.md"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available — cannot resolve last README.md commit SHA")

    if not readme_sha:
        pytest.skip("README.md has no commit history in this checkout")

    body = translation.read_text(encoding="utf-8")
    expected = f"synced-with: README.md @ {readme_sha}"
    assert expected in body, (
        f"{translation.name} synced-with SHA must match the last commit "
        f"that touched README.md ({readme_sha}). "
        "Run the AGENTS.md sed one-liner AFTER the README content commit."
    )
