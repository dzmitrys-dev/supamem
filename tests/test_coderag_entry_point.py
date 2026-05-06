"""Phase 15 Plan A Task A1 — RED tests for the supamem.eval entry-point group
and the coderag sub-package skeleton.

Locks:
- Entry-point group ``supamem.eval`` registered with a single ``coderag`` entry
  resolving to ``supamem.eval.coderag:CodeRAGSuite``.
- Sub-package ``supamem.eval.coderag`` imports cleanly with stub bodies.
- ``ingest.py`` imports ZERO symbols from ``supamem.indexer.*`` — eval-isolation
  carry-lock from Phase 14 D-SCOPE-05.
- ``pyproject.toml`` carries ``pytrec_eval>=0.5`` in eval extras and ``peers-mem0``
  optional-dependencies key with ``mem0ai>=2.0,<3.0``.
"""
from __future__ import annotations

import ast
from importlib.metadata import entry_points
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
INGEST_PY = REPO_ROOT / "src" / "supamem" / "eval" / "coderag" / "ingest.py"


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Entry-point group registration


def test_coderag_entry_point_registered() -> None:
    eps = list(entry_points(group="supamem.eval"))
    coderag_eps = [e for e in eps if e.name == "coderag"]
    assert len(coderag_eps) == 1, f"expected exactly one 'coderag' entry; got {eps!r}"
    assert coderag_eps[0].value == "supamem.eval.coderag:CodeRAGSuite", (
        f"unexpected entry-point target: {coderag_eps[0].value!r}"
    )


def test_coderag_entry_point_resolves() -> None:
    (ep,) = (e for e in entry_points(group="supamem.eval") if e.name == "coderag")
    obj = ep.load()
    assert isinstance(obj, type), "entry-point must resolve to a class"
    assert obj.__name__ == "CodeRAGSuite"


# ---------------------------------------------------------------------------
# Sub-package imports


def test_coderag_subpackage_imports() -> None:
    import supamem.eval.coderag  # noqa: F401
    import supamem.eval.coderag.ingest  # noqa: F401
    import supamem.eval.coderag.metrics  # noqa: F401
    import supamem.eval.coderag.report  # noqa: F401
    import supamem.eval.coderag.runner  # noqa: F401


def test_coderag_runner_has_run_function() -> None:
    from supamem.eval.coderag import runner

    assert callable(getattr(runner, "_run_coderag", None)), (
        "supamem.eval.coderag.runner._run_coderag must be callable"
    )


# ---------------------------------------------------------------------------
# Eval-isolation lock — D-SCOPE-05 carry


def test_coderag_ingest_no_indexer_imports() -> None:
    """ingest.py MUST NOT import any symbol from supamem.indexer.*."""
    tree = ast.parse(INGEST_PY.read_text(encoding="utf-8"))
    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "supamem.indexer" or mod.startswith("supamem.indexer."):
                offending.append(f"from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "supamem.indexer" or alias.name.startswith(
                    "supamem.indexer."
                ):
                    offending.append(f"import {alias.name}")
    assert not offending, (
        "supamem.eval.coderag.ingest MUST NOT import supamem.indexer.* "
        f"(D-SCOPE-05 carry-lock); offending: {offending!r}"
    )


# ---------------------------------------------------------------------------
# pyproject.toml shape


def test_pyproject_has_pytrec_eval_in_eval_extras() -> None:
    data = _load_pyproject()
    eval_extras = data["project"]["optional-dependencies"]["eval"]
    assert any(s.replace(" ", "").startswith("pytrec_eval>=0.5") for s in eval_extras), (
        f"pytrec_eval>=0.5 missing from eval extras: {eval_extras!r}"
    )


def test_pyproject_has_peers_mem0_extras() -> None:
    data = _load_pyproject()
    extras = data["project"]["optional-dependencies"]
    assert "peers-mem0" in extras, f"peers-mem0 missing from optional-dependencies: {list(extras)!r}"
    peers = extras["peers-mem0"]
    assert any("mem0ai" in s and ">=2.0" in s and "<3.0" in s for s in peers), (
        f"peers-mem0 must contain mem0ai>=2.0,<3.0; got {peers!r}"
    )


def test_pyproject_has_supamem_eval_entry_point_group() -> None:
    data = _load_pyproject()
    eps = data["project"]["entry-points"]
    assert "supamem.eval" in eps, (
        f"[project.entry-points.\"supamem.eval\"] missing; got groups {list(eps)!r}"
    )
    assert eps["supamem.eval"].get("coderag") == "supamem.eval.coderag:CodeRAGSuite", (
        f"unexpected entry-point mapping: {eps['supamem.eval']!r}"
    )
