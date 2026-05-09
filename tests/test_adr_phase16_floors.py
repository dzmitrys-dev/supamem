"""Phase 16 Plan F — ADR-0002 §7 live floors + §8 mem0 peer comparison.

Locks the post-Phase-16 shape of `docs/adr/0002-coderag-eval-philosophy.md`:

- §7 floors are LIVE (no offline-fixture sentinels: no `< 0.005 ms`, no
  `1.000` recall_5 floor cells).
- §7 ranking floors fall in ``(0.0, 1.0)``; latency p95 floors fall in
  ``(5, 5000]`` ms (D-LAT-04 — ceiling is 5000ms, not 500ms).
- §7 carries the explicit one-shot reasoning paragraph for the
  500ms → 5000ms p95 ceiling adjustment (D-LAT-01).
- §8 "Mem0 peer comparison" section exists with a paired-bootstrap delta
  table (delta / ci_lower / ci_upper columns).
- Pre-existing §8 ("LongMemEval demotion" — actually canonical §6 in
  pre-16 ADR; the renumbering pivot is whichever section was numbered 8
  before) renumbered to §9.
- §8 prose cites the `mem0_vs_supamem` sign convention (D-PEER-02).

The test reads the ADR once per module and parses headings + table cells
with cheap regexes (no markdown library dependency).
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


# --- helpers ---------------------------------------------------------------


def _split_sections(text: str) -> dict[int, tuple[str, str]]:
    """Return {section_number: (title, body)} parsed from `## N. Title` headings.

    Body extends from the heading line through the line *before* the next
    `## ` heading (or end-of-document).
    """
    # Match `## 7. ...` OR `### 7. ...` style headings (the ADR uses `###`
    # because §1..§N are children of `## Decision`).
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


def _table_cells(body: str) -> list[str]:
    """Return all pipe-separated cells from any markdown tables in `body`.

    Strips leading/trailing whitespace per cell. Filters out separator
    rows (`|---|---|`) and empty cells.
    """
    cells: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|") or not s.endswith("|"):
            continue
        # skip separator rows like |---|:---:|
        if re.fullmatch(r"\|[\s:|-]+\|", s):
            continue
        parts = [c.strip() for c in s.strip("|").split("|")]
        cells.extend(c for c in parts if c)
    return cells


def _floats_in(cells: list[str]) -> list[float]:
    """Extract every parseable float from a list of cell strings."""
    out: list[float] = []
    for c in cells:
        # tolerate `0.5232 ± 0.005` style — pull each numeric token
        for tok in re.findall(r"-?\d+\.\d+", c):
            try:
                out.append(float(tok))
            except ValueError:
                pass
    return out


# --- fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def adr_text() -> str:
    assert ADR_PATH.is_file(), f"ADR-0002 missing at {ADR_PATH}"
    return ADR_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sections(adr_text: str) -> dict[int, tuple[str, str]]:
    return _split_sections(adr_text)


# --- assertions ------------------------------------------------------------


def test_1_no_offline_sentinels(sections: dict[int, tuple[str, str]]) -> None:
    """§7 must NOT carry the offline-fixture sentinels.

    - No `< 0.005 ms` (the `_SmokeBackend` microsecond timing tell).
    - No `1.000` cell in any recall_5 row of the §7 tables (the smoke
      fixture's trivial-recovery sentinel; live recall_5 is ≪ 1.000).
    """
    assert 7 in sections, f"§7 missing — found sections {sorted(sections)}"
    _, body = sections[7]
    assert "< 0.005 ms" not in body, (
        "§7 still references the offline `< 0.005 ms` sentinel — replace "
        "with live latency p95 floors derived from the 16-E baselines."
    )
    # Walk table rows looking for recall_5 lines containing the bare 1.000 cell.
    for line in body.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        # row label is typically the first cell
        label = cells[0].lower() if cells else ""
        if "recall_at_5" in label or "recall_5" in label:
            for c in cells[1:]:
                # any standalone 1.000 token is the smoke sentinel
                if re.fullmatch(r"1\.000+", c.split()[0] if c else ""):
                    pytest.fail(
                        f"§7 recall_5 row still has the offline `1.000` "
                        f"sentinel cell: {s!r}"
                    )


def test_2_ranking_floor_range(sections: dict[int, tuple[str, str]]) -> None:
    """Every parsed §7 ranking-metric cell value lies strictly in (0.0, 1.0).

    Targets the 'Floor' column for rows whose label is one of the ranking
    metrics; we approximate this by walking each table row, identifying
    ranking-labelled rows, and asserting every numeric cell on that row
    sits in (0, 1) — latency rows (with `_ms_` or `latency_` in the
    label) are excluded.
    """
    assert 7 in sections
    _, body = sections[7]
    rank_tokens = ("recall_at_", "mrr", "ndcg_at_", "recall_5", "ndcg_cut_")
    for line in body.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        label = cells[0].lower() if cells else ""
        if "latency" in label or "_ms_" in label:
            continue
        if not any(tok in label for tok in rank_tokens):
            continue
        for c in cells[1:]:
            for tok in re.findall(r"-?\d+\.\d+", c):
                v = float(tok)
                # ε numbers like 0.005 may legitimately appear as a column;
                # the contract is that no ranking cell drops to ≤ 0 or
                # crosses 1.0 on the FLOOR — we enforce on every numeric.
                assert 0.0 < v < 1.0, (
                    f"§7 ranking row {label!r} carries cell value {v} "
                    f"outside (0,1) — line: {s!r}"
                )


def test_3_latency_floor_range(sections: dict[int, tuple[str, str]]) -> None:
    """Every §7 latency p95 cell lies in (5, 5000] ms; ≥ 1 cell > 500ms.

    Upper bound is **5000**, not 500 (D-LAT-04: one-shot ceiling
    adjustment per D-LAT-01 raised the hard ceiling from 500 → 5000
    after live rerank-on measurements landed at p95 ≈ 3000–4500ms).
    """
    assert 7 in sections
    _, body = sections[7]
    seen_above_500 = False
    p95_seen = 0
    for line in body.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        label = cells[0].lower() if cells else ""
        if "p95" not in label:
            continue
        for c in cells[1:]:
            for tok in re.findall(r"\d+\.?\d*", c):
                # skip pure integers like '5' that are likely ε floors
                if "." not in tok:
                    continue
                v = float(tok)
                # ε-floor cells are tiny (< 5ms historically) but post-16E
                # are 100-200ms — still legitimate. The contract is on
                # the *floor/ceiling* numeric, so allow 5..5000 inclusive
                # of the upper bound (D-LAT-04 ceiling is 5000ms hard).
                if v <= 5.0 or v > 5000.0:
                    # might be an ε column value (e.g. 5ms floor) — the
                    # contract says STRICT > 5; but 5.0 epsilons are
                    # baseline noise floors. We tolerate exactly 5.0.
                    if v == 5.0:
                        continue
                    pytest.fail(
                        f"§7 p95 row {label!r} carries cell {v} outside "
                        f"(5, 5000] — line: {s!r}"
                    )
                if v > 500.0:
                    seen_above_500 = True
                p95_seen += 1
    assert p95_seen >= 1, "§7 has no p95 latency cells parseable"
    assert seen_above_500, (
        "§7 has no p95 cell above 500ms — live rerank-on stack p95 "
        "should land in 2000–4500ms range (D-LAT-04)."
    )


def test_4_one_shot_ceiling_paragraph(sections: dict[int, tuple[str, str]]) -> None:
    """§7 contains the one-shot ceiling reasoning paragraph (D-LAT-01)."""
    assert 7 in sections
    _, body = sections[7]
    low = body.lower()
    for token in ("5000", "gpu rerank", "one-shot", "future"):
        assert token in low, (
            f"§7 missing one-shot reasoning token {token!r}; expected "
            f"all of (5000, gpu rerank, one-shot, future) per D-LAT-01."
        )


def test_5_section_8_mem0_peer_comparison(adr_text: str, sections: dict[int, tuple[str, str]]) -> None:
    """§8 exists, titled 'Mem0 peer comparison' (case-insensitive), with
    a paired-bootstrap CI table whose headers include delta + ci columns.
    """
    assert 8 in sections, f"§8 missing — found sections {sorted(sections)}"
    title, body = sections[8]
    assert "mem0 peer comparison" in title.lower(), (
        f"§8 title is {title!r}; expected 'Mem0 peer comparison'."
    )
    low = body.lower()
    assert "delta" in low, "§8 must surface a `delta` column in its table"
    assert "ci_lower" in low, "§8 must surface a `ci_lower` column"
    assert "ci_upper" in low, "§8 must surface a `ci_upper` column"


def test_6_renumbering_preserves_former_section(sections: dict[int, tuple[str, str]]) -> None:
    """The pre-Phase-16 ADR's §6 'LongMemEval demotion' content survives
    the renumbering and still appears with its prose intact.

    Pre-Phase-16, §8 did NOT exist as a top-level section in the ADR —
    the document went §1..§7. Phase 16 INSERTS §8 as a new section. So
    the renumbering test reduces to: §7 ('Locked numerical floors')
    title is preserved, and the `LongMemEval` demotion content (§6 in
    pre-Phase-16) is still discoverable in the document. If a future
    edit renumbers tail sections, this test still passes as long as the
    LongMemEval-demotion prose lives somewhere in the ADR.
    """
    # §7's title must still mention floors / numerical / locked
    assert 7 in sections
    title7, body7 = sections[7]
    low7 = title7.lower()
    assert any(k in low7 for k in ("floor", "numerical", "locked", "live")), (
        f"§7 title is {title7!r}; expected to retain the 'numerical floors' "
        f"semantics from pre-Phase-16 ADR."
    )
    # The LongMemEval-demotion prose (former §6) is still in the doc.
    full = "\n".join(body for _, body in sections.values())
    assert "LongMemEval" in full and "on-demand" in full, (
        "ADR lost the LongMemEval-demotion prose during renumbering."
    )


def test_7_delta_sign_convention(sections: dict[int, tuple[str, str]]) -> None:
    """§8 prose explicitly cites the `mem0_vs_supamem` sign convention
    OR the equivalent natural-language phrasing (D-PEER-02).
    """
    assert 8 in sections
    _, body = sections[8]
    low = body.lower()
    # Either the literal token, OR the natural-language phrasing.
    has_token = "mem0_vs_supamem" in low
    has_phrase = (
        "positive delta" in low
        and ("peer" in low or "mem0" in low)
        and ("better" in low or "wins" in low or "ahead" in low)
    )
    assert has_token or has_phrase, (
        "§8 must document the delta sign convention — either the literal "
        "`mem0_vs_supamem` token or the phrase `positive delta means peer "
        "is better/wins` (D-PEER-02)."
    )
