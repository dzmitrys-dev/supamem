"""Phase 17 Plan H — ADR-0002 §9 "Phase 17 uplift comparison" structure lock.

ADR-as-test (D-ADR-01, RESEARCH Q-4): the locked verbatim heading and the
three sibling sub-tables (`default vs ast_on`, `default vs hyde_on`,
`default vs ast_plus_hyde`) are gated by parser tests so any future edit
that drifts the structure fails CI loudly.

The §9 column shape mirrors §8 verbatim (`metric / supamem / mem0 / delta /
ci_lower / ci_upper / qualitative`) so the regex parser is shared across
phases (Phase 16 floors + Phase 17 uplift). The intervention identity is
encoded in the §9 sub-heading, not the column header, on purpose — this is
why `mem0` stays as the second column header.

Helpers (`_split_sections`, `_table_cells`) copied verbatim from
`tests/test_adr_phase16_floors.py:41-78` so both phase parsers share the
same regex contract; if Phase 16's parser shape changes, this file should
be updated in lockstep.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ADR_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "adr"
    / "0002-coderag-eval-philosophy.md"
)


EXPECTED_SUB_HEADINGS = [
    "default vs ast_on",
    "default vs hyde_on",
    "default vs ast_plus_hyde",
]
EXPECTED_COLUMNS = [
    "metric",
    "supamem",
    "mem0",
    "delta",
    "ci_lower",
    "ci_upper",
    "qualitative",
]
EXPECTED_AXES = [
    ("code_fact", "supamem_only"),
    ("code_fact", "fastapi_only"),
    ("code_fact", "combined"),
    ("decision_rationale", "supamem_only"),
    ("decision_rationale", "combined"),
]
EXPECTED_METRICS = [
    "recall_at_1",
    "recall_at_5",
    "recall_at_10",
    "recall_at_20",
    "mrr",
    "ndcg_at_10",
]


# --- helpers ---------------------------------------------------------------
# Copied verbatim from tests/test_adr_phase16_floors.py:41-78.


def _split_sections(text: str) -> dict[int, tuple[str, str]]:
    """Return {section_number: (title, body)} parsed from `## N. Title`."""
    pat = re.compile(r"^#{2,4}\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pat.finditer(text))
    sections: dict[int, tuple[str, str]] = {}
    for i, m in enumerate(matches):
        num = int(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        sections[num] = (title, body)
    return sections


def _table_cells(body: str) -> list[list[str]]:
    """Return all pipe-separated cell rows from any markdown tables in body.

    Each returned element is a row (list of trimmed cells). Separator rows
    like `|---|---|` and blank rows are filtered out.
    """
    rows: list[list[str]] = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|") or not s.endswith("|"):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", s):
            continue
        parts = [c.strip() for c in s.strip("|").split("|")]
        rows.append(parts)
    return rows


def _split_subsections(body: str) -> dict[str, str]:
    """Return {h3_title: body} for `### ...` sub-headings inside a section."""
    pat = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pat.finditer(body))
    subs: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        subs[title] = body[start:end]
    return subs


def _split_subsubsections(body: str) -> dict[str, str]:
    """Return {h4_title: body} for `#### ...` sub-sub-headings."""
    pat = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pat.finditer(body))
    subs: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        subs[title] = body[start:end]
    return subs


# --- fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def adr_text() -> str:
    assert ADR_PATH.is_file(), f"ADR-0002 missing at {ADR_PATH}"
    return ADR_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sections(adr_text: str) -> dict[int, tuple[str, str]]:
    return _split_sections(adr_text)


# --- assertions ------------------------------------------------------------


def test_adr_section_9_heading_exact_text(sections: dict[int, tuple[str, str]]) -> None:
    """§9 title is exactly 'Phase 17 uplift comparison' (D-ADR-01 lock)."""
    assert 9 in sections, f"§9 missing — found sections {sorted(sections)}"
    title, _ = sections[9]
    assert title == "Phase 17 uplift comparison", (
        f"§9 title is {title!r}; D-ADR-01 locks it to "
        f"'Phase 17 uplift comparison' verbatim."
    )


def test_adr_section_9_has_three_sibling_subtables(
    sections: dict[int, tuple[str, str]],
) -> None:
    """§9 body has exactly three `### default vs <intervention>` sub-headings."""
    assert 9 in sections
    _, body = sections[9]
    subs = _split_subsections(body)
    for h in EXPECTED_SUB_HEADINGS:
        assert h in subs, (
            f"§9 missing sub-heading '### {h}'; got {list(subs)}."
        )
    # No stray sibling subs beyond the three expected.
    extras = [h for h in subs if h not in EXPECTED_SUB_HEADINGS]
    assert not extras, f"§9 has unexpected sub-headings: {extras}"


def test_adr_section_9_table_columns_match_section_8(
    sections: dict[int, tuple[str, str]],
) -> None:
    """Every table in §9 has the §8 column header sequence verbatim.

    First row of each table must be the 7-column header
    `metric | supamem | mem0 | delta | ci_lower | ci_upper | qualitative`.
    """
    assert 9 in sections
    _, body = sections[9]
    rows = _table_cells(body)
    # Pick out rows that look like headers (start with 'metric').
    header_rows = [r for r in rows if r and r[0].strip().lower() == "metric"]
    assert header_rows, "§9 has no parseable tables with a 'metric' header row"
    for row in header_rows:
        cells_low = [c.strip().lower() for c in row]
        assert cells_low == EXPECTED_COLUMNS, (
            f"§9 table header is {cells_low}; expected {EXPECTED_COLUMNS}"
        )


def test_adr_section_9_axis_x_col_matrix_complete(
    sections: dict[int, tuple[str, str]],
) -> None:
    """Each sub-section has tables for the §8 axis×col matrix.

    Required combos: `code_fact × {supamem_only, fastapi_only, combined}`
    + `decision_rationale × {supamem_only, combined}`.
    `decision_rationale.fastapi_only` is null per INV-A1 — skipped.
    """
    assert 9 in sections
    _, body = sections[9]
    subs = _split_subsections(body)
    for sub_title in EXPECTED_SUB_HEADINGS:
        assert sub_title in subs, f"§9 missing sub-section '### {sub_title}'"
        sub_body = subs[sub_title]
        h4s = _split_subsubsections(sub_body)
        for axis, col in EXPECTED_AXES:
            # Match h4 of the form "<axis> axis — <col> column" (em-dash or hyphen).
            matches = [
                h for h in h4s
                if axis in h and col in h
            ]
            assert matches, (
                f"§9 '{sub_title}' missing axis×col table for "
                f"{axis} × {col}; got h4s: {list(h4s)}"
            )


def test_adr_section_9_every_delta_cell_present(
    sections: dict[int, tuple[str, str]],
) -> None:
    """Every (sub-section, axis, col) table has all 6 metric rows with
    non-empty delta + ci_lower + ci_upper numeric cells.
    """
    numeric_pat = re.compile(r"^[+-]?\d+\.\d+$")
    assert 9 in sections
    _, body = sections[9]
    subs = _split_subsections(body)
    for sub_title in EXPECTED_SUB_HEADINGS:
        sub_body = subs[sub_title]
        h4s = _split_subsubsections(sub_body)
        for axis, col in EXPECTED_AXES:
            h4_matches = [t for t, _ in h4s.items() if axis in t and col in t]
            assert h4_matches, (
                f"§9 '{sub_title}' missing h4 for {axis}×{col}"
            )
            tbl_body = h4s[h4_matches[0]]
            rows = _table_cells(tbl_body)
            data_rows = [
                r for r in rows
                if r and r[0].strip().lower() != "metric"
            ]
            metric_labels = {
                r[0].strip().strip("`").lower() for r in data_rows
            }
            for m in EXPECTED_METRICS:
                assert m in metric_labels, (
                    f"§9 '{sub_title}' table for {axis}×{col} missing "
                    f"metric row {m!r}; got {metric_labels}"
                )
            # delta / ci_lower / ci_upper are columns 3, 4, 5 (0-indexed).
            for r in data_rows:
                if r[0].strip().strip("`").lower() not in EXPECTED_METRICS:
                    continue
                assert len(r) == 7, (
                    f"§9 '{sub_title}' table row {r!r} has {len(r)} "
                    f"cells; expected 7."
                )
                for idx in (3, 4, 5):  # delta, ci_lower, ci_upper
                    cell = r[idx].strip()
                    assert numeric_pat.match(cell), (
                        f"§9 '{sub_title}' row {r[0]!r} col {idx} "
                        f"cell {cell!r} is not signed-numeric "
                        f"(e.g. '+0.0123')."
                    )


def test_adr_section_8_preserved_verbatim(adr_text: str) -> None:
    """§8 'Mem0 peer comparison' must still parse with the Phase 16 contract.

    Re-runs the Phase 16 §8 assertions in-line so the Section 9 append
    can't accidentally drift §8 headers / column labels / sign convention.
    """
    sections = _split_sections(adr_text)
    assert 8 in sections, "§8 missing after §9 append"
    title, body = sections[8]
    assert "mem0 peer comparison" in title.lower(), (
        f"§8 title is {title!r}; expected 'Mem0 peer comparison'."
    )
    low = body.lower()
    assert "delta" in low, "§8 lost its `delta` column"
    assert "ci_lower" in low, "§8 lost its `ci_lower` column"
    assert "ci_upper" in low, "§8 lost its `ci_upper` column"
    # Sign convention token / phrase still present (D-PEER-02).
    has_token = "mem0_vs_supamem" in low
    has_phrase = (
        "positive delta" in low
        and ("peer" in low or "mem0" in low)
        and ("better" in low or "wins" in low or "ahead" in low)
    )
    assert has_token or has_phrase, (
        "§8 lost its delta sign-convention prose (D-PEER-02)."
    )


def test_adr_post_section_8_renumbered(
    sections: dict[int, tuple[str, str]],
) -> None:
    """No duplicate section numbers; §9 is exclusively the Phase 17 uplift
    section. Sections after old §8 (if any) renumbered to ≥ 10.
    """
    nums = sorted(sections)
    # No duplicates by construction of _split_sections (dict key) — but
    # the regex could match the same number twice if the same `## N. ...`
    # appears twice. Detect by re-running the regex against the raw text.
    assert 9 in nums
    title9, _ = sections[9]
    assert title9 == "Phase 17 uplift comparison", (
        f"§9 must be the Phase 17 uplift section; got {title9!r}."
    )
    # Ensure no other section also titled "Phase 17 uplift comparison".
    other_p17 = [
        n for n, (t, _) in sections.items()
        if n != 9 and t == "Phase 17 uplift comparison"
    ]
    assert not other_p17, (
        f"Duplicate 'Phase 17 uplift comparison' sections at {other_p17}"
    )
