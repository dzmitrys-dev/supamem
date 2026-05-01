"""Tests for the path-component classifier helper (Plan 07-01).

Behaviors locked to Phase 7 CONTEXT.md decisions D-01 / D-01a / D-11 / D-12 /
D-14 — first-match-wins by ``rooms`` insertion order, exact path-component
equality (no substring matching), pure function with zero I/O.

Cross-references:
- ``.planning/phases/07-coding-path-classifier/07-CONTEXT.md`` D-01..D-15
- ``.planning/phases/07-coding-path-classifier/07-RESEARCH.md`` Pattern 1,
  Pitfall 6 (Path.parts on absolute paths)
"""
from __future__ import annotations

from pathlib import Path

from supamem.indexer.classifier import classify_room

DEFAULT_ROOMS: dict[str, list[str]] = {
    "tests":      ["tests", "test", "__tests__", "spec", "specs"],
    "types":      ["types", "@types", "typings"],
    "migrations": ["migrations", "alembic", "schema"],
    "config":     ["config", "configs", ".github", "ci"],
    "scripts":    ["scripts", "bin", "tools"],
    "docs":       ["docs", "documentation"],
    "frontend":   ["frontend", "web", "client", "ui", "components", "pages"],
    "backend":    ["src", "backend", "api", "server", "lib"],
}


def test_tests_wins_over_backend() -> None:
    """CLASS-02 positive: D-01a priority — ``tests`` precedes ``backend``."""
    assert classify_room("tests/backend/api_test.py", DEFAULT_ROOMS) == "tests"


def test_substring_does_not_match() -> None:
    """CLASS-02 negative: ``test`` MUST NOT substring-match into ``chest_xray``."""
    assert classify_room("data/chest_xray/img.png", DEFAULT_ROOMS) is None


def test_unmatched_returns_none() -> None:
    """No path component matches any keyword → None."""
    assert classify_room("README.md", DEFAULT_ROOMS) is None


def test_python_src_layout_classifies_backend() -> None:
    """Python ``src/`` layout: ``src`` is a backend keyword; no frontend match."""
    assert classify_room("src/myapp/foo.py", DEFAULT_ROOMS) == "backend"


def test_js_monorepo_components_wins_frontend() -> None:
    """Pattern-mapper caveat #3 — JS monorepo ``src/components/Button.tsx``:
    both ``src`` and ``components`` are present, but ``frontend`` precedes
    ``backend`` in D-01a, so ``components`` wins."""
    assert (
        classify_room("src/components/Button.tsx", DEFAULT_ROOMS) == "frontend"
    )


def test_rust_src_main_classifies_backend() -> None:
    """Rust ``src/main.rs`` layout: ``src`` is backend keyword."""
    assert classify_room("src/main.rs", DEFAULT_ROOMS) == "backend"


def test_go_cmd_layout_returns_none() -> None:
    # Documents that 'cmd' is intentionally NOT a default keyword — Go projects
    # need user-configured rooms; tracked as known gap, not a bug.
    assert classify_room("cmd/server/main.go", DEFAULT_ROOMS) is None


def test_docs_match() -> None:
    assert classify_room("docs/intro.md", DEFAULT_ROOMS) == "docs"


def test_migrations_alembic_match() -> None:
    assert classify_room("alembic/versions/001.py", DEFAULT_ROOMS) == "migrations"


def test_path_object_accepted() -> None:
    assert classify_room(Path("tests/foo.py"), DEFAULT_ROOMS) == "tests"


def test_custom_order_priority_follows_config() -> None:
    """First-match-wins follows ``rooms`` config order, NOT a hardcoded
    priority. Reverse the default map and ``backend`` now wins for
    ``tests/backend/api_test.py``."""
    rooms = {"backend": ["src", "backend"], "tests": ["tests"]}
    assert classify_room("tests/backend/api_test.py", rooms) == "backend"


def test_empty_rooms_returns_none() -> None:
    assert classify_room("foo.py", {}) is None


def test_absolute_path_classifies_correctly() -> None:
    """RESEARCH Pitfall 6 — leading ``/`` in ``Path.parts`` is harmless."""
    assert classify_room("/abs/tests/foo.py", DEFAULT_ROOMS) == "tests"
