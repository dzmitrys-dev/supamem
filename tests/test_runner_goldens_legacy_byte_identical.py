"""Phase 14 Plan B Task B1 — defense-in-depth byte-identical lock for
``_run_goldens_legacy``.

Plan A captured the function-body sha256 in
``tests/fixtures/run_goldens_legacy_snapshot.json`` (sha256
``44b5b281…``, byte_len 3183). Plan B's runner edits restructure the
per-record loop in ``_run_longmemeval`` heavily; this test asserts the
*sibling* legacy function body is unchanged for the rest of Phase 14.

The single source of truth for the lock is the snapshot JSON — updating
it requires an explicit decision-record in CONTEXT.md (D-VEND-04).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import supamem.eval.runner as runner_mod


_SNAPSHOT_PATH = (
    Path(__file__).parent / "fixtures" / "run_goldens_legacy_snapshot.json"
)


def _load_snapshot() -> dict:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _extract_function_source(name: str) -> str:
    """Return the AST source segment for the named top-level function."""
    runner_path = Path(runner_mod.__file__)
    src = runner_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = ast.get_source_segment(src, node)
            assert body is not None, f"no source segment for {name!r}"
            return body
    raise AssertionError(f"function {name!r} not found in runner.py")


def test_run_goldens_legacy_function_source_unchanged() -> None:
    """The body sha256 of ``_run_goldens_legacy`` must equal the Plan A
    snapshot. This is the canonical D-VEND-04 lock for Phase 14."""
    snap = _load_snapshot()
    body = _extract_function_source("_run_goldens_legacy")
    actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
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


def test_run_goldens_legacy_function_byte_len_unchanged() -> None:
    """Defense-in-depth: complement the sha256 check with byte-length."""
    snap = _load_snapshot()
    body = _extract_function_source("_run_goldens_legacy")
    assert len(body) == snap["byte_len"], (
        f"byte length drift: expected {snap['byte_len']}, got {len(body)}"
    )


def test_filters_dispatcher_byte_identical() -> None:
    """``retrieval/filters.py`` MUST NOT be modified by Phase 14 (D-SCOPE-03).

    ``session_id`` is not a magic key — it flows through Phase 11's existing
    pass-through loop. This test pins a sha256 of the file at the start of
    Phase 14 Plan B; if the file changes, this test fails until the snapshot
    is bumped (with a CONTEXT.md decision record).
    """
    import supamem.retrieval.filters as filters_mod

    expected_sha_path = (
        Path(__file__).parent / "fixtures" / "filters_py_phase14_lock.sha256"
    )
    body = Path(filters_mod.__file__).read_text(encoding="utf-8")
    actual_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if not expected_sha_path.exists():
        # First-run capture: write the snapshot so future runs detect drift.
        expected_sha_path.write_text(actual_sha + "\n", encoding="utf-8")
    expected_sha = expected_sha_path.read_text(encoding="utf-8").strip()
    assert actual_sha == expected_sha, (
        f"retrieval/filters.py drift detected!\n"
        f"  expected sha256: {expected_sha}\n"
        f"  actual sha256:   {actual_sha}\n"
        f"D-SCOPE-03 forbids modification of filters.py during Phase 14. "
        f"session_id flows through the existing pass-through path."
    )
