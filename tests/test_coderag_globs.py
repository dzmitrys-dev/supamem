"""Phase 15 Plan B Task B1 — exclude/allowlist glob tests (parametrized)."""
from __future__ import annotations

import pytest

from supamem.eval.coderag.corpus import is_excluded, is_allowlisted_extension


@pytest.mark.parametrize(
    "path",
    [
        "tests/foo.py",
        "src/tests/x.py",
        "pkg/test_y.py",
        "pkg/y_test.py",
        "package.lock",
        "src/sub/something.lock",
        "__pycache__/x.pyc",
        "src/__pycache__/x.pyc",
        "dist/x.py",
        "build/x.py",
        "pkg/foo.generated.py",
        ".planning/x.md",
        ".gsd/x.md",
    ],
)
def test_excluded_paths(path):
    assert is_excluded(path), f"expected excluded: {path}"


@pytest.mark.parametrize(
    "path",
    [
        "src/foo.py",
        "src/sub/bar.py",
        "docs/adr/0001-x.md",
        "README.md",
        "llms.txt",
    ],
)
def test_included_paths(path):
    assert not is_excluded(path), f"expected NOT excluded: {path}"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/foo.py", True),
        ("src/foo.ts", True),
        ("src/foo.md", True),
        ("README.md", True),
        ("llms.txt", True),
        ("src/foo.json", False),
        ("src/foo.toml", False),
        ("src/foo.png", False),
    ],
)
def test_allowlisted_extensions(path, expected):
    assert is_allowlisted_extension(path) is expected
