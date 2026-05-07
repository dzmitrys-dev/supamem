"""Phase 15 Plan E Task E1 — ADR-0002 doc-shape tests.

Enforces the required content of ``docs/adr/0002-coderag-eval-philosophy.md``
without coupling to prose: the must-haves table in 15-E-PLAN's frontmatter
becomes 11 substring/structural assertions here.

The numerical-floor table is filled at ADR-author time from
``.planning/phases/15-agentic-coding-eval-suite/15-C-SUMMARY.md`` — the
test in this file only checks that *one* of the named metrics is mentioned
in narrative form (``recall_at_5``, ``mrr``, or ``ndcg_at_10``), so the
ADR is free to reorganise the table without breaking this lock.
"""
from __future__ import annotations

from pathlib import Path

import pytest


ADR_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "adr"
    / "0002-coderag-eval-philosophy.md"
)


@pytest.fixture(scope="module")
def adr_text() -> str:
    return ADR_PATH.read_text(encoding="utf-8")


def test_adr_0002_exists() -> None:
    assert ADR_PATH.is_file(), f"ADR-0002 missing at {ADR_PATH}"


def test_adr_0002_has_madr_frontmatter(adr_text: str) -> None:
    assert adr_text.startswith("---\n"), "ADR-0002 must open with MADR `---` frontmatter"
    fm_end = adr_text.find("\n---", 4)
    assert fm_end > 0, "ADR-0002 frontmatter missing closing `---`"
    fm = adr_text[4:fm_end]
    for key in ("status:", "date:", "deciders:"):
        assert key in fm, f"ADR-0002 frontmatter missing `{key}`"


def test_adr_0002_has_required_sections(adr_text: str) -> None:
    for section in ("## Context", "## Decision", "## Consequences", "## Alternatives Considered"):
        assert section in adr_text, f"ADR-0002 missing `{section}` section"


def test_adr_0002_cites_coderag_bench(adr_text: str) -> None:
    assert "CodeRAG-Bench" in adr_text


def test_adr_0002_cites_swe_bench_cl(adr_text: str) -> None:
    assert "SWE-Bench-CL" in adr_text


def test_adr_0002_documents_corpus_pin_policy(adr_text: str) -> None:
    assert "commit_sha" in adr_text or "commit-SHA" in adr_text or "commit SHA" in adr_text


def test_adr_0002_documents_longmemeval_demotion(adr_text: str) -> None:
    assert "LongMemEval" in adr_text
    assert "on-demand" in adr_text


def test_adr_0002_documents_mem0_peer(adr_text: str) -> None:
    assert "mem0" in adr_text
    assert "supamem_eval_coderag_mem0" in adr_text


def test_adr_0002_documents_fastapi_no_adr(adr_text: str) -> None:
    assert "fastapi" in adr_text
    assert ("no ADR" in adr_text) or ("fastapi_only" in adr_text and "null" in adr_text)


def test_adr_0002_documents_epsilon_floors(adr_text: str) -> None:
    assert any(token in adr_text for token in ("ε", "epsilon", "eps")), \
        "ADR-0002 must document the ε derivation rule"
    # At least one numeric anchor (1× stddev / 5% / 5ms / 0.005 / 500ms).
    numeric_anchors = ("stddev", "5%", "5ms", "0.005", "500ms")
    assert any(token in adr_text for token in numeric_anchors), \
        f"ADR-0002 must mention at least one ε numeric anchor; tried {numeric_anchors!r}"


def test_adr_0002_links_to_15c_baseline_data(adr_text: str) -> None:
    metric_names = ("recall_at_5", "mrr", "ndcg_at_10")
    assert any(m in adr_text for m in metric_names), \
        f"ADR-0002 body must reference at least one measured metric name from 15-C; tried {metric_names!r}"


def test_adr_0002_has_no_unfilled_placeholders(adr_text: str) -> None:
    assert "<FROM_15C_SUMMARY>" not in adr_text, \
        "ADR-0002 still has <FROM_15C_SUMMARY> placeholders — fill from 15-C-SUMMARY.md"
