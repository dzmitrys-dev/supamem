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
