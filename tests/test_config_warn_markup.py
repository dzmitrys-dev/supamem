"""Regression tests for CR-04 — user config keys interpolated into Rich markup.

``_warn_unknown_config_keys``' own docstring states the hard requirement:

    MUST stay warn-only — ``load_config`` runs on every invocation including
    the MCP stdio server path, so a typo must never break startup

The table label was deliberately markup-escaped (``\\[{table}]``), so the hazard
was known — but the *user-controlled* key names went into the same Rich markup
string raw. TOML permits arbitrary characters in quoted keys, so a key named
``[/bold]`` raised ``rich.errors.MarkupError`` straight out of ``load_config()``,
breaking every CLI subcommand and the MCP stdio server start. A quieter second
mode: ``[bold]my_typo`` rendered as ``my_typo``, so the diagnostic named a key
the user could not find in their file.

The fix builds the message as PLAIN text and escapes once at print time, so no
user-supplied byte can ever be parsed as markup and every key is reported
verbatim. Sibling fail-closed sites that interpolate user-controlled *string*
values (``reranker_name``, ``mcp.response_format``) had the same shape and must
still exit 2 cleanly rather than raising MarkupError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem.config import load_config


def _write_cfg(tmp_path: Path, body: str) -> Path:
    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


# ───────────────────── CR-04: the warn-only startup path ───────────────────


def test_closing_tag_key_does_not_break_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact CR-04 reproduction.

    Pre-fix failure: ``rich.errors.MarkupError: closing tag '[/bold]' at
    position 74 doesn't match any open tag`` propagating out of load_config().
    """
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, '[supamem.eval]\n"[/bold]" = 1\n')

    cfg, _chain = load_config(root)  # must not raise

    captured = capsys.readouterr()
    assert captured.out == "", "MCP stdio purity: the warning belongs on stderr"
    assert "[/bold]" in captured.err, captured.err


def test_open_tag_key_is_reported_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The quieter failure mode: ``[bold]my_typo`` rendered as ``my_typo``, so
    the warning named a key that does not appear in the user's file."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, '[supamem.eval]\n"[bold]my_typo" = 1\n')

    load_config(root)

    err = capsys.readouterr().err
    assert "[bold]my_typo" in err, (
        f"the key must be reported exactly as written in the TOML; got: {err!r}"
    )


def test_table_label_still_renders_literally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard on the behavior the original ``\\[`` escape bought:
    the table name renders as a literal ``[supamem.eval]``, not as markup."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, "[supamem.eval]\nbogus_eval_key = 42\n")

    load_config(root)

    err = capsys.readouterr().err
    assert "[supamem.eval]" in err, err
    assert "bogus_eval_key" in err, err


@pytest.mark.parametrize(
    "key",
    ["[/bold]", "[bold]", "[supamem.warn]", "[/]", "[red]x[/red]", "[[weird]]"],
)
def test_no_markup_shaped_key_can_break_startup(
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No markup-shaped key may ever escape as an exception from the warn path
    — that is the invariant the docstring promises."""
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, f'[supamem.eval]\n"{key}" = 1\n')

    load_config(root)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert key in captured.err, captured.err


# ──────────── CR-04 siblings: fail-closed sites with the same shape ─────────


def test_reranker_name_with_markup_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``reranker_name`` is a user-controlled string interpolated into markup.

    Pre-fix failure: MarkupError instead of the intended clean ``SystemExit(2)``
    — the fail-closed gate stopped being fail-closed and became a traceback.
    """
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, '[supamem.reranker]\nname = "[/bold]"\n')

    with pytest.raises(SystemExit) as exc:
        load_config(root)
    assert exc.value.code == 2


def test_response_format_with_markup_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    root = _write_cfg(tmp_path, '[supamem.mcp]\nresponse_format = "[/bold]"\n')

    with pytest.raises(SystemExit) as exc:
        load_config(root)
    assert exc.value.code == 2
