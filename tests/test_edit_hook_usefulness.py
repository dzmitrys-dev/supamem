"""Edit-hook usefulness stage 1 (v0.4.0a4) — the owner-approved plan from
``cl-harness/research/research-supamem-effectiveness-2026-11-14.md``.

Baseline measured by that study: injection rate 4.2%, use rate 0.6%, repeat rate 65.6%,
stale rate 8.5%, worktree sessions 42% of calls and 100% empty, no score floor, no
usefulness metric. Each test here pins one fix; all failed before the change.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
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


# ---- 14. review-fix round: exactly ONE JSON per hook invocation ------------------------------------------------
# The success path emitted, then journaled INSIDE the outer try; a journal crash with a
# non-OSError (lone surrogate → UnicodeEncodeError, a ValueError) hit the outer handler and
# emitted a SECOND JSON line — two stdout lines violate the hook contract.


def test_raising_journal_still_emits_exactly_one_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)
    (proj / "docs" / "a.md").write_text("# A\n")
    _fake_backend(
        monkeypatch, [_chunk(str(proj / "docs" / "a.md"), "Body.", score=4.0, rerank=4.0)]
    )
    monkeypatch.chdir(proj)

    def boom(row: dict[str, Any], cache_dir: Path | None = None) -> None:
        raise ValueError("simulated journal crash (e.g. lone surrogate → UnicodeEncodeError)")

    monkeypatch.setattr(hook, "append_row", boom)
    assert (
        hook.run(proj / "src" / "x.py", _cfg(), payload={"session_id": "s-rj", "cwd": str(proj)})
        == 0
    )
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"exactly ONE stdout line is required, got {len(lines)}: {lines!r}"
    parsed = json.loads(lines[0])
    assert parsed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_journal_fail_soft_on_non_code_target_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import supamem.hooks.claude_code as hook

    proj = _project(tmp_path)

    def boom(row: dict[str, Any], cache_dir: Path | None = None) -> None:
        raise ValueError("journal crash before the emit")

    monkeypatch.setattr(hook, "append_row", boom)
    monkeypatch.chdir(proj)
    assert (
        hook.run(proj / "README.md", _cfg(), payload={"session_id": "s-rj2", "cwd": str(proj)}) == 0
    )
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, "even a pre-emit journal crash must leave exactly one JSON line"
    assert json.loads(lines[0])["hookSpecificOutput"]["additionalContext"] == ""


def test_append_row_survives_lone_surrogate(tmp_path: Path) -> None:
    """UnicodeEncodeError (a ValueError, NOT OSError) used to escape append_row."""
    from supamem.stats.injections import append_row

    row = {
        "ts": TS0,
        "session": "s-sur",
        "chunks": [{"source": "a.md", "score": 1.0, "exists": True, "lang": "en"}],
        "query": "bad \udce9 surrogate",
    }
    append_row(row, cache_dir=tmp_path)  # must not raise
    p = tmp_path / "injections.jsonl"
    line = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
    json.loads(line), "the journal file must stay line-parseable after the replacement"


def test_journal_row_tolerates_bad_score_values(tmp_path: Path) -> None:
    from supamem.hooks.claude_code import _journal_row

    root = _project(tmp_path)

    def row_for(score: Any) -> dict[str, Any]:
        # RetrievedChunk (pydantic) already rejects non-numeric scores at construction;
        # the stand-in pins the journal's OWN fail-softness should that guard ever loosen.
        odd = SimpleNamespace(source_path="a.md", file_path=None, text="x", score=score)
        return _journal_row(
            session="s",
            target="t",
            query="q",
            chunks=[odd],  # type: ignore[list-item]
            root=root,
            empty=False,
            deduped=0,
            dropped={},
            ms=1.0,
            shadow=None,
        )

    nan_row = row_for(float("nan"))
    assert nan_row["chunks"][0]["score"] == 0.0, (
        "NaN must not reach the journal (json 'NaN' is invalid)"
    )
    assert row_for("not-a-number")["chunks"][0]["score"] == 0.0
    assert row_for(None)["chunks"][0]["score"] == 0.0


# ---- 15. review-fix round: hook-stats survives naive timestamps and malformed rows ----------------------------


def test_hook_stats_handles_naive_timestamps_and_malformed_rows(tmp_path: Path) -> None:
    import supamem.stats.injections as inj

    naive_now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    aware_now = datetime.now(timezone.utc).isoformat()
    journal = tmp_path / "injections.jsonl"
    _write_row(journal, ts=naive_now)  # naive — no offset at all
    _write_row(
        journal,
        ts=aware_now,
        session="sess-naive-2",
        chunks=[],
        empty=True,
        deduped=2,
        target="/abs/proj/src/y.py",
    )
    with journal.open("a", encoding="utf-8") as fh:  # malformed rows: skip, never crash
        fh.write('{"ts": 123, "chunks": []}\n')  # non-string ts
        fh.write('{"ts": "not-a-date", "chunks": []}\n')  # unparseable ts
        fh.write("complete junk\n")  # not JSON
        fh.write("[]\n")  # not a dict
    _write_row(
        journal,
        ts=aware_now,
        session="sess-naive-3",
        chunks="junk-not-a-list",
        empty=True,
        deduped="abc",
        dropped="also-junk",
        target="/abs/proj/src/z.py",
    )
    stats = inj.compute_stats(days=7, journal_path=journal, transcript_roots=[tmp_path / "absent"])
    assert stats["invocations"] == 3, "naive-ts rows count; malformed rows are skipped"
    assert stats["dedup_suppressed_rate"] == pytest.approx(1 / 3, rel=1e-3)


# ---- 16. review-fix round: filter-aware metrics (finding: repeat_rate blind to the new filters) ----------------


def test_hook_stats_reports_dedup_and_stale_dropped_rates(tmp_path: Path) -> None:
    import supamem.stats.injections as inj

    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    t0 = now.isoformat()
    journal = tmp_path / "injections.jsonl"
    _write_row(journal, ts=t0)  # emitted a chunk (the only non_empty row)
    # ALL candidates deduped → chunks=[] but deduped=3 (today invisible to repeat_rate)
    _write_row(
        journal,
        ts=t0,
        session="sess-dedup",
        chunks=[],
        empty=True,
        deduped=3,
        target="/abs/proj/src/b.py",
    )
    # everything stale-dropped → chunks=[], dropped.stale=2
    _write_row(
        journal,
        ts=t0,
        session="sess-stale",
        chunks=[],
        empty=True,
        deduped=0,
        dropped={"stale": 2, "floor": 0, "foreign": 0},
        target="/abs/proj/src/c.py",
    )
    stats = inj.compute_stats(days=7, journal_path=journal, transcript_roots=[tmp_path / "absent"])
    assert stats["non_empty"] == 1
    assert stats["repeat_rate"] == 0.0, "old metric keeps its emitted-rows-only denominator"
    assert stats["dedup_candidates"] == 3, (
        "denominator: rows that had any candidate after the floor"
    )
    assert stats["dedup_suppressed_rate"] == pytest.approx(1 / 3, rel=1e-3)
    assert stats["stale_dropped_rate"] == pytest.approx(1 / 3, rel=1e-3)
    assert stats["baselines"] == {"repeat_rate": 0.656, "stale_rate": 0.085}


def test_hook_stats_text_output_compares_to_research_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from supamem.cli import app

    fake = {
        "journal_path": "/tmp/j.jsonl",
        "window_days": 7.0,
        "invocations": 10,
        "non_empty": 4,
        "injection_rate": 0.4,
        "use_rate": 0.25,
        "used_events": 1,
        "useful_per_100_edits": 10.0,
        "repeat_rate": 0.1,
        "dedup_suppressed_rate": 0.5,
        "dedup_candidates": 8,
        "stale_rate": 0.0,
        "stale_dropped_rate": 0.125,
        "lang_mismatch_rate": 0.0,
        "shadow_rows": 2,
        "shadow_use_rate": 0.5,
        "baselines": {"repeat_rate": 0.656, "stale_rate": 0.085},
    }
    monkeypatch.setattr("supamem.stats.injections.compute_stats", lambda **kw: dict(fake))
    result = CliRunner().invoke(app, ["hook-stats"])
    assert result.exit_code == 0, result.output
    assert "dedup_suppressed_rate" in result.stdout
    assert "stale_dropped_rate" in result.stdout
    assert "65.6" in result.stdout, "the text report must name the baseline repeats figure"
    assert "8.5" in result.stdout, "the text report must name the baseline stale figure"


# ---- 17. review-fix round: use heuristic matches on path-component boundaries ----------------------------------


def test_same_target_matches_on_path_component_boundary() -> None:
    from supamem.stats.injections import _same_target

    assert not _same_target("/repo/src/myapp.py", "app.py"), (
        "suffix-of-a-longer-name is NOT a match"
    )
    assert not _same_target("app.py", "/repo/src/myapp.py")
    assert _same_target("src/app.py", "/repo/src/app.py"), "relative tail on a boundary matches"
    assert _same_target("/repo/src/app.py", "src/app.py")
    assert _same_target("/repo/src/app.py", "/repo/./src/app.py")
    assert _same_target("docs/a.md", "docs/a.md")
    assert not _same_target("src/app.py", "src/other/app.py")


def test_prose_mention_requires_boundary_anchored_basename() -> None:
    import supamem.stats.injections as inj

    row = {
        "ts": TS0,
        "chunks": [{"source": "/abs/proj/src/app.py", "score": 3.0, "exists": True, "lang": "en"}],
    }
    hits = [
        {"ts": None, "kind": "text", "path": "", "text": "per src/app.py the router is X"},
        {"ts": None, "kind": "text", "path": "", "text": "app.py is the entrypoint"},
    ]
    assert inj._chunks_used(row, hits) is True
    misses = [
        {"ts": None, "kind": "text", "path": "", "text": "myapp.py handles the routing"},
        {"ts": None, "kind": "text", "path": "", "text": "snapshot at app.py.bak is stale"},
        {"ts": None, "kind": "text", "path": "", "text": "see my_app.py for details"},
    ]
    assert inj._chunks_used(row, misses) is False, (
        "a longer name sharing the basename tail must NOT count as use"
    )


# ---- 18. review-fix round: [supamem.hook] values are validated (fail closed) at load ---------------------------


@pytest.mark.parametrize(
    ("toml_line", "needle"),
    [
        ("fetch_k = 0", "fetch_k"),
        ("fetch_k = -3", "fetch_k"),
        ('fetch_k = "abc"', "fetch_k"),
        ("fetch_k = 30.5", "fetch_k"),
        ("inject_k = 0", "inject_k"),
        ("inject_k = 40", "inject_k"),  # > fetch_k default 30
        ("min_score = -1.0", "min_score"),
        ('min_score = "x"', "min_score"),
        ('shadow_task_query = "false"', "shadow_task_query"),
        ("shadow_task_query = 1", "shadow_task_query"),
    ],
)
def test_hook_config_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    toml_line: str,
    needle: str,
) -> None:
    from supamem.config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f"[supamem.hook]\n{toml_line}\n")
    monkeypatch.setenv("SUPAMEM_CONFIG", str(cfg_path))
    with pytest.raises(SystemExit) as ei:
        load_config(tmp_path)
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert needle in err, f"the gate must name the offending key {needle!r}: {err!r}"
    assert "supamem.hook" in err


def test_hook_config_accepts_valid_boundary_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supamem.config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[supamem.hook]\nfetch_k = 1\ninject_k = 1\nmin_score = 0\nshadow_task_query = false\n"
    )
    monkeypatch.setenv("SUPAMEM_CONFIG", str(cfg_path))
    cfg, _chain = load_config(tmp_path)
    assert cfg.hook_fetch_k == 1 and cfg.hook_inject_k == 1
    assert cfg.hook_min_score == 0 and cfg.hook_shadow_task_query is False


# ---- 19. review-fix round: dedup marker races + expired-marker sweep -------------------------------------------


def test_save_seen_remerges_ids_saved_between_read_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parallel hook of the SAME session can save between our read and the replace.

    Deterministic simulation: the first load_seen inside save_seen sees {a}; by the time
    the replace happens a parallel hook has persisted {b}. The merge must re-read before
    replacing so {b} survives.
    """
    from supamem.stats import injections as inj

    inj.save_seen("s-race", {"a"}, cache_dir=tmp_path)
    real_load = inj.load_seen
    calls = {"n": 0}

    def flaky_load(session: str, cache_dir: Path | None = None) -> set[str]:
        calls["n"] += 1
        base = real_load(session, cache_dir)
        return base | {"b"} if calls["n"] >= 2 else base

    monkeypatch.setattr(inj, "load_seen", flaky_load)
    inj.save_seen("s-race", {"x"}, cache_dir=tmp_path)
    monkeypatch.undo()
    assert {"a", "b", "x"} <= real_load("s-race", tmp_path), (
        "ids persisted by a parallel hook between read and replace must not be lost"
    )


