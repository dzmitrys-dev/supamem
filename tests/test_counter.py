"""Tests for ``supamem.stats.counter`` (Plan 80.6-06).

Locks the schema-v2 Welford aggregate contract: per-call JSONL audit + a
per-(kind, source) aggregate file with sum/sumsq/count/min/max. v1 flat
``{"<kind>:<source>": <count>}`` upgrades on first read. Counter must
never raise — failures are logged and swallowed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from supamem.stats import render
from supamem.stats.counter import bump


def _aggregates(cache_dir: Path) -> dict:
    p = cache_dir / "aggregates.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _jsonl_lines(cache_dir: Path) -> list[dict]:
    p = cache_dir / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_bump_creates_aggregates_and_jsonl(tmp_path: Path) -> None:
    bump("search", "mcp_softchat", tokens=100, latency_ms=12.5, cache_dir=tmp_path)
    assert (tmp_path / "aggregates.json").exists()
    assert (tmp_path / "audit.jsonl").exists()


def test_bump_appends_jsonl_line_per_call(tmp_path: Path) -> None:
    for i in range(3):
        bump("search", "mcp", tokens=10 + i, latency_ms=1.0, cache_dir=tmp_path)
    lines = _jsonl_lines(tmp_path)
    assert len(lines) == 3
    for line in lines:
        assert line["kind"] == "search"
        assert line["source"] == "mcp"
        assert "ts" in line


def test_bump_aggregates_welford(tmp_path: Path) -> None:
    """Three calls with tokens [10,20,30] → sum=60, count=3, mean=20, max=30, min=10."""
    for tok in (10, 20, 30):
        bump("search", "mcp", tokens=tok, latency_ms=1.0, cache_dir=tmp_path)
    agg = _aggregates(tmp_path)["search:mcp"]
    assert agg["sum"] == 60
    assert agg["count"] == 3
    assert agg["min"] == 10
    assert agg["max"] == 30
    assert agg["sumsq"] == 100 + 400 + 900


def test_bump_v1_v2_migration(tmp_path: Path) -> None:
    """Pre-write a v1 flat {"<kind:source>": int} file; first bump migrates schema."""
    legacy = tmp_path / "aggregates.json"
    legacy.write_text(json.dumps({"search:mcp_softchat": 5}), encoding="utf-8")
    bump("search", "mcp_softchat", tokens=20, latency_ms=2.0, cache_dir=tmp_path)
    agg = _aggregates(tmp_path)["search:mcp_softchat"]
    assert agg["count"] == 6
    assert agg["sum"] == 25
    assert "sumsq" in agg


def test_bump_failsoft_on_locked_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If fcntl.flock raises, bump must log and return — never propagate."""
    import fcntl

    def boom(*_args, **_kwargs):
        raise OSError("locked")

    monkeypatch.setattr(fcntl, "flock", boom)
    bump("search", "mcp", tokens=1, latency_ms=0.1, cache_dir=tmp_path)


def test_render_today_filters_by_date(tmp_path: Path) -> None:
    """JSONL across two dates — render('today') only counts today's lines."""
    import datetime as dt

    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(
            json.dumps(rec)
            for rec in [
                {"ts": f"{yesterday}T10:00:00", "kind": "search", "source": "mcp", "tokens": 5, "latency_ms": 1},
                {"ts": f"{today}T10:00:00", "kind": "search", "source": "mcp", "tokens": 7, "latency_ms": 2},
                {"ts": f"{today}T11:00:00", "kind": "search", "source": "mcp", "tokens": 9, "latency_ms": 3},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = render(show="today", format="json", cache_dir=tmp_path)
    data = json.loads(out)
    rec = data["search:mcp"]
    assert rec["count"] == 2
    assert rec["tokens_total"] == 16


def test_render_table_format_contains_p95(tmp_path: Path) -> None:
    """Table render must include a p95 column header."""
    import datetime as dt

    today = dt.date.today().isoformat()
    audit = tmp_path / "audit.jsonl"
    rows = [
        json.dumps({"ts": f"{today}T10:0{i}:00", "kind": "search", "source": "mcp",
                    "tokens": 10, "latency_ms": float(i)})
        for i in range(5)
    ]
    audit.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = render(show="today", format="table", cache_dir=tmp_path)
    assert "p95" in out.lower()
