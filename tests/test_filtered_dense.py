"""Tests for ``supamem.retrieval.filtered_dense.FilteredDenseBackend`` (Phase 11 FILT-01/02).

Covers:

- D-FD-01..02 — composition (wraps TunedHybridBackend, never subclasses) and
  delegation of ``where=`` through to ``self._inner.query``.
- D-PREV-01..04 — backend-level ``RetrievedChunk.preview`` cap. Truncation
  rule mirrors ``mcp_server.py:227`` byte-for-byte
  (``text[: max(0, cap - 1)] + "…"``); ``preview_chars=0`` disables
  truncation; default cap is 240.
- Frozen-instance discipline — ``RetrievedChunk(model_config=frozen=True)``
  forbids mutation; the wrapper MUST use ``model_copy(update=...)`` so the
  inner backend's chunks are never mutated by reference (regression lock).
- Pitfall 7 (RESEARCH-A §5) — ``mcp_server.py:218-238`` MUST keep reading
  ``h.text`` for transport-cap truncation, NEVER ``h.preview``. Both caps
  sit on the SAME RAW INPUT (``h.text``) — never composed. Re-routing the
  MCP server to read ``h.preview`` would create the double-ellipsis bug.
- Plugin/entry-point + config wiring tests live alongside Task C3 (this
  file) so the round-trip ``config.toml → ResolvedConfig → FilteredDenseBackend``
  is exercised end-to-end.
"""
from __future__ import annotations

import importlib.metadata as _md
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from supamem.config import ResolvedConfig
from supamem.retrieval.types import RetrievedChunk


def _cfg(**overrides: Any) -> ResolvedConfig:
    base: dict[str, Any] = {
        "qdrant_url": "http://localhost:6333",
        "collection": "test_filtered_dense",
    }
    base.update(overrides)
    return ResolvedConfig(**base)


