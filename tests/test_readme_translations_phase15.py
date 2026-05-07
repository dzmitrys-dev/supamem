"""Phase 15 Plan E Task E3 — README + 4 translations doc-shape tests.

Per AGENTS.md "README Translations": all 5 README files must stay in
lockstep. Code blocks, badges, file paths, and CLI commands stay in
canonical English; only prose/headings/explanatory text get translated.

The synced-with SHA test (`test_translations_synced_with_sha_matches_post_readme_commit`)
is a soft check — it skips gracefully when the working tree has
uncommitted README.md changes (the test runs AFTER the sync-bump
commit lands; pre-bump it would FAIL, which is the trigger for the
sync-bump step).
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


def test_readme_has_coderag_benchmarks_section(readme_en: str) -> None:
    assert "coderag" in readme_en
    # Three-column phrasing OR explicit metric-name mention.
    has_three_column = "three-column" in readme_en or (
        "supamem_only" in readme_en and "fastapi_only" in readme_en
    )
    has_metric = any(m in readme_en for m in ("Recall", "MRR", "nDCG"))
    assert has_three_column and has_metric, (
        "README.md must have a coderag Benchmarks section with "
        "three-column reporting + at least one metric name (Recall/MRR/nDCG)"
    )


def test_readme_cli_surface_lists_coderag(readme_en: str) -> None:
    assert "supamem eval --suite coderag" in readme_en, (
        "README.md CLI surface must list `supamem eval --suite coderag`"
    )


def test_readme_where_filter_table_lists_repo_and_axis(readme_en: str) -> None:
    """Heuristic: README.md must mention `repo` and `axis` somewhere
    in the where-filter / payload-field semantics context."""
    # Locate the Where-filter / Filtered retrieval section.
    where_section_idx = readme_en.lower().find("where")
    assert where_section_idx >= 0, "README.md missing where-filter context"
    body = readme_en[where_section_idx:]
    assert "`repo`" in body or "`payload.repo`" in body, (
        "README.md must list `repo` (or `payload.repo`) in where-filter / payload-field context"
    )
    assert "`axis`" in body or "`payload.axis`" in body, (
        "README.md must list `axis` (or `payload.axis`) in where-filter / payload-field context"
    )


def test_readme_links_adr_0002(readme_en: str) -> None:
    assert "ADR-0002" in readme_en or "0002-coderag-eval-philosophy" in readme_en


_LANG_SWITCHER_LINKS = (
    "[English](README.md)",
    "[简体中文](README.zh-CN.md)",
    "[Español](README.es.md)",
    "[日本語](README.ja.md)",
    "[Русский](README.ru.md)",
)


def test_translations_share_line_1_language_switcher_links() -> None:
    """All 5 READMEs' line 1 must contain the same 5 language-switcher links.

    Per AGENTS.md README Translations: "all 5 files share the identical
    `[English](README.md) · [简体中文](README.zh-CN.md) · ...` line as
    their first line". The leading `**Languages:**` / `**语言:**` /
    `**Idiomas:**` / `**言語:**` / `**Языки:**` label is intentionally
    translated; the link list itself stays in lockstep.
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
    """Synced-with marker must appear in the header (first 5 lines) per
    AGENTS.md README Translations contract. Existing repo convention
    places it on line 3 (after a blank separator) — accept lines 2 OR 3.
    """
    head_lines = translation.read_text(encoding="utf-8").splitlines()[:5]
    matched = any(_SYNCED_WITH_RE.match(line) for line in head_lines)
    assert matched, (
        f"{translation.name} header (first 5 lines) must contain a "
        f"`<!-- synced-with: README.md @ <sha> -->` marker; got "
        f"{head_lines!r}"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_carry_coderag_term(translation: Path) -> None:
    """The literal term `coderag` stays canonical English in all translations."""
    body = translation.read_text(encoding="utf-8")
    assert "coderag" in body, f"{translation.name} missing canonical term `coderag`"


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_carry_supamem_eval_coderag_cli_literal(translation: Path) -> None:
    """Per AGENTS.md: code blocks/CLI commands stay canonical English."""
    body = translation.read_text(encoding="utf-8")
    assert "supamem eval --suite coderag" in body, (
        f"{translation.name} missing canonical CLI literal `supamem eval --suite coderag`"
    )


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_synced_with_sha_matches_post_readme_commit(translation: Path) -> None:
    """The `synced-with` SHA on each translation must match `git rev-parse --short HEAD`.

    Soft skip when (a) git is unavailable, or (b) README.md has
    uncommitted changes (pre-sync-bump state — expected to FAIL).
    """
    try:
        head_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available — cannot resolve HEAD short SHA")

    # Skip if README.md has uncommitted modifications (pre-sync-bump state).
    try:
        diff_status = subprocess.check_output(
            ["git", "status", "--porcelain", "README.md"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except subprocess.CalledProcessError:
        diff_status = ""
    if diff_status:
        pytest.skip(
            f"README.md has uncommitted changes ({diff_status!r}); sync-bump runs "
            "in a separate post-commit step per AGENTS.md."
        )

    body = translation.read_text(encoding="utf-8")
    expected = f"synced-with: README.md @ {head_sha}"
    assert expected in body, (
        f"{translation.name} synced-with SHA must match HEAD ({head_sha}). "
        "Run the AGENTS.md sed one-liner to sync."
    )
