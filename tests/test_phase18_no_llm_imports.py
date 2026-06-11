"""Phase 18 Req-02 — no LLM provider imports on borrow modules.

Scans allowlisted Phase 18 modules for forbidden provider client imports.
Extend ``PHASE18_MODULES`` as Plans F–H land adaptive depth, dedup, autotune, etc.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

# Updated as Phase 18 borrow modules ship (Plans F–H).
PHASE18_MODULES: tuple[str, ...] = (
    "supamem.qdrant_collection",
    "supamem.retrieval.tuned_hybrid",
    "supamem.memory_writer",
    "supamem.indexer",
    "supamem.eval.coderag.gate",
    "supamem.eval.autotune",
)

_FORBIDDEN_IMPORT_ROOTS = frozenset({"openai", "anthropic", "ollama", "litellm"})


def _module_source_path(module_name: str) -> Path:
    mod = importlib.import_module(module_name)
    source_file = inspect.getsourcefile(mod)
    assert source_file is not None, f"no source file for {module_name}"
    return Path(source_file)


def _forbidden_roots_in_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0].lower()
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = node.module.split(".", 1)[0].lower()
            if root in _FORBIDDEN_IMPORT_ROOTS:
                hits.append(node.module)
    return hits


@pytest.mark.parametrize("module_name", PHASE18_MODULES)
def test_phase18_module_has_no_llm_provider_imports(module_name: str) -> None:
    path = _module_source_path(module_name)
    source = path.read_text(encoding="utf-8")
    hits = _forbidden_roots_in_imports(source)
    assert not hits, (
        f"{module_name} ({path}) must not import LLM providers on Phase 18 borrow paths; "
        f"found: {hits}"
    )
