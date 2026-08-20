"""Regression tests for WR-01 — atomic_write_json rewrote files it did not own.

``atomic_write_json`` decided "changed" by comparing the raw file TEXT against
its own canonical ``json.dumps(indent=2, ensure_ascii=False, sort_keys=False)``
output. Any file whose formatting merely differed — compact JSON, 4-space
indent, tabs — counted as changed even when the edit was a semantic no-op.

Consequences, reproduced in the review on a machine where supamem had NEVER
been installed: ``uninstall`` reported ``would_change = 2``, rewrote
``.mcp.json`` from one compact line to pretty-printed, and dropped two
``.bak.<ns>`` siblings. Because ``repair`` is uninstall-then-install and is
documented as "NOT a no-op on a healthy install", EVERY ``repair`` run
reformatted the user's ``~/.claude/settings.json`` / ``~/.claude.json`` /
``.mcp.json`` and left a fresh set of backups that nothing prunes.
``~/.claude.json`` is Claude Code's primary state file.

It also inflated the SM-7 dry-run count: "would rewrite 4 file(s)" where 2 of
the 4 were whitespace-only rewrites.
"""
from __future__ import annotations

import json
from pathlib import Path

from supamem.config_io import atomic_write_json

COMPACT = '{"mcpServers":{"other":{"command":"x"}}}'
FOUR_SPACE = '{\n    "mcpServers": {\n        "other": {\n            "command": "x"\n        }\n    }\n}'
TABBED = '{\n\t"mcpServers": {\n\t\t"other": {\n\t\t\t"command": "x"\n\t\t}\n\t}\n}'
CONTENT = {"mcpServers": {"other": {"command": "x"}}}


def _baks(target: Path) -> list[Path]:
    return sorted(target.parent.glob(target.name + ".bak.*"))


def test_compact_json_is_not_rewritten_when_semantically_identical(tmp_path: Path) -> None:
    """The exact WR-01 reproduction: a compact third-party `.mcp.json`.

    Pre-fix failure: written=True, the file became pretty-printed, and a
    `.bak.<ns>` sibling appeared — for a semantic no-op.
    """
    target = tmp_path / ".mcp.json"
    target.write_text(COMPACT, encoding="utf-8")

    res = atomic_write_json(target, CONTENT)

    assert res.written is False, "a semantic no-op must not write"
    assert res.diff == "", "a semantic no-op must not report a diff"
    assert res.backup_path is None
    assert target.read_text(encoding="utf-8") == COMPACT, "byte-identical"
    assert _baks(target) == [], "no .bak litter for a no-op"


def test_alternative_indentation_is_not_rewritten(tmp_path: Path) -> None:
    for label, body in (("4-space", FOUR_SPACE), ("tabs", TABBED)):
        target = tmp_path / f"{label}.json"
        target.write_text(body, encoding="utf-8")
        res = atomic_write_json(target, CONTENT)
        assert res.written is False, label
        assert target.read_text(encoding="utf-8") == body, label
        assert _baks(target) == [], label


def test_key_order_difference_is_still_a_no_op(tmp_path: Path) -> None:
    """JSON objects are unordered maps; a different key order is the same
    object graph, so rewriting purely to reorder is not ours to do."""
    target = tmp_path / "ordered.json"
    target.write_text('{"b": 2, "a": 1}', encoding="utf-8")

    res = atomic_write_json(target, {"a": 1, "b": 2})

    assert res.written is False
    assert target.read_text(encoding="utf-8") == '{"b": 2, "a": 1}'


def test_real_semantic_change_still_writes_and_backs_up(tmp_path: Path) -> None:
    """The guard must not suppress genuine edits — this is the whole point of
    the writer."""
    target = tmp_path / ".mcp.json"
    target.write_text(COMPACT, encoding="utf-8")

    res = atomic_write_json(target, {"mcpServers": {"other": {"command": "x"}, "supamem": {}}})

    assert res.written is True
    assert res.diff != ""
    assert res.backup_path is not None
    assert "supamem" in json.loads(target.read_text(encoding="utf-8"))["mcpServers"]
    assert len(_baks(target)) == 1


def test_unparseable_existing_file_falls_back_to_text_compare(tmp_path: Path) -> None:
    """A corrupt/non-JSON existing file must still be rewritten (with a backup)
    rather than silently skipped."""
    target = tmp_path / "broken.json"
    target.write_text("{not json at all", encoding="utf-8")

    res = atomic_write_json(target, CONTENT)

    assert res.written is True
    assert res.backup_path is not None
    assert json.loads(target.read_text(encoding="utf-8")) == CONTENT
    # The original bytes are recoverable.
    assert res.backup_path.read_text(encoding="utf-8") == "{not json at all"


def test_missing_file_is_created(tmp_path: Path) -> None:
    target = tmp_path / "new.json"
    res = atomic_write_json(target, CONTENT)
    assert res.written is True
    assert res.backup_path is None
    assert json.loads(target.read_text(encoding="utf-8")) == CONTENT


def test_dry_run_reports_no_change_for_formatting_only_difference(tmp_path: Path) -> None:
    """The SM-7 dry-run count must not be inflated by whitespace-only rewrites."""
    target = tmp_path / ".mcp.json"
    target.write_text(COMPACT, encoding="utf-8")

    res = atomic_write_json(target, CONTENT, dry_run=True)

    assert res.written is False
    assert res.diff == "", "a formatting-only difference must not count as would-change"
    assert target.read_text(encoding="utf-8") == COMPACT


def test_uninstall_is_a_true_no_op_when_supamem_was_never_installed(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end WR-01: the review reproduced would_change=2 plus two .bak
    files on a machine where supamem had never been installed."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".claude").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)

    mcp = project / ".mcp.json"
    settings = home / ".claude" / "settings.json"
    mcp_body = '{"mcpServers":{"other":{"command":"x"}}}'
    # A realistic third-party settings.json: compact, and carrying a hook that
    # is NOT supamem's, so the strip is a genuine semantic no-op. (Deliberately
    # not an EMPTY PreToolUse list — _strip_supamem_hook prunes empty
    # containers, which is a real semantic change and a separate question from
    # WR-01's formatting-only rewrites.)
    settings_body = (
        '{"hooks":{"PreToolUse":[{"matcher":"Edit",'
        '"hooks":[{"type":"command","command":"other-tool"}]}]}}'
    )
    mcp.write_text(mcp_body, encoding="utf-8")
    settings.write_text(settings_body, encoding="utf-8")

    from supamem.install.claude_code import uninstall

    assert uninstall() == 0, "nothing of supamem's is present — nothing to change"
    assert mcp.read_text(encoding="utf-8") == mcp_body, "byte-identical"
    assert settings.read_text(encoding="utf-8") == settings_body, "byte-identical"
    assert _baks(mcp) == []
    assert _baks(settings) == []