def test_save_seen_parallel_writers_keep_union(tmp_path: Path) -> None:
    from supamem.stats import injections as inj

    errors: list[Exception] = []

    def writer(ids: set[str]) -> None:
        try:
            for _ in range(20):
                inj.save_seen("s-par", ids, cache_dir=tmp_path)
        except Exception as exc:  # noqa: BLE001 — collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=({f"id{i:02d}"},)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"parallel saves must not raise: {errors}"
    assert {f"id{i:02d}" for i in range(8)} <= inj.load_seen("s-par", tmp_path), (
        "every writer's ids must land in the marker despite the shared-session race"
    )


def test_save_seen_sweeps_expired_markers_bounded(tmp_path: Path) -> None:
    from supamem.stats import injections as inj

    old_ts = time.time() - 25 * 3600
    fresh = tmp_path / "dedup-fresh.json"
    fresh.write_text('{"ts": 1, "ids": ["k"]}', encoding="utf-8")
    for i in range(55):
        old = tmp_path / f"dedup-old-{i:02d}.json"
        old.write_text('{"ts": 0, "ids": []}', encoding="utf-8")
        os.utime(old, (old_ts, old_ts))
    inj.save_seen("s-sweep", {"x"}, cache_dir=tmp_path)
    remaining = sorted(tmp_path.glob("dedup-old-*.json"))
    assert len(remaining) == 5, "the sweep removes at most 50 expired markers per call"
    assert fresh.exists(), "a live marker must survive the sweep"
    inj.save_seen("s-sweep", {"y"}, cache_dir=tmp_path)
    assert not list(tmp_path.glob("dedup-old-*.json")), "a later save finishes the sweep"


