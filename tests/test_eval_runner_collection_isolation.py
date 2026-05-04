"""Phase 14 Plan A Task A3 — bench-collection override + byte-identical
goldens-path lock.

Two parts:

1. ``_build_backend`` swaps ``cfg.collection`` to the isolated bench
   prefix (``supamem_eval_longmemeval_s``) when ``suite='longmemeval_s'``,
   does NOT override for ``goldens``, and does NOT mutate the caller's
   cfg (D-SCOPE-05).
2. The legacy ``_run_goldens_legacy`` function source is byte-identical
   to the snapshot captured at the start of Plan A (D-VEND-04 lock).
   Updating the snapshot requires an explicit decision-record in
   CONTEXT.md.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig

import supamem.eval.runner as runner_mod


# ---------------------------------------------------------------------------
# Helpers


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "user_project"}
    base.update(overrides)
    return ResolvedConfig(**base)


# ---------------------------------------------------------------------------
# 1. Override-seam tests


def test_build_backend_uses_bench_collection_for_longmemeval_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When suite='longmemeval_s', the backend's resolved cfg.collection is
    the isolated bench prefix, NOT the caller's cfg.collection."""
    captured_cfgs: list[ResolvedConfig] = []

    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            captured_cfgs.append(config)

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    runner_mod._build_backend(cfg, suite="longmemeval_s")

    assert len(captured_cfgs) == 1
    assert captured_cfgs[0].collection == "supamem_eval_longmemeval_s", (
        f"expected bench prefix, got {captured_cfgs[0].collection!r}"
    )


def test_build_backend_does_not_override_for_goldens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goldens path: cfg.collection stays as the caller's original."""
    captured_cfgs: list[ResolvedConfig] = []

    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            captured_cfgs.append(config)

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    runner_mod._build_backend(cfg, suite="goldens")
    runner_mod._build_backend(cfg)  # no suite arg → goldens-equivalent default

    for got in captured_cfgs:
        assert got.collection == "user_project"


def test_build_backend_does_not_mutate_caller_cfg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller's cfg.collection unchanged after _build_backend returns."""
    class FakeBackend:
        def __init__(self, *, config: ResolvedConfig) -> None:
            pass

    monkeypatch.setattr(runner_mod, "TunedHybridBackend", FakeBackend)

    cfg = _cfg(collection="user_project")
    original = cfg.collection
    runner_mod._build_backend(cfg, suite="longmemeval_s")
    assert cfg.collection == original


# ---------------------------------------------------------------------------
# 2. byte-identical _run_goldens_legacy lock (D-VEND-04)


def _load_snapshot() -> dict:
    path = Path(__file__).parent / "fixtures" / "run_goldens_legacy_snapshot.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_goldens_legacy_byte_identical_at_157() -> None:
    """The function body of ``_run_goldens_legacy`` MUST match the
    snapshot captured at the start of Plan A. This is the D-VEND-04
    byte-identical regression lock — updating the snapshot requires an
    explicit decision-record in CONTEXT.md.
    """
    snap = _load_snapshot()
    runner_path = Path(runner_mod.__file__)
    src = runner_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_goldens_legacy":
            target = node
            break
    assert target is not None, "_run_goldens_legacy not found"

    body = ast.get_source_segment(src, target)
    assert body is not None
    actual_sha = hashlib.sha256(body.encode()).hexdigest()
    assert actual_sha == snap["sha256"], (
        f"_run_goldens_legacy drift detected!\n"
        f"  expected sha256: {snap['sha256']}\n"
        f"  actual sha256:   {actual_sha}\n"
        f"  expected len:    {snap['byte_len']}\n"
        f"  actual len:      {len(body)}\n"
        f"D-VEND-04 forbids non-trivial edits to the goldens path. "
        f"Update tests/fixtures/run_goldens_legacy_snapshot.json only "
        f"after recording the decision in CONTEXT.md."
    )
    # Also verify the documented line range survives — Plan B's
    # complementary diff test will assert on this too.
    assert target.lineno == snap["startline"]
    assert target.end_lineno == snap["endline"]
