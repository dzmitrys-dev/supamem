"""Edit-hook usefulness stage 1 (v0.4.0a4) — the owner-approved plan from
``cl-harness/research/research-supamem-effectiveness-2026-11-14.md``.

Baseline measured by that study: injection rate 4.2%, use rate 0.6%, repeat rate 65.6%,
stale rate 8.5%, worktree sessions 42% of calls and 100% empty, no score floor, no
usefulness metric. Each test here pins one fix; all failed before the change.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.retrieval.types import RetrievedChunk


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "test_hook"}
    base.update(overrides)
    return ResolvedConfig(**base)


def _chunk(
    source: str,
    text: str,
    *,
    score: float = 3.0,
    rerank: float | None = None,
    headers: dict[str, str] | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=source,
        text=text,
        score=score,
        source_path=source,
        rerank_score=rerank,
        headers=headers,
    )


def _journal_rows() -> list[dict[str, Any]]:
    from supamem.stats.counter import default_cache_dir

    p = default_cache_dir() / "injections.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _fake_backend(monkeypatch: pytest.MonkeyPatch, chunks: list[RetrievedChunk]) -> MagicMock:
    import supamem.hooks.claude_code as hook

    b = MagicMock()
    b.query.return_value = list(chunks)
    monkeypatch.setattr(hook, "_get_backend", lambda cfg: b)
    monkeypatch.setattr(hook, "_touch_marker", lambda *a, **k: None)
    return b


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "src").mkdir()
    return proj


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def main_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real git repo (with a committed tree) plus one linked worktree."""
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", cwd=main)
    (main / "README.md").write_text("# repo\n")
    _git("add", "README.md", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    return main, wt


# ---- 1. worktree sessions resolve the main checkout as the project root -------------------------------------


def test_worktree_session_sees_main_checkout_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    main_repo_with_worktree: tuple[Path, Path],
) -> None:
    import supamem.hooks.claude_code as hook

    main, wt = main_repo_with_worktree
    doc = main / "docs" / "a.md"
    doc.parent.mkdir()
    doc.write_text("# A\n")
    _fake_backend(
        monkeypatch, [_chunk(str(doc), "The turn resolver invariant holds.", score=4.0, rerank=4.0)]
    )
    payload = {"session_id": "s-wt", "cwd": str(wt)}
    assert hook.run(wt / "src" / "x.py", _cfg(), payload=payload) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx, "worktree session must see main-checkout chunks (was 100% empty by construction)"
    assert "docs/a.md" in ctx
    row = _journal_rows()[-1]
    assert row["empty"] is False
    assert row["target"] == str(wt / "src" / "x.py")


def test_hook_cli_loads_config_from_the_main_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    main_repo_with_worktree: tuple[Path, Path],
) -> None:
    from typer.testing import CliRunner

    from supamem.cli import app

    main, wt = main_repo_with_worktree
    (main / ".supamem").mkdir()
    (main / ".supamem" / "config.toml").write_text('[supamem]\ncollection = "supamem-wt-test"\n')
    doc = main / "docs" / "a.md"
    doc.parent.mkdir()
    doc.write_text("# A\n")
    import supamem.hooks.claude_code as hook

    b = MagicMock()
    b.query.return_value = [_chunk(str(doc), "Main doc body.", score=4.0, rerank=4.0)]
    monkeypatch.setattr(hook, "_get_backend", lambda cfg: b)
    monkeypatch.setattr(hook, "_touch_marker", lambda *a, **k: None)
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    monkeypatch.delenv("SUPAMEM_PROJECT_ROOT", raising=False)
    payload = {
        "session_id": "cli-1",
        "cwd": str(wt),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(wt / "src" / "x.py")},
    }
    r = CliRunner().invoke(app, ["hook", "claude-code"], input=json.dumps(payload))
    assert r.exit_code == 0, r.output
    out = r.stdout.strip().splitlines()[-1]
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "docs/a.md" in ctx, (
        "the hook config must be loaded from the main checkout, not the worktree cwd "
        "(a worktree never contains the untracked .supamem/config.toml)"
    )
    assert b.query.call_args.kwargs["k"] == 30  # fetch_k default raised from 10


# ---- 2. session dedup: a repeat is suppressed, a new session gets it again ----------------------------------


