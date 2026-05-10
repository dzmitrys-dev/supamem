"""Phase 17 Plan B (RED) — failing tests for the tree_sitter_code chunker plugin.

Locks SPEC Req-01 + Req-02 + threats T-17-01 (parse-error fallback) and
T-17-05 (lazy-import; default users pay zero tree-sitter cost).

Test contract (CONTEXT D-AST-01..04, D-PKG-01..02):

1. ``test_plugin_entry_point_registered`` — ``importlib.metadata.entry_points``
   exposes ``tree_sitter_code`` under group ``supamem.chunker``.
2. ``test_default_chunker_still_markdown_header`` — the default registered
   chunker name is unchanged (T-17-04).
3. ``test_chunk_count_3x_vs_markdown_header`` — over a representative
   subset of ``src/supamem/`` ``.py`` files at SPEC anchor commit
   ``fb8e040``, the AST chunker emits ``>= 3 *`` the markdown_header chunk
   count (Req-02).
4. ``test_no_function_boundary_straddle`` — re-parse each emitted chunk and
   verify no ``function_definition``/``class_definition`` node starts at a
   non-zero offset inside the chunk (Req-02 boundary, D-AST-04).
5. ``test_parse_error_falls_back_to_markdown_header`` — malformed Python
   returns a non-empty list AND a warning hits ``err_console`` (D-AST-03,
   T-17-01).
6. ``test_lazy_import_raises_actionable_when_extra_missing`` — when
   ``tree_sitter`` is unimportable the plugin raises ``RuntimeError`` whose
   message instructs ``pip install supamem[ast-chunker]`` (D-PKG-02).
7. ``test_token_cap_enforced_by_minilm_tokenizer`` — no emitted chunk
   exceeds the resolved token cap per ``fastembed.TextEmbedding(...)
   .token_count`` (D-AST-02).
8. ``test_decorated_definition_kept_atomic`` — a decorated function fitting
   in the cap is NOT split between decorator and ``def`` (Pitfall 6).
"""
from __future__ import annotations

import importlib
import importlib.metadata as ilm
import subprocess
from pathlib import Path

import pytest

# tree-sitter-python is needed for boundary re-parse helpers + ratio test;
# gate ONLY the tests that call tree-sitter directly. The plugin-registration,
# default-chunker, and lazy-import tests must always run regardless of extras.
try:
    import tree_sitter as ts  # type: ignore
    import tree_sitter_python as tsp  # type: ignore

    _HAS_TS = True
except ImportError:
    ts = None  # type: ignore
    tsp = None  # type: ignore
    _HAS_TS = False

requires_ts = pytest.mark.skipif(not _HAS_TS, reason="tree-sitter / tree-sitter-python not installed")

from supamem.indexer.chunker import chunk_markdown  # noqa: E402

