"""Golden regression tests for SM-4/SM-6 duplicate managed-block healing.

Field-report replica (rev 2): upgrades accumulated TWO fenced blocks in
``~/CLAUDE.md`` (v0.2.0 + v0.3.0a7), which crashed install/uninstall/repair
with an unhandled ``ValueError`` from ``extract_managed_block``. These tests
prove the healed end state: exactly one merged block re-fenced at the current
version, user text outside the fences byte-identical, and a byte-level no-op
on healthy input.
"""
from __future__ import annotations

import pytest

from supamem.config_io import (
    extract_managed_block,
    sweep_managed_blocks,
    wrap_managed_block,
)

IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"
PREFIX = "# My personal instructions\n\nUse concise answers.\n"
MIDDLE = "\n## Middle section\n\nKeep this user text.\n"
SUFFIX = "\n## Tail\n\nThanks.\n"


def _two_block_fixture() -> str:
    """Field-report replica: user text around a v0.2.0 + v0.3.0a7 block pair."""
    old = wrap_managed_block(IMPORT_LINE, version="0.2.0")
    new = wrap_managed_block(IMPORT_LINE, version="0.3.0a7")
    return f"{PREFIX}{old}{MIDDLE}{new}{SUFFIX}"


def test_sweep_heals_golden_two_block_fixture() -> None:
    text = _two_block_fixture()
    healed, removed = sweep_managed_blocks(text, version="0.4.0a2")
    assert removed == 1
    assert healed.count("BEGIN SUPAMEM") == 1
    assert "# BEGIN SUPAMEM v0.4.0a2 MANAGED BLOCK" in healed
    assert "# END SUPAMEM v0.4.0a2 MANAGED BLOCK" in healed
    # Import line survives exactly once (order-preserving line dedupe).
    assert healed.count(IMPORT_LINE) == 1
    # User content outside the fence pairs is byte-identical.
    assert healed.startswith(PREFIX)
    assert MIDDLE in healed
    assert healed.endswith(SUFFIX)


def test_sweep_single_block_is_byte_level_noop() -> None:
    text = f"{PREFIX}{wrap_managed_block('owned line', version='0.4.0a2')}{SUFFIX}"
    healed, removed = sweep_managed_blocks(text, version="0.4.0a2")
    assert removed == 0
    assert healed == text


def test_sweep_zero_blocks_is_byte_level_noop() -> None:
    text = "just user text\nnothing managed here\n"
    healed, removed = sweep_managed_blocks(text, version="0.4.0a2")
    assert removed == 0
    assert healed == text


def test_sweep_is_idempotent() -> None:
    """SM-4b: sweep(sweep(text)) is a fixed point — second call returns the
    same output and count 0."""
    text = _two_block_fixture()
    once, removed_1 = sweep_managed_blocks(text, version="0.4.0a2")
    twice, removed_2 = sweep_managed_blocks(once, version="0.4.0a2")
    assert removed_1 == 1
    assert removed_2 == 0
    assert twice == once


def test_sweep_preserves_lines_between_duplicate_fences() -> None:
    """Non-block lines BETWEEN the two duplicate fences survive verbatim."""
    between = "keep line one\nkeep line two\n"
    old = wrap_managed_block(IMPORT_LINE, version="0.2.0")
    new = wrap_managed_block(IMPORT_LINE, version="0.3.0a7")
    text = f"{PREFIX}{old}\n{between}\n{new}{SUFFIX}"
    healed, removed = sweep_managed_blocks(text, version="0.4.0a2")
    assert removed == 1
    assert between in healed


def test_extract_still_raises_on_raw_two_block_fixture() -> None:
    """Belt-and-braces alongside tests/test_config_io.py:122 — the strict
    tripwire remains reachable on the unhealed field-report replica.

    CR-01 narrowed *what* trips it: the tripwire now counts complete BEGIN/END
    fence pairs rather than bare BEGIN mentions, so it fires only on states
    ``sweep_managed_blocks`` can actually heal. This fixture — two genuinely
    complete blocks — is exactly such a state, so it still raises; the message
    changed to say "managed blocks" because that is now what is being counted.
    See tests/test_managed_block_asymmetry.py for the states that must NOT raise.
    """
    with pytest.raises(ValueError, match="multiple SUPAMEM managed blocks"):
        extract_managed_block(_two_block_fixture())


# ──────────────────────── WR-06: block-level dedup ─────────────────────────


def test_sweep_preserves_repeated_lines_inside_the_managed_region() -> None:
    """WR-06: owned content was deduplicated LINE-by-line across blocks, so any
    legitimately repeated line vanished — blank lines (collapsing a multi-
    paragraph managed region into one paragraph), markdown ``---`` separators,
    repeated indented list items.

    The docstring promised text was "preserved verbatim"; that only ever held
    for text OUTSIDE the fences. Dedup is now per-block, so a block's internal
    structure survives intact.
    """
    owned = "para one\n\nsecond para\n\n---\n\n- item\n- item\n"
    old = wrap_managed_block(owned, version="0.2.0")
    new = wrap_managed_block("something else\n", version="0.3.0a7")
    healed, removed = sweep_managed_blocks(f"{PREFIX}{old}\n{new}{SUFFIX}", version="0.4.0a2")

    assert removed == 1
    assert owned in healed, f"the first block's internal structure must survive: {healed!r}"
    assert healed.count("- item") == 2, "repeated list items must both survive"
    assert "---" in healed, "markdown separators must survive"
    assert "something else" in healed, "the second block's distinct content is kept"


def test_sweep_dedups_identical_blocks_not_identical_lines() -> None:
    """Two byte-identical blocks collapse to one copy; a block that merely
    SHARES a line with another keeps its own copy of that line."""
    shared = "@~/.supamem/share/rules/dual-memory.md"
    a = wrap_managed_block(f"{shared}\nunique to a", version="0.2.0")
    b = wrap_managed_block(f"{shared}\nunique to b", version="0.3.0a7")
    healed, _ = sweep_managed_blocks(f"{a}\n{b}", version="0.4.0a2")
    assert healed.count(shared) == 2, "distinct blocks each keep their shared line"
    assert "unique to a" in healed
    assert "unique to b" in healed

    dup = wrap_managed_block(shared, version="0.2.0")
    dup2 = wrap_managed_block(shared, version="0.3.0a7")
    healed2, _ = sweep_managed_blocks(f"{dup}\n{dup2}", version="0.4.0a2")
    assert healed2.count(shared) == 1, "byte-identical owned content collapses"
