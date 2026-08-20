"""Regression tests for CR-01 / CR-03 — the managed-block marker regexes disagreed.

Three regexes described the same concept and matched three different things:

* ``config_io._FENCE_RE``  — complete BEGIN/END *pairs*, not line-anchored.
* ``config_io._BEGIN_RE``  — bare BEGIN *mentions*, not line-anchored.
* ``doctor._VERSION_RE``   — looser still (no ``# `` prefix, no ``— DO NOT EDIT``
  suffix), counting BEGIN *mentions*, behind a comment claiming parity with
  ``_FENCE_RE``.

Two consequences, both reproduced in the phase 19.1 review:

CR-01 — ``extract_managed_block`` raised ``ValueError`` when the count of bare
BEGIN markers exceeded one, while ``sweep_managed_blocks`` (the healer that is
supposed to make that raise unreachable) only ever merges complete *pairs*. Any
asymmetric state was therefore fatal *and* unhealable: install / uninstall /
repair all died with an unhandled traceback and no recovery path.

CR-03 — ``supamem doctor`` counted the same bare mentions, so a single prose
sentence in the user's own ``~/CLAUDE.md`` inflated ``block_count``, flipped
``any_drift``, and printed "run supamem repair" for a state repair cannot
change. Un-actionable permanent red.

Because neither regex was line-anchored, any file that merely *documents* the
marker trips both — including this repository's own ``CLAUDE.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem.config_io import (
    count_managed_blocks,
    extract_managed_block,
    sweep_managed_blocks,
    wrap_managed_block,
)

IMPORT_LINE = "@~/.supamem/share/rules/dual-memory.md"

# The exact benign prose from the CR-01 reproduction: a backticked mention of
# the marker inside an ordinary sentence. The marker is NOT at column 0 — it is
# wrapped in backticks — so no line-anchored fence regex may see it.
PROSE = (
    "supamem manages a fenced region that starts with a line like\n"
    "`# BEGIN SUPAMEM v0.4.0a2 MANAGED BLOCK — DO NOT EDIT`.\n"
)
INDENTED_PROSE = (
    "Example:\n"
    "    # BEGIN SUPAMEM v0.4.0a2 MANAGED BLOCK — DO NOT EDIT\n"
    "    # END SUPAMEM v0.4.0a2 MANAGED BLOCK\n"
)


def _real_block(version: str = "0.4.0a2") -> str:
    return wrap_managed_block(IMPORT_LINE, version=version)


# ──────────────────────── CR-01: the tripwire ──────────────────────────────


def test_prose_mention_plus_real_block_does_not_raise() -> None:
    """CR-01 primary repro: prose mention + one real block must extract cleanly.

    Pre-fix failure: ``ValueError: multiple BEGIN SUPAMEM markers found in
    text`` — ``_BEGIN_RE`` counted the backticked prose mention as a marker.
    """
    text = f"{PROSE}\n{_real_block()}\n"
    before, owned, after = extract_managed_block(text)
    assert owned == IMPORT_LINE
    # The prose is user text and must survive on the `before` side verbatim.
    assert PROSE in before


def test_two_prose_mentions_without_any_real_block_is_not_a_block() -> None:
    """CR-01 secondary route: two prose mentions, no real block at all.

    Pre-fix failure: the very *first* ``install`` on such a file raised.
    """
    text = PROSE + PROSE
    before, owned, after = extract_managed_block(text)
    assert owned == ""
    assert before == text


def test_indented_marker_block_is_not_a_managed_block() -> None:
    """An indented (fenced-code-sample) marker pair is documentation, not a fence."""
    assert count_managed_blocks(INDENTED_PROSE) == 0
    before, owned, after = extract_managed_block(INDENTED_PROSE)
    assert owned == ""


def test_asymmetric_two_begin_one_end_does_not_raise() -> None:
    """CR-01 third route: a user mangles an END fence, so the next install
    appends a second complete block → 2 BEGIN / 1 pair.

    Pre-fix failure: ``ValueError`` on every subsequent verb, and
    ``sweep_managed_blocks`` reported 0 duplicates so ``repair`` — the
    documented remedy — was a permanent no-op.
    """
    mangled = (
        "# BEGIN SUPAMEM v0.2.0 MANAGED BLOCK — DO NOT EDIT\n"
        f"{IMPORT_LINE}\n"
        "# END SUPAMEM v0.2.0 MANAGED-BLOCK\n"  # user typo breaks the END fence
    )
    text = f"# notes\n{mangled}\n{_real_block()}\n"
    # Must not raise.
    before, owned, after = extract_managed_block(text)
    assert IMPORT_LINE in owned


def test_lone_orphan_begin_marker_does_not_raise_and_is_swept() -> None:
    """An unpaired BEGIN marker is a malformed-fence problem for the healer to
    normalize, never a reason to abort the installer."""
    text = f"# notes\n# BEGIN SUPAMEM v0.2.0 MANAGED BLOCK — DO NOT EDIT\n{_real_block()}\n"
    extract_managed_block(text)  # must not raise

    healed, removed = sweep_managed_blocks(text, version="0.4.0a2")
    assert removed >= 1, "the orphan marker line must be reported as swept"
    assert "v0.2.0" not in healed, "the orphan BEGIN line must be gone"
    assert "# notes" in healed, "user text around the orphan survives"
    assert count_managed_blocks(healed) == 1


def test_sweep_output_is_always_accepted_by_extract() -> None:
    """The load-bearing invariant CR-01 broke: ``sweep_managed_blocks`` must
    return a state ``extract_managed_block`` provably accepts, for *every*
    input — that is what makes ``repair`` a real remedy rather than a no-op.
    """
    orphan_begin = "# BEGIN SUPAMEM v0.1.0 MANAGED BLOCK — DO NOT EDIT\n"
    orphan_end = "# END SUPAMEM v0.1.0 MANAGED BLOCK\n"
    pathological = [
        "",
        PROSE,
        PROSE + PROSE,
        INDENTED_PROSE,
        _real_block(),
        _real_block("0.2.0") + "\n" + _real_block("0.3.0a7"),
        PROSE + _real_block(),
        orphan_begin,
        orphan_end,
        orphan_begin + orphan_begin,
        orphan_begin + _real_block(),
        _real_block() + orphan_begin,
        orphan_end + _real_block("0.2.0") + orphan_begin,
        f"# notes\n{orphan_begin}user text\n{orphan_end}\n{_real_block()}\n",
    ]
    for raw in pathological:
        healed, _removed = sweep_managed_blocks(raw, version="0.4.0a2")
        assert count_managed_blocks(healed) <= 1, f"sweep left >1 pair for {raw!r}"
        try:
            extract_managed_block(healed)
        except ValueError as exc:  # pragma: no cover — the bug this test pins
            pytest.fail(f"extract raised {exc!r} on swept output of {raw!r}")
        # And sweeping again is a fixed point.
        again, removed_again = sweep_managed_blocks(healed, version="0.4.0a2")
        assert removed_again == 0, f"sweep not idempotent for {raw!r}"
        assert again == healed, f"sweep not byte-stable for {raw!r}"


def test_two_complete_blocks_still_raise() -> None:
    """The strict tripwire stays reachable for the state it was designed for:
    two genuinely complete, healable blocks."""
    text = _real_block("0.2.0") + "\n" + _real_block("0.3.0a7")
    assert count_managed_blocks(text) == 2
    with pytest.raises(ValueError, match="managed blocks"):
        extract_managed_block(text)


# ──────────────────────── CR-03: doctor's count ────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_doctor_does_not_count_prose_mentions_as_blocks(home: Path) -> None:
    """CR-03 repro: one prose sentence + one real current-version block.

    Pre-fix failure: ``block_count == 2``, ``drift is True`` → doctor printed
    "2 managed blocks detected — run supamem repair" and exited 1, while
    ``sweep_managed_blocks`` would heal 0 blocks. Permanently red, un-actionable.
    """
    from supamem import __version__
    from supamem.doctor import version_drift_report

    (home / "CLAUDE.md").write_text(
        f"{PROSE}\n{wrap_managed_block(IMPORT_LINE, version=__version__)}\n",
        encoding="utf-8",
    )
    rows = {r["client"]: r for r in version_drift_report()}
    row = rows["claude-code"]
    assert row["block_count"] == 1, row
    assert row["block_version"] == __version__, row
    assert row["drift"] is False, row


def test_doctor_render_omits_duplicate_warning_for_prose_mention(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rendered line must not demand a repair that repair cannot perform."""
    import supamem.doctor as mod
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    (home / "CLAUDE.md").write_text(
        f"{PROSE}\n{wrap_managed_block(IMPORT_LINE, version=__version__)}\n",
        encoding="utf-8",
    )
    mod.run_doctor()
    flat = " ".join(capsys.readouterr().out.split())
    assert "managed blocks detected" not in flat, flat
    assert f"v{__version__} (current)" in flat, flat


