"""Golden regression tests for SM-4/SM-6 duplicate managed-block healing.

Field-report replica (rev 2): upgrades accumulated TWO fenced blocks in
``~/CLAUDE.md`` (v0.2.0 + v0.3.0a7), which crashed install/uninstall/repair
with an unhandled ``ValueError`` from ``extract_managed_block``. These tests
prove the healed end state: exactly one merged block re-fenced at the current
version, user text outside the fences byte-identical, and a byte-level no-op
on healthy input.
"""
from __future__ import annotations

from supamem.config_io import sweep_managed_blocks, wrap_managed_block

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
