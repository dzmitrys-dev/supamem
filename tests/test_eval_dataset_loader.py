"""Phase 10 Plan 10-01 — RED tests for the LongMemEval_S dataset loader.

The loader lives at ``supamem.eval.suite_loader`` (Plan 10-03 will create it).
These tests pin the public contract:

- HF lazy-fetch via ``huggingface_hub.snapshot_download`` (D-VEND-01)
- Pinned revision SHA module-level constant (D-VEND-02)
- ``--dataset-path`` override skips HF entirely (D-VEND-03)
- Cache layout: ``<cache_dir>/datasets/longmemeval/<sha>/``
- Records yield ``{id, question, sessions, answer, axis}``

Every test MUST FAIL today: the loader module does not exist. ``importorskip``
keeps collection green so failures surface as assertion errors, not collection
errors, once the module lands.

Per D-07: this file imports NO SaaS LLM SDK (no openai/anthropic/cohere).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest


loader_mod = pytest.importorskip("supamem.eval.suite_loader")
meta_mod = pytest.importorskip("supamem.eval.datasets.longmemeval_meta")


def test_pinned_revision_is_40_char_hex() -> None:
    """D-VEND-02: ``PINNED_REVISION`` is a 40-char hex SHA constant.

    Placeholder is acceptable in this wave (Plan 10-03 will overwrite with
    the real upstream HF revision). The shape contract is what we lock now.
    """
    rev = meta_mod.PINNED_REVISION
    assert isinstance(rev, str)
    assert re.fullmatch(r"[0-9a-f]{40}", rev), (
        f"PINNED_REVISION must be 40-char hex, got {rev!r}"
    )


def test_load_longmemeval_calls_snapshot_download_with_pinned_sha(tmp_path) -> None:
    """D-VEND-01: lazy-fetch happens via ``huggingface_hub.snapshot_download``
    with the pinned revision SHA. We mock the network call and assert it was
    invoked with the right repo + revision."""
    with patch("huggingface_hub.snapshot_download") as mock_dl:
        mock_dl.return_value = str(tmp_path / "snap")
        loader_mod.load_longmemeval(cache_dir=tmp_path, dataset_path=None)
    assert mock_dl.called, "snapshot_download must be invoked when no dataset_path"
    kwargs = mock_dl.call_args.kwargs
    # Loose contract: revision kwarg matches the pinned constant.
    assert kwargs.get("revision") == meta_mod.PINNED_REVISION
    # Cache dir is honored (D-VEND-01 layout).
    assert "cache_dir" in kwargs


def test_load_longmemeval_dataset_path_skips_hf(tmp_path) -> None:
    """D-VEND-03: when ``dataset_path`` is provided, we MUST NOT touch HF.
    Air-gapped CI mirrors depend on this skip."""
    local = tmp_path / "local"
    local.mkdir()
    with patch("huggingface_hub.snapshot_download") as mock_dl:
        list(loader_mod.load_longmemeval(cache_dir=tmp_path, dataset_path=str(local)))
    assert not mock_dl.called, (
        "snapshot_download MUST be skipped when dataset_path is provided"
    )


def test_load_longmemeval_cache_layout(tmp_path) -> None:
    """Cached path layout: ``<cache_dir>/datasets/longmemeval/<sha>/``.

    The loader either writes there itself, or passes a cache_dir matching
    that prefix to ``snapshot_download``. Either contract is acceptable;
    we assert the prefix is honored."""
    captured: dict[str, str] = {}

    def _fake(*args, **kwargs):  # noqa: ANN001 — mock signature
        captured["cache_dir"] = str(kwargs.get("cache_dir", ""))
        return captured["cache_dir"]

    with patch("huggingface_hub.snapshot_download", side_effect=_fake):
        loader_mod.load_longmemeval(cache_dir=tmp_path, dataset_path=None)

    assert "datasets" in captured["cache_dir"]
    assert "longmemeval" in captured["cache_dir"]


def test_load_longmemeval_record_shape(tmp_path) -> None:
    """Returned iterable yields records with exactly the documented keys."""
    local = tmp_path / "local"
    local.mkdir()
    # Plan 10-03 decides whether the loader reads a pre-shaped JSONL or the
    # raw HF parquet; either way, with a *real* fixture the contract holds.
    # In the RED state, this call will fail before yielding — that's the
    # whole point: the contract is locked in the test, not in absent code.
    records = list(loader_mod.load_longmemeval(cache_dir=tmp_path, dataset_path=str(local)))
    if not records:
        pytest.fail("empty fixture: at minimum one record expected from a valid dataset_path")
    for rec in records:
        for key in ("id", "question", "sessions", "answer", "axis"):
            assert key in rec, f"record missing {key!r}: {rec!r}"


# Plan 14-A — iter_haystack_chunks tests (Task A1)
# Per D-SCOPE-02: one session_id per chunk, sourced verbatim from
# raw["haystack_session_ids"][i] paired with raw["haystack_sessions"][i].

from supamem.eval.datasets import longmemeval_loader as _ll_mod  # noqa: E402


def _make_raw_record(
    question_id: str,
    axis: str,
    sessions: list,
    session_ids: list | None = None,
) -> dict:
    if session_ids is None:
        session_ids = [f"s_{i:03d}" for i in range(len(sessions))]
    return {
        "question_id": question_id,
        "question_type": axis.replace("_", "-"),
        "axis": axis,
        "question": "irrelevant for ingest",
        "answer": "irrelevant for ingest",
        "haystack_session_ids": session_ids,
        "haystack_sessions": sessions,
    }


def test_iter_haystack_chunks_yields_one_per_turn() -> None:
    rec = _make_raw_record(
        "q0",
        "single_session_user",
        sessions=[
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ],
            [
                {"role": "user", "content": "u3"},
                {"role": "assistant", "content": "a3"},
                {"role": "user", "content": "u4"},
            ],
        ],
    )
    out = list(_ll_mod.iter_haystack_chunks([rec]))
    assert len(out) == 6


def test_iter_haystack_chunks_session_id_from_haystack_session_ids() -> None:
    rec = _make_raw_record(
        "q1",
        "multi_session",
        sessions=[
            [{"role": "user", "content": "x"}],
            [{"role": "user", "content": "y"}, {"role": "assistant", "content": "z"}],
        ],
        session_ids=["sess-A", "sess-B"],
    )
    out = list(_ll_mod.iter_haystack_chunks([rec]))
    sids = [t[0] for t in out]
    assert sids == ["sess-A", "sess-B", "sess-B"]


def test_iter_haystack_chunks_text_is_role_content_join() -> None:
    rec = _make_raw_record(
        "q2",
        "knowledge_update",
        sessions=[[{"role": "user", "content": "morning routine"}]],
    )
    out = list(_ll_mod.iter_haystack_chunks([rec]))
    assert len(out) == 1
    _sid, text, _axis = out[0]
    assert "user" in text
    assert "morning routine" in text


def test_iter_haystack_chunks_carries_axis() -> None:
    rec = _make_raw_record(
        "q3",
        "temporal_reasoning",
        sessions=[
            [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        ],
    )
    out = list(_ll_mod.iter_haystack_chunks([rec]))
    assert len(out) == 2
    assert all(axis == "temporal_reasoning" for (_sid, _text, axis) in out)


def test_iter_haystack_chunks_skips_empty_sessions() -> None:
    rec = _make_raw_record(
        "q4",
        "single_session_assistant",
        sessions=[
            [],
            [{"role": "assistant", "content": "hi"}],
        ],
        session_ids=["empty", "real"],
    )
    out = list(_ll_mod.iter_haystack_chunks([rec]))
    assert len(out) == 1
    assert out[0][0] == "real"


def test_iter_haystack_chunks_handles_bundled_fixture() -> None:
    fixture = Path(_ll_mod.__file__).parent / "longmemeval_fixture.json"
    assert fixture.exists()
    raw_records = list(_ll_mod.iter_raw_longmemeval(dataset_path=str(fixture.parent)))
    assert raw_records, "bundled fixture should yield at least one raw record"
    out = list(_ll_mod.iter_haystack_chunks(raw_records))
    assert out, "expected at least one (session_id, text, axis) tuple"
    for sid, text, axis in out:
        assert isinstance(sid, str) and sid
        assert isinstance(text, str) and text
        assert isinstance(axis, str) and axis
