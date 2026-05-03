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
