"""Phase 15 Plan E Task E2 — CHANGELOG v0.3.0a5 doc-shape tests.

Enforces required content of the new CHANGELOG entry without coupling
to prose. The release manager may swap ``0.3.0a5`` for ``0.3.0`` at
commit time; the regex accepts either form.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# Match `## [0.3.0a5] — ...` OR `## v0.3.0a5 — ...` OR `## [0.3.0] — ...`
# (release-manager flexibility per 15-E-PLAN).
_VERSION_HEADER_RE = re.compile(
    r"^## (?:\[)?v?0\.3\.0(?:a5)?(?:\])?\b",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def changelog() -> str:
    return CHANGELOG_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def v05_section(changelog: str) -> str:
    """Return the v0.3.0a5 (or v0.3.0) entry body up to the next ## heading."""
    match = _VERSION_HEADER_RE.search(changelog)
    assert match is not None, "CHANGELOG missing v0.3.0a5 / v0.3.0 entry header"
    start = match.start()
    # Find next `## ` after start.
    next_match = re.search(r"^## ", changelog[start + 1:], re.MULTILINE)
    end = (start + 1 + next_match.start()) if next_match else len(changelog)
    return changelog[start:end]


def test_changelog_has_v0_3_0a5_entry(changelog: str) -> None:
    assert _VERSION_HEADER_RE.search(changelog) is not None, (
        "CHANGELOG.md must have a `## [0.3.0a5]` (or `## [0.3.0]`) header"
    )


def test_changelog_v0_3_0a5_mentions_coderag(v05_section: str) -> None:
    assert "coderag" in v05_section


def test_changelog_v0_3_0a5_mentions_three_column(v05_section: str) -> None:
    assert "three-column" in v05_section or (
        "supamem_only" in v05_section and "fastapi_only" in v05_section
    )


def test_changelog_v0_3_0a5_mentions_mem0(v05_section: str) -> None:
    assert "mem0" in v05_section


def test_changelog_v0_3_0a5_mentions_longmemeval_demotion(v05_section: str) -> None:
    assert "LongMemEval" in v05_section
    assert ("demoted" in v05_section.lower()) or ("on-demand" in v05_section)


def test_changelog_v0_3_0a5_links_adr_0002(v05_section: str) -> None:
    assert "ADR-0002" in v05_section or "0002-coderag-eval-philosophy" in v05_section


def test_changelog_v0_3_0a5_appears_above_v0_3_0a4(changelog: str) -> None:
    """Most-recent at top per the existing CHANGELOG convention."""
    a5_match = _VERSION_HEADER_RE.search(changelog)
    a4_idx = changelog.find("## [0.3.0a4]")
    assert a5_match is not None
    assert a4_idx > 0, "CHANGELOG missing existing v0.3.0a4 entry — expected for diff order"
    assert a5_match.start() < a4_idx, (
        "v0.3.0a5 entry must appear ABOVE v0.3.0a4 (newest-first)"
    )