def test_session_dedup_suppresses_repeat_and_new_session_reinjects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    doc = proj / "docs" / "a.md"
    doc.write_text("# A\n")
    _fake_backend(monkeypatch, [_chunk(str(doc), "Invariant text.", score=4.0, rerank=4.0)])
    monkeypatch.chdir(proj)
    same = {"session_id": "s1", "cwd": str(proj)}
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=same) == 0
    first = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "docs/a.md" in first
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=same) == 0
    second = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert second == "", (
        "identical re-injection in the SAME session must be suppressed (baseline 65.6%)"
    )
    assert _journal_rows()[-1]["deduped"] == 1
    fresh = {"session_id": "s2", "cwd": str(proj)}
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=fresh) == 0
    third = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "docs/a.md" in third, "a NEW session must inject again"


def test_dedup_marker_expires_after_24h(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook
    from supamem.stats.counter import default_cache_dir

    proj = _project(tmp_path)
    doc = proj / "docs" / "a.md"
    doc.write_text("# A\n")
    _fake_backend(monkeypatch, [_chunk(str(doc), "Invariant text.", score=4.0, rerank=4.0)])
    monkeypatch.chdir(proj)
    payload = {"session_id": "s-old", "cwd": str(proj)}
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=payload) == 0
    capsys.readouterr()
    marker = default_cache_dir() / "dedup-s-old.json"
    assert marker.exists()
    old = json.loads(marker.read_text())
    old["ts"] = old["ts"] - 24 * 3600 - 1
    marker.write_text(json.dumps(old))
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=payload) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "docs/a.md" in ctx, "a marker older than the 24h TTL must not suppress"


# ---- 3. stale drop: a chunk whose source is gone is not injected --------------------------------------------


def test_stale_chunk_is_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    live = proj / "docs" / "live.md"
    live.write_text("# A\n")
    ghost = proj / "docs" / "deleted.md"  # never created
    _fake_backend(
        monkeypatch,
        [
            _chunk(str(ghost), "Ghost text about deleted research.", score=5.0, rerank=5.0),
            _chunk(str(live), "Live text.", score=4.0, rerank=4.0),
            _chunk("docs/live.md", "Relative live text.", score=3.0, rerank=3.0),
        ],
    )
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s3", "cwd": str(proj)}) == 0
    )
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Ghost text" not in ctx
    assert "Live text." in ctx and "Relative live text." in ctx
    row = _journal_rows()[-1]
    assert row["dropped"]["stale"] == 1, "relative sources resolve against the project root"


# ---- 4. score floor: applies to reranked scores only ---------------------------------------------------------


def test_score_floor_applies_only_to_reranked_scores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    for name in ("noisy", "rrf", "strong"):
        (proj / "docs" / f"{name}.md").write_text("# A\n")
    _fake_backend(
        monkeypatch,
        [
            _chunk(str(proj / "docs" / "noisy.md"), "Noisy noise.", score=1.2, rerank=1.2),
            _chunk(str(proj / "docs" / "rrf.md"), "Rrf only body.", score=1.0, rerank=None),
            _chunk(str(proj / "docs" / "strong.md"), "Strong body.", score=4.0, rerank=4.5),
        ],
    )
    monkeypatch.chdir(proj)
    payload = {"session_id": "s4", "cwd": str(proj)}
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=payload) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Noisy noise." not in ctx, (
        "a reranked score below the floor (default 2.5) must be dropped"
    )
    assert "Rrf only body." in ctx, "RRF-only scores (rerank_score None) are NOT floored"
    assert "Strong body." in ctx
    assert _journal_rows()[-1]["dropped"]["floor"] == 1

    cfg = _cfg()
    cfg.hook_min_score = 0.0
    assert (
        hook.run(proj / "src" / "y.py", cfg, payload={"session_id": "s4b", "cwd": str(proj)}) == 0
    )
    ctx2 = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Noisy noise." in ctx2, "min_score = 0 disables the floor"


# ---- 5. the project filter runs BEFORE the inject cut ---------------------------------------------------------


def test_project_filter_runs_before_the_cut(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "own0.md").write_text("# A\n")
    foreign = [
        _chunk(str(tmp_path / f"other{i}" / "d.md"), f"Foreign {i} filler.", score=5.0, rerank=5.0)
        for i in range(10)
    ]
    own = [_chunk(str(proj / "docs" / "own0.md"), "Own zero text.", score=4.0, rerank=4.0)]
    b = _fake_backend(monkeypatch, foreign + own)
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s5", "cwd": str(proj)}) == 0
    )
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "Own zero text." in ctx, (
        "10 foreign chunks must not crowd the project's own chunk out of the cut"
    )
    assert b.query.call_args.kwargs["k"] == 30, (
        "fetch_k default raised 10 -> 30 so the filter has room"
    )
    row = _journal_rows()[-1]
    assert row["dropped"]["foreign"] == 10


