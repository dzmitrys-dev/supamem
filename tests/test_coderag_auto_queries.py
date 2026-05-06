"""Phase 15 Plan B Task B2 — auto-query extractor tests (PR + ADR + downsample)."""
from __future__ import annotations

import json
from pathlib import Path


from supamem.eval.coderag.auto_queries import (
    downsample_stratified,
    extract_adr_queries,
    extract_pr_queries,
)


# ----------------------------- PR / code_fact axis --------------------------


def test_extract_pr_queries_yields_for_each_commit_with_gold(tiny_repo):
    queries = extract_pr_queries(tiny_repo, "tiny")
    # The fixture has 10 commits but several touch only excluded paths
    # (tests/, *.lock, dist/). Surviving queries should cover at least the
    # source/doc commits (≥3).
    assert len(queries) >= 3
    for q in queries:
        assert q["axis"] == "code_fact"
        assert q["repo"] == "tiny"
        assert q["text"], q
        assert q["gold"], q


def test_extract_pr_queries_excludes_test_files_from_gold(tiny_repo):
    queries = extract_pr_queries(tiny_repo, "tiny")
    for q in queries:
        for path in q["gold"]:
            assert path != "tests/test_foo.py"
            assert not path.startswith("tests/")


def test_extract_pr_queries_excludes_lock_files_from_gold(tiny_repo):
    queries = extract_pr_queries(tiny_repo, "tiny")
    for q in queries:
        for path in q["gold"]:
            assert not path.endswith(".lock")


def test_extract_pr_queries_drops_query_with_empty_gold(tiny_repo):
    queries = extract_pr_queries(tiny_repo, "tiny")
    # Verify NO query corresponds to the tests-only commit (it touched only
    # an excluded path → gold empty → query dropped).
    for q in queries:
        assert q["gold"], f"empty-gold query slipped through: {q['id']}"


def test_extract_pr_queries_text_includes_title(tiny_repo):
    queries = extract_pr_queries(tiny_repo, "tiny")
    # Fixture commit titles are "commit NN: <path>".
    assert any("commit " in q["text"] for q in queries)


# ----------------------------- ADR / decision axis --------------------------


def test_extract_adr_queries_yields_one_per_adr(tiny_repo):
    queries = extract_adr_queries(tiny_repo, "tiny")
    assert len(queries) == 2
    for q in queries:
        assert q["axis"] == "decision_rationale"
        assert q["repo"] == "tiny"


def test_extract_adr_queries_gold_includes_adr_path(tiny_repo):
    queries = extract_adr_queries(tiny_repo, "tiny")
    by_id = {q["id"]: q for q in queries}
    # Every ADR query's gold must include the ADR file itself.
    assert any(p == "docs/adr/0001-pick-foo.md" for q in queries for p in q["gold"])
    assert any(p == "docs/adr/0002-pick-bar.md" for q in queries for p in q["gold"])
    assert by_id  # touch the alias for clarity


def test_extract_adr_queries_gold_includes_decision_section_citations(tiny_repo):
    queries = extract_adr_queries(tiny_repo, "tiny")
    # ADR-0001 cites src/foo.py
    q1 = next(q for q in queries if "0001" in q["id"])
    assert "src/foo.py" in q1["gold"]
    assert "docs/adr/0001-pick-foo.md" in q1["gold"]
    # ADR-0002 cites src/bar.py (and `README.md` — but README.md has no
    # slash, so per Pitfall 4 anchoring it is NOT picked up as a path; the
    # ADR file itself remains in gold).
    q2 = next(q for q in queries if "0002" in q["id"])
    assert "src/bar.py" in q2["gold"]
    assert "docs/adr/0002-pick-bar.md" in q2["gold"]


def test_extract_adr_queries_regex_requires_slash_anchor(tiny_repo):
    """Pitfall 4: prose '.py' (no slash) must NOT be treated as a path.

    ADR-0002 contains 'any .py file works' under Decision; that prose-".py"
    must not slip into the gold list.
    """
    queries = extract_adr_queries(tiny_repo, "tiny")
    q2 = next(q for q in queries if "0002" in q["id"])
    for path in q2["gold"]:
        # Every gold path MUST contain at least one "/" (anchored regex).
        assert "/" in path, f"prose-only token leaked into gold: {path!r}"


