"""Phase 18 Plan E — ADR-0003 doc-shape tests.

Locks ``docs/adr/0003-simplemem-evaluation.md`` structure and locked concept
verdicts without coupling to full prose.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ADR_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "adr"
    / "0003-simplemem-evaluation.md"
)


@pytest.fixture(scope="module")
def adr_text() -> str:
    return ADR_PATH.read_text(encoding="utf-8")


def test_adr_0003_exists() -> None:
    assert ADR_PATH.is_file(), f"ADR-0003 missing at {ADR_PATH}"


def test_adr_0003_has_madr_frontmatter(adr_text: str) -> None:
    assert adr_text.startswith("---\n"), "ADR-0003 must open with MADR `---` frontmatter"
    fm_end = adr_text.find("\n---", 4)
    assert fm_end > 0, "ADR-0003 frontmatter missing closing `---`"
    fm = adr_text[4:fm_end]
    for key in ("status:", "date:", "deciders:"):
        assert key in fm, f"ADR-0003 frontmatter missing `{key}`"


def test_adr_0003_has_required_sections(adr_text: str) -> None:
    for section in ("## Context", "## Decision", "## Consequences", "## Alternatives Considered"):
        assert section in adr_text, f"ADR-0003 missing `{section}` section"


def test_adr_0003_cites_simplemem_and_eval_context(adr_text: str) -> None:
    assert "SimpleMem" in adr_text
    assert "LoCoMo" in adr_text
    assert "CodeRAG" in adr_text
    assert ("local-only" in adr_text) or ("local only" in adr_text)


def test_adr_0003_documents_concept_names(adr_text: str) -> None:
    lowered = adr_text.lower()
    for token in ("adaptive", "dedup", "evolvemem", "symbolic", "compression", "synthesis"):
        assert token in lowered, f"ADR-0003 must mention concept `{token}`"


def test_adr_0003_has_borrow_and_reject_verdicts(adr_text: str) -> None:
    assert "BORROW" in adr_text
    assert "REJECT" in adr_text


def test_adr_0003_has_decision_table(adr_text: str) -> None:
    assert "| Concept |" in adr_text or "| concept |" in adr_text.lower()
    assert "| Verdict |" in adr_text or "| verdict |" in adr_text.lower()


def test_adr_0003_documents_locomo_transfer_caveat(adr_text: str) -> None:
    assert "do not transfer" in adr_text.lower() or "does not transfer" in adr_text.lower()


def test_adr_0003_documents_stricter_than_hyde(adr_text: str) -> None:
    assert "HyDE" in adr_text or "hyde" in adr_text.lower()
    assert "Phase 17" in adr_text or "phase 17" in adr_text.lower()