# ---- 6. shadow task query: journaled, never emitted -----------------------------------------------------------


def _transcript(tmp_path: Path) -> Path:
    t = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "user",
            "timestamp": "2026-11-14T10:00:00Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Fix the respawn bug in the arena loop"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-11-14T10:00:05Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]},
        },
        {
            "type": "user",
            "timestamp": "2026-11-14T10:00:10Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
            },
        },
    ]
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return t


def test_shadow_task_query_journaled_but_never_emitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    for name in ("main", "shadow"):
        (proj / "docs" / f"{name}.md").write_text("# A\n")
    main_ch = _chunk(str(proj / "docs" / "main.md"), "Main doc body.", score=4.0, rerank=4.0)
    shadow_ch = _chunk(str(proj / "docs" / "shadow.md"), "Shadow doc body.", score=4.0, rerank=4.0)
    import supamem.hooks.claude_code as cc

    b = MagicMock()
    b.query.side_effect = lambda q, k=10: [main_ch] if "arena" not in q else [shadow_ch]
    monkeypatch.setattr(cc, "_get_backend", lambda cfg: b)
    monkeypatch.setattr(cc, "_touch_marker", lambda *a, **k: None)
    monkeypatch.chdir(proj)
    payload = {
        "session_id": "s6",
        "cwd": str(proj),
        "transcript_path": str(_transcript(tmp_path)),
    }
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=payload) == 0
    out = capsys.readouterr().out
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "docs/main.md" in ctx
    assert "shadow" not in ctx.lower(), "the shadow result must NEVER be emitted"
    row = _journal_rows()[-1]
    assert row["shadow"] is not None
    assert "respawn bug" in row["shadow"]["query"]
    assert row["shadow"]["chunks"] and row["shadow"]["chunks"][0]["source"].endswith("shadow.md")

    # config off → no shadow query at all
    cfg = _cfg()
    cfg.hook_shadow_task_query = False
    assert hook.run(proj / "src" / "y.py", cfg, payload=payload) == 0
    capsys.readouterr()
    assert _journal_rows()[-1]["shadow"] is None
    assert b.query.call_count == 3  # 2 main + 1 shadow before, only main now


def test_shadow_survives_a_broken_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "a.md").write_text("# A\n")
    _fake_backend(
        monkeypatch, [_chunk(str(proj / "docs" / "a.md"), "Body.", score=4.0, rerank=4.0)]
    )
    monkeypatch.chdir(proj)
    payload = {
        "session_id": "s7",
        "cwd": str(proj),
        "transcript_path": str(tmp_path / "missing.jsonl"),
    }
    assert hook.run(proj / "src" / "x.py", _cfg(), payload=payload) == 0, (
        "a missing transcript must fail soft"
    )
    assert _journal_rows()[-1]["shadow"] is None


# ---- 7. the journal row schema ---------------------------------------------------------------------------------


def test_journal_row_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "a.md").write_text("# A\n")
    _fake_backend(
        monkeypatch, [_chunk(str(proj / "docs" / "a.md"), "Body text.", score=4.0, rerank=4.0)]
    )
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s8", "cwd": str(proj)}) == 0
    )
    capsys.readouterr()
    row = _journal_rows()[-1]
    for key in (
        "ts",
        "session",
        "target",
        "query",
        "chunks",
        "empty",
        "deduped",
        "dropped",
        "ms",
        "shadow",
    ):
        assert key in row, f"journal row missing {key!r}"
    assert {"source", "score", "exists", "lang"} <= set(row["chunks"][0])
    assert {"stale", "floor", "foreign"} <= set(row["dropped"])
    assert isinstance(row["ms"], (int, float))
    assert row["session"] == "s8" and row["empty"] is False
    assert row["chunks"][0]["lang"] == "en"

    # every invocation journals, including non-code targets (empty rows)
    assert hook.run(proj / "README.md", _cfg(), payload={"session_id": "s8", "cwd": str(proj)}) == 0
    capsys.readouterr()
    row2 = _journal_rows()[-1]
    assert row2["empty"] is True and row2["chunks"] == []


# ---- 8. pointer format ------------------------------------------------------------------------------------------