def test_extract_adr_queries_drops_missing_paths_with_warning(tmp_path, capsys):
    """Cited path missing at pinned SHA → dropped + warning."""
    # Build a minimal repo with one ADR citing a non-existent path.
    import os
    import subprocess

    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.t"}
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True, env=env)
    adr_dir = repo / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    adr = adr_dir / "0001-x.md"
    adr.write_text(
        "# 0001\n\n## Problem\n\nMissing.\n\n## Decision\n\n"
        "Use `src/missing.py` for the impl.\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, env=env)

    queries = extract_adr_queries(repo, "tiny")
    assert len(queries) == 1
    assert "src/missing.py" not in queries[0]["gold"]
    # err_console.print writes to stderr — capsys captures it.
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert "missing" in combined or "src/missing.py" in (captured.err + captured.out)


def test_extract_adr_queries_supamem_only_fastapi_skipped(tmp_path):
    """A-D-HAY-04: repos without docs/adr/ return [] (fastapi case)."""
    # Build a repo with NO docs/adr/ directory.
    import os
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.t"}
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True, env=env)
    (repo / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, env=env)

    queries = extract_adr_queries(repo, "fastapi")
    assert queries == []


# ----------------------------- downsample -----------------------------------


def test_downsample_stratified_seed_42_deterministic():
    queries = []
    for i in range(300):
        queries.append({"id": f"cf_supamem_{i:03d}", "axis": "code_fact", "repo": "supamem", "text": "t", "gold": ["g"]})
    for i in range(200):
        queries.append({"id": f"cf_fastapi_{i:03d}", "axis": "code_fact", "repo": "fastapi", "text": "t", "gold": ["g"]})
    for i in range(100):
        queries.append({"id": f"dr_supamem_{i:03d}", "axis": "decision_rationale", "repo": "supamem", "text": "t", "gold": ["g"]})

    a = downsample_stratified(queries, 300, seed=42)
    b = downsample_stratified(queries, 300, seed=42)
    assert a == b
    assert len(a) == 300
    # Per-bucket proportions should approximately preserve input ratios.
    n_cf_sup = sum(1 for q in a if q["axis"] == "code_fact" and q["repo"] == "supamem")
    n_cf_fa = sum(1 for q in a if q["axis"] == "code_fact" and q["repo"] == "fastapi")
    n_dr_sup = sum(1 for q in a if q["axis"] == "decision_rationale" and q["repo"] == "supamem")
    # Input proportions: 300/600/100 → halved → 150/100/50 (approx).
    assert 140 <= n_cf_sup <= 160
    assert 90 <= n_cf_fa <= 110
    assert 40 <= n_dr_sup <= 60


def test_downsample_returns_input_when_under_target():
    queries = [{"id": f"q{i}", "axis": "code_fact", "repo": "supamem", "text": "t", "gold": ["g"]} for i in range(50)]
    out = downsample_stratified(queries, 300, seed=42)
    assert len(out) == 50
    assert {q["id"] for q in out} == {q["id"] for q in queries}


# ----------------------------- smoke fixture --------------------------------


def test_coderag_smoke_fixture_loads():
    p = Path(__file__).resolve().parents[1] / "src" / "supamem" / "eval" / "datasets" / "coderag_smoke.json"
    assert p.exists(), p
    assert p.stat().st_size <= 200 * 1024, f"smoke fixture too large: {p.stat().st_size} bytes"
    data = json.loads(p.read_text())
    assert "meta" in data
    assert "questions" in data
    assert len(data["questions"]) >= 5


def test_coderag_smoke_fixture_schema():
    p = Path(__file__).resolve().parents[1] / "src" / "supamem" / "eval" / "datasets" / "coderag_smoke.json"
    data = json.loads(p.read_text())
    required = {"id", "axis", "text", "repo", "gold", "haystack"}
    for q in data["questions"]:
        assert required <= set(q.keys()), f"missing keys in {q.get('id')}: {required - set(q.keys())}"
        assert isinstance(q["gold"], list)
        assert isinstance(q["haystack"], list)


def test_coderag_smoke_fixture_has_both_axes():
    p = Path(__file__).resolve().parents[1] / "src" / "supamem" / "eval" / "datasets" / "coderag_smoke.json"
    data = json.loads(p.read_text())
    axes = {q["axis"] for q in data["questions"]}
    assert "code_fact" in axes
    assert "decision_rationale" in axes