# ---- 20. review-fix round: _iter_events streams the transcript -------------------------------------------------


def test_iter_events_streams_without_a_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import supamem.stats.injections as inj

    t = tmp_path / "big.jsonl"
    line = json.dumps(
        {
            "type": "assistant",
            "timestamp": TS0,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        }
    )
    t.write_text((line + "\n") * 50, encoding="utf-8")

    def no_read_text(self: Path, *a: Any, **k: Any) -> str:
        raise AssertionError("_iter_events must stream line by line, not read_text the file")

    monkeypatch.setattr(Path, "read_text", no_read_text)
    events = inj._iter_events(t)
    monkeypatch.undo()
    assert len(events) == 50


# ---- 21. review round 2: non-finite / unbounded gate values, anchored marker sweep ------------------------------


@pytest.mark.parametrize(
    ("toml_line", "needle"),
    [
        ("min_score = inf", "min_score"),
        ("min_score = nan", "min_score"),
        ("min_score = 1e400", "min_score"),
        ("fetch_k = 1000000000", "fetch_k"),
    ],
)
def test_hook_config_rejects_non_finite_and_unbounded_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    toml_line: str,
    needle: str,
) -> None:
    from supamem.config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f"[supamem.hook]\n{toml_line}\n")
    monkeypatch.setenv("SUPAMEM_CONFIG", str(cfg_path))
    with pytest.raises(SystemExit) as ei:
        load_config(tmp_path)
    assert ei.value.code == 2
    assert needle in capsys.readouterr().err


def test_marker_sweep_only_deletes_supamem_markers(tmp_path: Path) -> None:
    """A shared audit dir may hold foreign files that match a loose glob; the sweep deletes
    only files that are supamem dedup markers (marker schema) or supamem's own tmp names."""
    from supamem.stats import injections as inj

    old_ts = time.time() - 25 * 3600
    marker = tmp_path / "dedup-old.json"
    marker.write_text('{"ts": 0, "ids": []}', encoding="utf-8")
    own_tmp = tmp_path / ".dedup-old.json.abc123.tmp"
    own_tmp.write_text("{", encoding="utf-8")
    foreign_json = tmp_path / "dedup-notes.json"
    foreign_json.write_text('{"title": "my notes"}', encoding="utf-8")
    foreign_tmp = tmp_path / "upload.tmp"
    foreign_tmp.write_text("x", encoding="utf-8")
    for f in (marker, own_tmp, foreign_json, foreign_tmp):
        os.utime(f, (old_ts, old_ts))
    inj._sweep_expired_markers(tmp_path)
    assert not marker.exists() and not own_tmp.exists()
    assert foreign_json.exists(), "a non-marker dedup-*.json must survive"
    assert foreign_tmp.exists(), "a foreign *.tmp must survive"