def test_pointer_format_and_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "a.md").write_text("# A\n")
    long_text = (
        "The session module is the only writer of combat signals. "
        "Second sentence with more detail that should never appear. " * 5
    )
    _fake_backend(
        monkeypatch,
        [
            _chunk(
                str(proj / "docs" / "a.md"),
                long_text,
                score=4.0,
                rerank=4.0,
                headers={"Header 1": "Combat invariants"},
            ),
        ],
    )
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s9", "cwd": str(proj)}) == 0
    )
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    lines = ctx.splitlines()
    assert lines[0] == "### Memory context (supamem)"
    assert lines[1].startswith("- **docs/a.md** — Combat invariants (score 4.00): ")
    body = lines[1].split("): ", 1)[1]
    assert body.endswith("…")
    assert len(body) <= 161 + 1  # first sentence, ≤160 chars, ellipsis
    assert "Second sentence" not in ctx
    assert lines[-1] == "Pointers only: open the file before relying on it."
    assert len(ctx) <= 2000


def test_pointer_format_without_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "a.md").write_text("# A\n")
    _fake_backend(
        monkeypatch,
        [_chunk(str(proj / "docs" / "a.md"), "Just one sentence.", score=4.0, rerank=4.0)],
    )
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s9b", "cwd": str(proj)})
        == 0
    )
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "- **docs/a.md** (score 4.00): Just one sentence." in ctx


# ---- 9. hook-stats metrics on a synthetic journal + transcript --------------------------------------------------


TS0 = "2026-11-14T10:00:00+00:00"
TS1 = "2026-11-14T10:01:00+00:00"


