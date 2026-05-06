"""Phase 15 Plan B Task B1 — pinned-SHA fetch + corpus-walk tests."""
from __future__ import annotations

import subprocess
from pathlib import Path


from supamem.eval.coderag import corpus as corpus_mod
from supamem.eval.coderag.corpus import (
    fetch_pinned,
    repo_cache_path,
    walk_corpus,
)


# ----------------------------- subprocess shape ----------------------------


def _make_run_recorder():
    calls: list[dict] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({"cmd": cmd, "args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return calls, fake_run


def test_fetch_pinned_uses_args_list_not_shell(tmp_path, monkeypatch):
    calls, fake_run = _make_run_recorder()
    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "repo"
    fetch_pinned("https://example.test/owner/repo.git", "deadbeef" * 5, dest)
    assert calls, "subprocess.run was never called"
    for c in calls:
        # NEVER shell=True (T-15-04).
        assert c["kwargs"].get("shell", False) is False
        # cmd MUST be a list/tuple, never a single string.
        assert isinstance(c["cmd"], (list, tuple)), c["cmd"]


def test_fetch_pinned_init_fetch_checkout_sequence(tmp_path, monkeypatch):
    calls, fake_run = _make_run_recorder()
    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "repo"
    sha = "deadbeef" * 5
    fetch_pinned("https://example.test/owner/repo.git", sha, dest)

    # Extract the first arg after `git` (and the optional -C <path>) from each call.
    def first_subcommand(cmd: list[str]) -> str:
        if cmd[0] != "git":
            return cmd[0]
        i = 1
        if i < len(cmd) and cmd[i] == "-C":
            i += 2
        return cmd[i] if i < len(cmd) else ""

    seq = [first_subcommand(c["cmd"]) for c in calls if c["cmd"][0] == "git"]
    # Expect init → remote → fetch → checkout in that order, possibly with
    # other ancillary calls preserved.
    canonical = [s for s in seq if s in {"init", "remote", "fetch", "checkout"}]
    assert canonical[:4] == ["init", "remote", "fetch", "checkout"], canonical


def test_fetch_pinned_idempotent_on_sha_match(tmp_path, monkeypatch):
    """If dest/.git/HEAD already resolves to pinned sha, no fetch is issued."""
    dest = tmp_path / "repo"
    (dest / ".git").mkdir(parents=True)
    (dest / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    sha = "abc123" + "0" * 34

    calls, fake_run = _make_run_recorder()

    def fake_run_with_rev_parse(cmd, *args, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=sha + "\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run_with_rev_parse)
    fetch_pinned("https://example.test/repo.git", sha, dest)
    # Only rev-parse should have been issued; no init/fetch/checkout
    # subcommands. Inspect the parsed subcommand from each cmd, NOT raw paths
    # (tmp_path may legitimately contain the substring "fetch").
    def first_subcommand(cmd: list[str]) -> str:
        if cmd[0] != "git":
            return cmd[0]
        i = 1
        if i < len(cmd) and cmd[i] == "-C":
            i += 2
        return cmd[i] if i < len(cmd) else ""

    subs = [first_subcommand(c["cmd"]) for c in calls]
    assert "rev-parse" in subs
    assert "fetch" not in subs
    assert "init" not in subs
    assert "checkout" not in subs


def test_fetch_pinned_wipes_on_sha_mismatch(tmp_path, monkeypatch):
    dest = tmp_path / "repo"
    (dest / ".git").mkdir(parents=True)
    (dest / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (dest / "leftover.txt").write_text("stale\n")
    pinned = "newshanew" + "0" * 31

    def fake_run(cmd, *args, **kwargs):
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="DIFFERENTSHA\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetch_pinned("https://example.test/repo.git", pinned, dest)
    # Stale file should have been wiped.
    assert not (dest / "leftover.txt").exists()


def test_fetch_pinned_uses_platformdirs_cache(tmp_path, monkeypatch):
    cache = repo_cache_path("supamem", "abc1234567")
    s = str(cache)
    assert "supamem" in s.lower()
    assert "coderag" in s
    assert "abc1234567" in s


# ----------------------------- walk_corpus ----------------------------------


def test_walk_corpus_applies_allowlist(tiny_repo):
    paths = sorted(p.relative_to(tiny_repo).as_posix() for p in walk_corpus(tiny_repo))
    # Non-allowlisted files (e.g. *.lock at top level) MUST be excluded;
    # allowlisted (.py, .md) under canonical paths MUST appear.
    assert "src/foo.py" in paths
    assert "src/bar.py" in paths
    assert "package.lock" not in paths
    # No json/binary/object files leaked through (.git/* always excluded):
    for p in paths:
        assert not p.startswith(".git/"), p


def test_walk_corpus_applies_excludes_after_walk(tiny_repo):
    paths = {p.relative_to(tiny_repo).as_posix() for p in walk_corpus(tiny_repo)}
    # The fixture seeds these three excluded paths. Excludes are applied
    # AFTER the include list is fully assembled (Pitfall 1).
    assert "tests/test_foo.py" not in paths
    assert "package.lock" not in paths
    assert "dist/foo.generated.py" not in paths


def test_walk_corpus_includes_top_level_docs(tiny_repo):
    paths = {p.relative_to(tiny_repo).as_posix() for p in walk_corpus(tiny_repo)}
    assert "README.md" in paths
    assert "CHANGELOG.md" in paths
    assert "llms.txt" in paths


# ----------------------------- module hygiene -------------------------------


def test_corpus_module_has_no_shell_true():
    src = Path(corpus_mod.__file__).read_text()
    assert "shell=True" not in src
