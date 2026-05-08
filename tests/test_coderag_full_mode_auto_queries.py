"""Phase 16 Plan B Task 1 (RED) — full-mode auto-queries dispatch + field presence.

Acceptance gate for Req-01 / D-DISP-01..03:

* ``supamem eval --suite coderag --full`` swaps records from
  ``coderag_smoke.json`` to the auto_queries pipeline driven by the
  realized manifest (``ensure_populated_manifest``).
* Every record carries ``query_origin`` ∈ {"pr_first_parent",
  "adr_problem_section"} and ``training_leakage_suspected`` ∈ {True, False}.
* Default offline path (``full=False``) stays byte-identical to v0.3.0a5
  behavior — still loads the smoke fixture.
* ``downsample_stratified(seed=42, target=300)`` cap is honored.

Network is never touched: ``ensure_populated_manifest`` is monkeypatched
to a stub that returns the realized-manifest shape and ``repo_cache_path``
is redirected to two tmp git repos built deterministically.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture builders — two deterministic tmp git repos.


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t.test",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }


def _init_repo(dest: Path) -> None:
    env = _git_env()
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


def _commit(dest: Path, rel_path: str, content: str, msg: str) -> None:
    env = _git_env()
    full = dest / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(dest), "add", rel_path], check=True, env=env,
    )
    subprocess.run(
        ["git", "-C", str(dest), "commit", "-q", "-m", msg],
        check=True, env=env,
    )


def _build_pr_heavy_repo(dest: Path, n_commits: int = 45) -> Path:
    """Build a tmp repo with at least ``n_commits`` first-parent commits with
    non-empty gold (each touches a non-excluded source file).
    """
    _init_repo(dest)
    for i in range(n_commits):
        _commit(
            dest,
            f"src/mod_{i:03d}.py",
            f"def fn_{i}():\n    return {i}\n",
            f"feat: add mod_{i:03d}",
        )
    return dest


def _build_adr_heavy_repo(dest: Path, n_adrs: int = 12) -> Path:
    """Build a tmp repo with at least ``n_adrs`` ADR markdown files plus a
    src file each ADR cites (so gold is non-empty)."""
    _init_repo(dest)
    # Seed src file (avoid the case where ADRs cite missing files).
    _commit(dest, "src/core.py", "def core():\n    return 0\n", "feat: core")
    for i in range(n_adrs):
        adr_path = f"docs/adr/{i + 1:04d}-decision-{i:03d}.md"
        body = (
            f"# {i + 1:04d} — Decision {i:03d}\n\n"
            f"## Problem\n\nWe need decision {i}.\n\n"
            f"## Decision\n\nUse `src/core.py` for decision {i}.\n"
        )
        _commit(dest, adr_path, body, f"docs: add ADR {i + 1:04d}")
    return dest


@pytest.fixture
def pr_heavy_repo(tmp_path: Path) -> Path:
    return _build_pr_heavy_repo(tmp_path / "repo_pr")


@pytest.fixture
def adr_heavy_repo(tmp_path: Path) -> Path:
    return _build_adr_heavy_repo(tmp_path / "repo_adr")


# ---------------------------------------------------------------------------
# Helpers


class _NoOpBackend:
    """Returns no hits; lets ``_run_coderag`` complete without Qdrant."""

    def query(self, text, k=20, *, where=None):  # noqa: ANN001, ANN003, ARG002
        return []


def _patch_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pr_repo: Path | None = None,
    adr_repo: Path | None = None,
    captured: list | None = None,
) -> None:
    """Wire monkeypatches for dispatch test surface.

    * ``ensure_populated_manifest`` (in runner namespace) → returns a stub
      realized manifest pointing slug→tmp-repo via ``repo_cache_path`` patch.
    * ``repo_cache_path`` → returns the per-slug tmp repo path.
    * ``_build_backend`` → returns ``_NoOpBackend``.
    * ``CodeRAGSuite.run`` → captures records and returns a minimal valid
      envelope shape so the dispatch flow completes without hitting the
      real per-query loop.
    """
    import supamem.eval.runner as runner_mod
    import supamem.eval.coderag as coderag_pkg

    slug_to_root: dict[str, Path] = {}
    repos_entries: list[dict] = []
    if pr_repo is not None:
        slug_to_root["pr_repo"] = pr_repo
        repos_entries.append({
            "slug": "pr_repo",
            "repo_url": "file:///tmp/pr_repo",
            "commit_sha": "0" * 40,
            "content_sha256": "f" * 64,
        })
    if adr_repo is not None:
        slug_to_root["adr_repo"] = adr_repo
        repos_entries.append({
            "slug": "adr_repo",
            "repo_url": "file:///tmp/adr_repo",
            "commit_sha": "1" * 40,
            "content_sha256": "e" * 64,
        })

    def _stub_ensure(_path):  # noqa: ANN001
        return {"repos": list(repos_entries)}

    def _stub_repo_cache_path(slug, sha):  # noqa: ANN001, ARG001
        return slug_to_root[slug]

    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.ensure_populated_manifest",
        _stub_ensure,
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.repo_cache_path",
        _stub_repo_cache_path,
    )
    monkeypatch.setattr(
        runner_mod, "_build_backend", lambda *a, **k: _NoOpBackend(),
    )

    def _capture_run(records, backend, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ARG001
        if captured is not None:
            captured.append(list(records))
        return {
            "report_schema_version": "coderag.v1",
            "scores": {
                "code_fact": {
                    "supamem_only": None,
                    "fastapi_only": None,
                    "combined": None,
                },
                "decision_rationale": {
                    "supamem_only": None,
                    "fastapi_only": None,
                    "combined": None,
                },
            },
            "peers": {},
        }

    monkeypatch.setattr(coderag_pkg.CodeRAGSuite, "run", staticmethod(_capture_run))


# ---------------------------------------------------------------------------
# Test 1 — record count under --full ≥ 50


def test_full_mode_emits_at_least_50_records_from_auto_queries(
    monkeypatch: pytest.MonkeyPatch,
    pr_heavy_repo: Path,
    adr_heavy_repo: Path,
) -> None:
    from supamem.eval.runner import run_bench

    captured: list[list[dict]] = []
    _patch_dispatch(
        monkeypatch,
        pr_repo=pr_heavy_repo,
        adr_repo=adr_heavy_repo,
        captured=captured,
    )

    rc = run_bench(suite="coderag", full=True)
    assert rc == 0
    assert captured, "CodeRAGSuite.run was never reached"
    records = captured[-1]
    assert len(records) >= 50, (
        f"expected ≥ 50 records under --full, got {len(records)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — default-path untouched (smoke records exactly)


def test_default_path_loads_smoke_fixture_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supamem.eval.runner import run_bench

    captured: list[list[dict]] = []
    # No repos passed: dispatch is in default mode (full=False).
    _patch_dispatch(monkeypatch, captured=captured)

    rc = run_bench(suite="coderag", full=False)
    assert rc == 0
    assert captured, "CodeRAGSuite.run was never reached"
    records = captured[-1]
    # The smoke fixture currently has 6 questions (v0.3.0a5 baseline).
    assert len(records) == 6, (
        f"smoke fixture must yield 6 records (byte-identical lock); got {len(records)}"
    )
    # Smoke records do NOT carry query_origin (it's a --full-only field
    # contract). Just sanity-check the smoke shape persists.
    for r in records:
        assert set(r.keys()) >= {"id", "axis", "repo", "text", "gold"}


# ---------------------------------------------------------------------------
# Test 3 — query_origin field present on every PR / ADR record at extraction


def test_extract_pr_queries_carry_query_origin_field(pr_heavy_repo: Path) -> None:
    from supamem.eval.coderag.auto_queries import extract_pr_queries

    queries = extract_pr_queries(pr_heavy_repo, "pr_repo")
    assert queries
    for q in queries:
        assert q["query_origin"] == "pr_first_parent", q


def test_extract_adr_queries_carry_query_origin_field(adr_heavy_repo: Path) -> None:
    from supamem.eval.coderag.auto_queries import extract_adr_queries

    queries = extract_adr_queries(adr_heavy_repo, "adr_repo")
    assert queries
    for q in queries:
        assert q["query_origin"] == "adr_problem_section", q


# ---------------------------------------------------------------------------
# Test 4 — training_leakage_suspected field PRESENT on every extracted record


def test_every_extracted_record_carries_training_leakage_suspected(
    pr_heavy_repo: Path,
    adr_heavy_repo: Path,
) -> None:
    from supamem.eval.coderag.auto_queries import (
        extract_adr_queries,
        extract_pr_queries,
    )

    pr_queries = extract_pr_queries(pr_heavy_repo, "pr_repo")
    adr_queries = extract_adr_queries(adr_heavy_repo, "adr_repo")
    assert pr_queries and adr_queries
    for q in (*pr_queries, *adr_queries):
        assert "training_leakage_suspected" in q, q
        assert isinstance(q["training_leakage_suspected"], bool), q


# ---------------------------------------------------------------------------
# Test 5 — downsample seed=42 + target=300 cap is deterministic and honored


def test_full_mode_downsamples_to_300_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When extracted-record count > 300, --full dispatch yields exactly 300
    records and identical id-lists across two runs (seed=42)."""
    from supamem.eval.runner import run_bench

    big_repo = _build_pr_heavy_repo(tmp_path / "big_repo", n_commits=350)

    captured_a: list[list[dict]] = []
    _patch_dispatch(monkeypatch, pr_repo=big_repo, captured=captured_a)
    run_bench(suite="coderag", full=True)

    captured_b: list[list[dict]] = []
    _patch_dispatch(monkeypatch, pr_repo=big_repo, captured=captured_b)
    run_bench(suite="coderag", full=True)

    assert captured_a and captured_b
    records_a = captured_a[-1]
    records_b = captured_b[-1]
    assert len(records_a) == 300, f"expected 300, got {len(records_a)}"
    assert len(records_b) == 300, f"expected 300, got {len(records_b)}"
    ids_a = [r["id"] for r in records_a]
    ids_b = [r["id"] for r in records_b]
    assert ids_a == ids_b, "seed=42 must yield identical id list across runs"