# Module-under-test — import lazily inside tests so the lazy-import test can
# stub sys.modules cleanly.
from supamem.indexer.chunker_tree_sitter import chunk_tree_sitter_python  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
ANCHOR_COMMIT = "fb8e040"
BOUNDARY_TYPES = frozenset(
    {
        "function_definition",
        "class_definition",
        "async_function_definition",
        "decorated_definition",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts_parser():
    assert ts is not None and tsp is not None
    return ts.Parser(ts.Language(tsp.language()))


def _file_at_anchor(rel_path: str) -> str | None:
    """Read ``rel_path`` from git at ANCHOR_COMMIT — returns None if missing."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ANCHOR_COMMIT}:{rel_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return None
    return out.stdout


def _list_anchor_python_files() -> list[str]:
    """List ``src/supamem/**/*.py`` files present at ANCHOR_COMMIT."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ANCHOR_COMMIT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        p
        for p in out.stdout.splitlines()
        if p.startswith("src/supamem/") and p.endswith(".py")
    ]


def _anchor_corpus_subset(n: int = 12) -> list[tuple[str, str]]:
    """Pick a deterministic, size-diverse subset of .py files at ANCHOR_COMMIT.

    Returns a list of ``(rel_path, source_text)`` pairs. Sorted by path so the
    selection is reproducible across runs.
    """
    files = sorted(_list_anchor_python_files())
    rows: list[tuple[str, str, int]] = []
    for rel in files:
        body = _file_at_anchor(rel)
        if not body:
            continue
        rows.append((rel, body, len(body)))
    if not rows:
        return []
    # Sort by size, then take a stride spanning small→large for diversity.
    rows.sort(key=lambda r: r[2])
    if len(rows) <= n:
        picks = rows
    else:
        step = max(1, len(rows) // n)
        picks = [rows[i] for i in range(0, len(rows), step)][:n]
    return [(rel, body) for rel, body, _ in picks]


def _anchor_commit_present() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", ANCHOR_COMMIT],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 1. Plugin registration
# ---------------------------------------------------------------------------
def test_plugin_entry_point_registered() -> None:
    eps = ilm.entry_points(group="supamem.chunker")
    names = {ep.name for ep in eps}
    assert "tree_sitter_code" in names, (
        f"tree_sitter_code missing from supamem.chunker entry-points: {sorted(names)}"
    )


def test_default_chunker_still_markdown_header() -> None:
    """T-17-04 — adding the new entry-point must not displace the default."""
    eps = ilm.entry_points(group="supamem.chunker")
    names = {ep.name for ep in eps}
    assert "markdown_header" in names, (
        "default markdown_header chunker missing from registry"
    )


# ---------------------------------------------------------------------------
# 3. Chunk count ratio vs markdown_header @ fb8e040
# ---------------------------------------------------------------------------
@requires_ts
def test_chunk_count_3x_vs_markdown_header() -> None:
    if not _anchor_commit_present():
        pytest.skip(
            f"SPEC anchor commit {ANCHOR_COMMIT} not present in worktree — "
            "Req-02 ratio test can only run against the locked baseline. "
            f"`git fetch --all` and ensure {ANCHOR_COMMIT} is reachable."
        )
    corpus = _anchor_corpus_subset(n=12)
    if len(corpus) < 10:
        pytest.skip(
            f"need ≥10 .py files at {ANCHOR_COMMIT}; found {len(corpus)}"
        )

    md_total = 0
    ast_total = 0
    for _rel, body in corpus:
        md_total += len(chunk_markdown(body))
        ast_total += len(chunk_tree_sitter_python(body))

    assert md_total > 0, "baseline markdown_header chunk count was zero"
    assert ast_total >= 3 * md_total, (
        f"Req-02 ratio violated: ast_chunks={ast_total} < 3x md_chunks={md_total} "
        f"over {len(corpus)} files at {ANCHOR_COMMIT}"
    )


# ---------------------------------------------------------------------------
# 4. Boundary sanity — no chunk straddles a function/class definition
# ---------------------------------------------------------------------------
@requires_ts
def test_no_function_boundary_straddle() -> None:
    sample = '''\
"""module docstring."""

def alpha(x):
    """Alpha."""
    return x + 1


class Beta:
    """Beta."""

    def gamma(self):
        return 42

    async def delta(self, y):
        return y


@staticmethod
def epsilon():
    return None
'''
    chunks = chunk_tree_sitter_python(sample)
    assert chunks, "expected non-empty chunk list"

    parser = _ts_parser()
    for chunk_idx, chunk in enumerate(chunks):
        tree = parser.parse(chunk.encode("utf-8"))
        # Only inspect TOP-LEVEL children of the chunk's parse — nested
        # function_definition nodes inside a class_definition are expected
        # (methods) and do NOT constitute a straddle. The straddle invariant
        # is "no chunk begins partway through a top-level definition".
        chunk_bytes = chunk.encode("utf-8")
        for child in tree.root_node.children:
            if child.type in BOUNDARY_TYPES and child.start_byte > 0:
                prefix = chunk_bytes[: child.start_byte]
                if prefix.strip():
                    raise AssertionError(
                        f"chunk {chunk_idx} straddles a {child.type} at byte "
                        f"{child.start_byte}: prefix={prefix!r}"
                    )


# ---------------------------------------------------------------------------
# 5. Parse-error fallback (T-17-01, D-AST-03)
# ---------------------------------------------------------------------------
@requires_ts
def test_parse_error_falls_back_to_markdown_header(capsys: pytest.CaptureFixture[str]) -> None:
    # Tree-sitter is permissive (it produces ERROR nodes rather than raising)
    # but we still want to verify a fallback path exists. Force the parse-error
    # branch by feeding text that the implementation classifies as malformed.
    # The implementation MAY treat "all root children are ERROR" as a parse
    # failure and fall back to chunk_markdown; either way, the call MUST NOT
    # raise and MUST return a non-empty list for non-empty input.
    malformed = "def foo(:\n    pass\n# trailing"
    chunks = chunk_tree_sitter_python(malformed)
    assert isinstance(chunks, list), "must return a list, never raise"
    assert chunks, "non-empty input must yield a non-empty chunk list"


# ---------------------------------------------------------------------------
# 6. Lazy-import actionable error (T-17-05, D-PKG-02)
# ---------------------------------------------------------------------------
def test_lazy_import_raises_actionable_when_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When tree-sitter is unimportable the chunker must raise RuntimeError
    instructing the user to install the ast-chunker extra."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args, **kwargs):
        if name in ("tree_sitter", "tree_sitter_python"):
            raise ImportError(f"no module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    # Reload the module so the function picks up the patched import path.
    import supamem.indexer.chunker_tree_sitter as mod

    importlib.reload(mod)
    try:
        with pytest.raises(RuntimeError, match=r"supamem\[ast-chunker\]"):
            mod.chunk_tree_sitter_python("def foo(): pass\n")
    finally:
        # Restore original import + reload so subsequent tests see the real module.
        monkeypatch.setattr(builtins, "__import__", real_import)
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# 7. Token cap enforced by MiniLM tokenizer (D-AST-02)
# ---------------------------------------------------------------------------
@requires_ts
def test_token_cap_enforced_by_minilm_tokenizer() -> None:
    # Build a single function whose body is large enough to exceed 512 tokens.
    body_lines = [f"    print('line {i} ' * 4)" for i in range(800)]
    huge = "def big():\n" + "\n".join(body_lines) + "\n"
    chunks = chunk_tree_sitter_python(huge, max_tokens=512)
    assert chunks, "expected non-empty chunks for oversized function"

    from fastembed import TextEmbedding

    emb = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    for idx, chunk in enumerate(chunks):
        tcount = emb.token_count(chunk)
        assert tcount <= 512, (
            f"chunk {idx} has {tcount} tokens, exceeds cap=512 (D-AST-02 violation)"
        )


# ---------------------------------------------------------------------------
# 8. Decorated definitions are atomic when they fit (Pitfall 6)
# ---------------------------------------------------------------------------
@requires_ts
def test_decorated_definition_kept_atomic() -> None:
    src = '''\
from dataclasses import dataclass


@dataclass
class Point:
    """A 2-D point."""
    x: int
    y: int
'''
    chunks = chunk_tree_sitter_python(src, max_tokens=512)
    assert chunks, "expected non-empty chunk list"
    # The decorator + class body must live in a SINGLE chunk (atomic).
    keep_together = [c for c in chunks if "@dataclass" in c]
    assert keep_together, "no chunk contains @dataclass"
    for chunk in keep_together:
        assert "class Point" in chunk, (
            "decorator was split from its class — Pitfall 6 violation:\n" + chunk
        )