def _write_row(path: Path, **over: Any) -> None:
    row = {
        "ts": TS0,
        "session": "sess-1",
        "target": "/abs/proj/src/x.py",
        "query": "x",
        "chunks": [{"source": "/abs/proj/docs/a.md", "score": 4.0, "exists": True, "lang": "en"}],
        "empty": False,
        "deduped": 0,
        "dropped": {"stale": 0, "floor": 0, "foreign": 0},
        "ms": 1.0,
        "shadow": None,
    }
    row.update(over)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _write_transcript(path: Path) -> None:
    lines = [
        # assistant reads the injected file LATER than the injection (ts 10:02 > row 10:00)
        {
            "type": "assistant",
            "timestamp": "2026-11-14T10:02:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "r1",
                        "name": "Read",
                        "input": {"file_path": "/abs/proj/docs/a.md"},
                    }
                ],
            },
        },
        # assistant mentions the shadow chunk's basename in text
        {
            "type": "assistant",
            "timestamp": "2026-11-14T10:03:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "per shadow.md the answer is X"}],
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_hook_stats_on_synthetic_transcript(tmp_path: Path) -> None:
    import supamem.stats.injections as inj

    journal = tmp_path / "injections.jsonl"
    _write_row(journal)  # used later via Read → use detected
    _write_row(
        journal,
        ts=TS1,
        chunks=[{"source": "/abs/proj/docs/b.md", "score": 3.0, "exists": True, "lang": "en"}],
        deduped=1,
    )  # a repeat counted; never used
    _write_row(
        journal,
        ts=TS1,
        session="sess-2",
        empty=True,
        chunks=[],
        shadow={"query": "q", "chunks": [{"source": "/abs/proj/docs/shadow.md", "score": 3.5}]},
    )
    roots = tmp_path / "projects"
    _write_transcript(roots / "projdir" / "sess-1.jsonl")
    _write_transcript(roots / "projdir" / "sess-2.jsonl")

    stats = inj.compute_stats(days=7, journal_path=journal, transcript_roots=[roots])
    assert stats["invocations"] == 3
    assert stats["non_empty"] == 2
    assert stats["injection_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert stats["use_rate"] == pytest.approx(0.5)  # 1 of 2 non-empty rows had a later Read
    assert stats["useful_per_100_edits"] == pytest.approx(100 / 3, rel=1e-3)
    assert stats["repeat_rate"] == pytest.approx(0.5)  # 1 of 2 non-empty rows suppressed a repeat
    assert stats["shadow_use_rate"] == pytest.approx(1.0)  # the shadow chunk is mentioned later


def test_hook_stats_ignores_older_transcript_events(tmp_path: Path) -> None:
    import supamem.stats.injections as inj

    journal = tmp_path / "injections.jsonl"
    _write_row(journal, ts="2026-11-14T11:00:00+00:00")  # AFTER the transcript's Read at 10:02
    roots = tmp_path / "projects"
    _write_transcript(roots / "p" / "sess-1.jsonl")
    stats = inj.compute_stats(days=7, journal_path=journal, transcript_roots=[roots])
    assert stats["use_rate"] == 0.0, "only LATER transcript events count as use"


# ---- 10. hook config keys ---------------------------------------------------------------------------------------


def test_hook_config_keys_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.config import load_config

    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    (tmp_path / ".supamem").mkdir()
    (tmp_path / ".supamem" / "config.toml").write_text(
        "[supamem]\n"
        "[supamem.hook]\n"
        'drop_tokens = ["x"]\n'
        "fetch_k = 7\n"
        "inject_k = 2\n"
        "min_score = 0.0\n"
        "shadow_task_query = false\n"
    )
    cfg, chain = load_config(tmp_path)
    assert cfg.hook_fetch_k == 7
    assert cfg.hook_inject_k == 2
    assert cfg.hook_min_score == 0.0
    assert cfg.hook_shadow_task_query is False
    assert cfg.drop_tokens == ["x"]  # existing semantics kept
    assert chain.hook_fetch_k != "default" and chain.hook_min_score != "default"


def test_hook_config_defaults() -> None:
    cfg = ResolvedConfig()
    assert cfg.hook_fetch_k == 30
    assert cfg.hook_inject_k == 5
    assert cfg.hook_min_score == 2.5
    assert cfg.hook_shadow_task_query is True


# ---- 11. run_index warnings -------------------------------------------------------------------------------------


def test_run_index_warns_when_no_files_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    from supamem.console import err_console
    from supamem.indexer import run_index

    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    err_console.width = 500  # no soft-wrap in the captured stderr
    rc = run_index(target="tuned", config=_cfg())
    err = capsys.readouterr().err
    assert rc == 0  # exit code unchanged
    assert "no files collected" in err
    assert "unconfigured" in err
    assert ".supamem/config.toml" in err, "the warning names the config path checked"


def test_run_index_warns_when_sources_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.indexer as idx

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n")
    monkeypatch.chdir(tmp_path)  # relative "docs" resolves HERE, not against the repo cwd
    monkeypatch.setattr(idx, "QdrantClient", lambda **kw: MagicMock())
    monkeypatch.setattr(idx, "ensure_collection", lambda *a, **k: None)
    monkeypatch.setattr(idx, "build_dense_embedder", lambda: MagicMock())
    monkeypatch.setattr(idx, "build_sparse_embedder", lambda: MagicMock())
    monkeypatch.setattr(idx, "_ensure_payload_indexes", lambda *a, **k: None)
    monkeypatch.setattr(idx, "_gc_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(idx, "_process_one_source", lambda **kw: (0, 2))

    fake_manifest = SimpleNamespace(
        validity_migration="x",
        path_prefixes_migration="x",
        classifier_hash=idx._classifier_hash(ResolvedConfig().classifier_rooms),
        save=lambda *_a, **_k: None,
    )
    monkeypatch.setattr(idx.Manifest, "load", classmethod(lambda cls, path: fake_manifest))

    from supamem.console import err_console

    err_console.width = 500
    cfg = _cfg()
    cfg.cache_dir = str(tmp_path)
    rc = idx.run_index(target="tuned", sources=["docs"], config=cfg)
    err = capsys.readouterr().err
    assert rc == 0, "exit code stays fail-soft 0"
    assert "2 source file(s) failed to index" in err


# ---- 12. installer matcher --------------------------------------------------------------------------------------


def test_installer_injection_hook_matches_multiedit() -> None:
    from supamem.install.claude_code import HOOKS_OVERLAY

    entry = HOOKS_OVERLAY["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Edit|Write|MultiEdit"
    assert entry["hooks"][0]["command"] == "supamem hook claude-code"


# ---- 13. dispatch passes the stdin payload through ---------------------------------------------------------------


def test_dispatch_passes_the_stdin_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import sys

    import supamem.hooks.claude_code as hook
    from supamem.hooks import dispatch

    seen: dict[str, Any] = {}

    def fake_run(file_path: Path, config: ResolvedConfig, payload: dict | None = None) -> int:
        seen["path"] = file_path
        seen["payload"] = payload
        return 0

    monkeypatch.setattr(hook, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": "sd1", "tool_input": {"file_path": "/r/src/k.ts"}})),
    )
    assert dispatch(client="claude-code", file_path=None, config=_cfg()) == 0
    assert seen["path"] == Path("/r/src/k.ts")
    assert seen["payload"]["session_id"] == "sd1"