def _hit(id_: str = "h1", text: str = "hello world", score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(id=id_, text=text, score=score, source_path="docs/x.md")


# ────────────────────────────────────────────────────────────────────────
# D-FD-01..02 — composition + delegation
# ────────────────────────────────────────────────────────────────────────


def test_query_delegates_to_inner_with_where() -> None:
    """FilteredDenseBackend.query delegates verbatim to TunedHybridBackend.query."""
    from supamem.retrieval import filtered_dense as mod

    fake_inner = MagicMock()
    fake_inner.query.return_value = [_hit()]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(config=_cfg())
        backend.query("q", k=3, where={"room": "backend"})

    fake_inner.query.assert_called_once_with("q", 3, where={"room": "backend"})


def test_class_name_attribute() -> None:
    from supamem.retrieval.filtered_dense import FilteredDenseBackend

    assert FilteredDenseBackend.name == "filtered_dense"
    assert FilteredDenseBackend.kind() == "filtered_dense"


# ────────────────────────────────────────────────────────────────────────
# D-PREV-01..04 — preview cap behavior
# ────────────────────────────────────────────────────────────────────────


def test_preview_truncates_at_cap() -> None:
    """preview_chars=10 + 16-char text → 9 chars + ellipsis (mirrors mcp_server.py:227)."""
    from supamem.retrieval import filtered_dense as mod

    long = "abcdefghijklmnop"  # 16 chars
    fake_inner = MagicMock()
    fake_inner.query.return_value = [_hit(text=long)]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(
            config=_cfg(retrieval_filtered_dense_preview_chars=10)
        )
        out = backend.query("q", k=1)

    assert len(out) == 1
    assert out[0].preview == "abcdefghi…"
    assert len(out[0].preview) == 10


def test_preview_passthrough_when_under_cap() -> None:
    from supamem.retrieval import filtered_dense as mod

    fake_inner = MagicMock()
    fake_inner.query.return_value = [_hit(text="short")]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(
            config=_cfg(retrieval_filtered_dense_preview_chars=240)
        )
        out = backend.query("q", k=1)

    assert out[0].preview == "short"


def test_preview_chars_zero_disables_truncation() -> None:
    """cap=0 → preview is the full text (no ellipsis), regardless of length."""
    from supamem.retrieval import filtered_dense as mod

    huge = "x" * 1000
    fake_inner = MagicMock()
    fake_inner.query.return_value = [_hit(text=huge)]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(
            config=_cfg(retrieval_filtered_dense_preview_chars=0)
        )
        out = backend.query("q", k=1)

    assert out[0].preview == huge


def test_preview_chars_default_is_240() -> None:
    """ResolvedConfig() default carries 240 into the backend's preview_chars."""
    from supamem.retrieval import filtered_dense as mod

    fake_inner = MagicMock()
    fake_inner.query.return_value = [_hit(text="x" * 250)]  # > default

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(config=_cfg())
        out = backend.query("q", k=1)

    # 250 > 240 → preview = 239 'x's + '…'
    assert len(out[0].preview) == 240
    assert out[0].preview.endswith("…")


# ────────────────────────────────────────────────────────────────────────
# Frozen-instance discipline
# ────────────────────────────────────────────────────────────────────────


def test_returned_chunks_are_new_instances_not_mutated() -> None:
    """model_copy creates a NEW frozen instance; identity differs from inner's hit."""
    from supamem.retrieval import filtered_dense as mod

    inner_hit = _hit(text="hello world")
    fake_inner = MagicMock()
    fake_inner.query.return_value = [inner_hit]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(config=_cfg())
        out = backend.query("q", k=1)

    assert out[0] is not inner_hit  # new instance via model_copy
    assert out[0].id == inner_hit.id  # same logical content
    assert out[0].preview is not None
    assert inner_hit.preview is None  # original untouched


def test_inner_chunks_unmodified() -> None:
    """frozen=True means we cannot mutate inner_hit even if we wanted to —
    confirm the wrapper's model_copy path leaves inner_hit.preview at None."""
    from supamem.retrieval import filtered_dense as mod

    inner_hit = _hit(text="abc")
    fake_inner = MagicMock()
    fake_inner.query.return_value = [inner_hit]

    with patch.object(mod, "TunedHybridBackend", return_value=fake_inner):
        backend = mod.FilteredDenseBackend(
            config=_cfg(retrieval_filtered_dense_preview_chars=2)
        )
        _ = backend.query("q", k=1)

    assert inner_hit.preview is None  # preview never mutated on the original


# ────────────────────────────────────────────────────────────────────────
# Pitfall 7 (RESEARCH-A §5) — anti-edit lock on mcp_server.py:218-238
# ────────────────────────────────────────────────────────────────────────


def test_pitfall_7_mcp_server_anti_edit() -> None:
    """mcp_server.py truncation zone (lines 218-238) MUST read h.text, NOT h.preview.

    Re-routing the MCP server to read h.preview creates the double-ellipsis bug
    (backend cap + transport cap both ellipsizing the same source).
    """
    import supamem.mcp_server as ms

    src_path = Path(ms.__file__)
    lines = src_path.read_text(encoding="utf-8").splitlines()
    zone = "\n".join(lines[217:238])  # 0-indexed slice for lines 218-238

    assert "h.text" in zone, (
        "Pitfall 7 anti-regression: mcp_server.py:218-238 must read h.text "
        "for transport-cap truncation"
    )
    assert "h.preview" not in zone, (
        "Pitfall 7 violation: mcp_server.py:218-238 must NOT read h.preview "
        "(double-ellipsis bug)"
    )


# ────────────────────────────────────────────────────────────────────────
# Task C3 — config field + nested-table mapping + entry-point
# ────────────────────────────────────────────────────────────────────────


def test_resolved_config_has_preview_chars_default_240() -> None:
    cfg = ResolvedConfig()
    assert cfg.retrieval_filtered_dense_preview_chars == 240


def test_load_config_reads_nested_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.config import load_config

    proj = tmp_path / "proj"
    (proj / ".supamem").mkdir(parents=True)
    (proj / ".supamem" / "config.toml").write_text(
        textwrap.dedent(
            """
            [supamem.retrieval.filtered_dense]
            preview_chars = 80
            """
        ).strip()
    )
    # Ensure no env override interferes.
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)

    cfg, _ = load_config(cwd=proj)
    assert cfg.retrieval_filtered_dense_preview_chars == 80


def test_load_config_rejects_negative_preview_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supamem.config import load_config

    proj = tmp_path / "proj"
    (proj / ".supamem").mkdir(parents=True)
    (proj / ".supamem" / "config.toml").write_text(
        textwrap.dedent(
            """
            [supamem.retrieval.filtered_dense]
            preview_chars = -1
            """
        ).strip()
    )
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)

    with pytest.raises(SystemExit):
        load_config(cwd=proj)


def test_load_config_accepts_zero_preview_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supamem.config import load_config

    proj = tmp_path / "proj"
    (proj / ".supamem").mkdir(parents=True)
    (proj / ".supamem" / "config.toml").write_text(
        textwrap.dedent(
            """
            [supamem.retrieval.filtered_dense]
            preview_chars = 0
            """
        ).strip()
    )
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)

    cfg, _ = load_config(cwd=proj)
    assert cfg.retrieval_filtered_dense_preview_chars == 0


def test_entry_point_resolves_filtered_dense() -> None:
    """``supamem.retrieval`` group must register ``filtered_dense``."""
    eps = _md.entry_points(group="supamem.retrieval")
    names = {ep.name for ep in eps}
    assert "filtered_dense" in names

    # And it loads to the FilteredDenseBackend class.
    from supamem.retrieval.filtered_dense import FilteredDenseBackend

    target = next(ep for ep in eps if ep.name == "filtered_dense")
    assert target.load() is FilteredDenseBackend
