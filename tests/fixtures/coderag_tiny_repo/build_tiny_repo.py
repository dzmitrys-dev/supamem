"""Build a deterministic tiny git repo for offline coderag testing.

Creates ~10 commits in a tmp_path, with 2 ADRs and a mix of source/test/lock files.
Used by ``tests/conftest.py::tiny_repo`` fixture.

Phase 15 Plan B Task B1 — fixture builder for pinned-SHA fetch + auto-query
extractor tests. Bytes here are stable across machines (we set committer
identity and date deterministically).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Deterministic content/order — DO NOT reorder; tests rely on commit count.
_PLAN: tuple[tuple[str, str], ...] = (
    ("src/foo.py", "def foo():\n    return 1\n"),
    ("src/bar.py", "def bar():\n    return 2\n"),
    # Excluded paths — must not appear in any gold-set:
    ("tests/test_foo.py", "def test_foo():\n    assert True\n"),
    ("package.lock", "{}\n"),
    ("dist/foo.generated.py", "# generated file\n"),
    # Top-level docs:
    ("README.md", "# tiny\n\nA tiny test repo.\n"),
    # ADR 0001: cites src/foo.py via backticked relative path:
    (
        "docs/adr/0001-pick-foo.md",
        "# 0001 — Pick foo\n\n## Problem\n\nWe need foo.\n\n"
        "## Decision\n\nUse `src/foo.py` for the impl.\n",
    ),
    # ADR 0002: cites src/bar.py and README.md; also has a prose ".py" mention
    # (no slash) under Decision to verify Pitfall 4 anchor — that mention must
    # NOT be picked up as a path.
    (
        "docs/adr/0002-pick-bar.md",
        "# 0002 — Pick bar\n\n## Why\n\nWe need bar too.\n\n"
        "## Decision\n\nSee `src/bar.py` and `README.md`. "
        "(Note: any .py file works, but cite the canonical one.)\n",
    ),
    ("CHANGELOG.md", "# changelog\n"),
    ("llms.txt", "# llms\n"),
)


def build(dest: Path) -> Path:
    """Initialize a deterministic tiny git repo at ``dest`` and return it."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.test",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(dest)], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(dest), "config", "commit.gpgsign", "false"],
        check=True, env=env,
    )
    subprocess.run(
        ["git", "-C", str(dest), "config", "user.name", "Test"],
        check=True, env=env,
    )
    subprocess.run(
        ["git", "-C", str(dest), "config", "user.email", "t@t.test"],
        check=True, env=env,
    )
    for i, (rel_path, content) in enumerate(_PLAN):
        full = dest / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(dest), "add", rel_path], check=True, env=env,
        )
        subprocess.run(
            ["git", "-C", str(dest), "commit", "-q", "-m", f"commit {i:02d}: {rel_path}"],
            check=True, env=env,
        )
    return dest


if __name__ == "__main__":  # pragma: no cover — manual invocation only
    import sys

    build(Path(sys.argv[1]))