def test_doctor_reports_malformed_fence_distinctly_from_duplicates(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CR-03 follow-through: an unpaired marker is a *different* state from
    duplicate blocks and must be labelled honestly — and it must be one that
    ``repair`` genuinely clears (``sweep_managed_blocks`` strips orphans)."""
    import supamem.doctor as mod
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    body = (
        "# BEGIN SUPAMEM v0.2.0 MANAGED BLOCK — DO NOT EDIT\n"
        f"{wrap_managed_block(IMPORT_LINE, version=__version__)}\n"
    )
    (home / "CLAUDE.md").write_text(body, encoding="utf-8")

    rows = {r["client"]: r for r in mod.version_drift_report()}
    assert rows["claude-code"]["malformed"] is True
    assert rows["claude-code"]["block_count"] == 1

    mod.run_doctor()
    flat = " ".join(capsys.readouterr().out.split())
    assert "malformed" in flat.lower(), flat
    assert "managed blocks detected" not in flat, flat

    # And the remedy actually works: one sweep clears the malformed state.
    healed, removed = sweep_managed_blocks(body)
    assert removed >= 1
    (home / "CLAUDE.md").write_text(healed, encoding="utf-8")
    rows = {r["client"]: r for r in mod.version_drift_report()}
    assert rows["claude-code"]["malformed"] is False
    assert rows["claude-code"]["drift"] is False


def test_doctor_and_config_io_share_one_marker_regex() -> None:
    """Parity by construction, not by comment: doctor must not carry its own
    private marker regex (two comments previously asserted a false parity)."""
    import supamem.doctor as mod

    assert not hasattr(mod, "_VERSION_RE"), (
        "doctor must consume the config_io fence regex, not duplicate it"
    )


# ──────────────────── CR-01 end to end through the installer ───────────────


@pytest.fixture
def project(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path_factory.mktemp("workspace")
    monkeypatch.chdir(cwd)
    return cwd


def test_install_then_uninstall_survives_prose_mention(home: Path, project: Path) -> None:
    """CR-01 full reproduction: a ``~/CLAUDE.md`` that merely documents the
    marker must not brick every verb after the first successful install.

    Pre-fix failure: install → ValueError, uninstall → ValueError,
    repair → ValueError, with sweep reporting 0 healable blocks.
    """
    from supamem.install.claude_code import install, uninstall

    claude_md = home / "CLAUDE.md"
    claude_md.write_text(PROSE, encoding="utf-8")

    install()
    body = claude_md.read_text(encoding="utf-8")
    assert count_managed_blocks(body) == 1
    assert PROSE in body, "the user's prose must survive install"

    # Every subsequent verb must keep working (this is what used to die).
    install()
    assert count_managed_blocks(claude_md.read_text(encoding="utf-8")) == 1

    uninstall()
    body = claude_md.read_text(encoding="utf-8")
    assert count_managed_blocks(body) == 0
    assert "supamem manages a fenced region" in body, "prose survives uninstall"
