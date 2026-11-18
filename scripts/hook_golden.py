#!/usr/bin/env python3
"""Hook-channel golden set: build + evaluate (v0.4.0a7, docs/usefulness-metrics.md gap #4).

Build: mines the injection journal + Claude Code transcripts with the SAME use-detection
as `supamem hook-stats` (stats/injections.py) and emits golden rows
{query, repo, collection, embedder, positives[], channel, lang}. Therapy is EXCLUDED
(personal medical notes are never re-read or shipped by tooling).

Evaluate: runs each golden query through the shared daemon with BOTH the local mxbai
reranker and a remote reranker, reports recall@5 / MRR per provider, the per-provider
score distributions of POSITIVES, and the floor (min_score) that keeps >=90% of them.

Usage:
  uv run python scripts/hook_golden.py build  --out /tmp/golden.jsonl
  uv run python scripts/hook_golden.py eval   --golden /tmp/golden.jsonl \
      --remote-model cohere/rerank-5-fast [--limit 100]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from supamem.stats.injections import (  # noqa: E402
    _chunks_used,
    _iter_events,
    default_transcript_roots,
    find_transcript,
)

EXCLUDED_REPOS = ("therapy",)  # privacy: medical notes never leave the analysis box


def _repo_of(target: str) -> str:
    parts = Path(target).expanduser().parts
    if "Coding" in parts:
        i = parts.index("Coding")
        if i + 1 < len(parts):
            return parts[i + 1]
    return "?"


# repo → (collection, embedder) — mirrors each repo's .supamem/config.toml (a5/a7 rollout).
REPO_CONFIG = {
    "supamem": ("dev_memory_tuned_hybrid", "minilm"),
    "cl-harness": ("supamem-cl-harness-ml", "multilingual_minilm"),
    "ferrohex": ("supamem-ferrohex-ml", "multilingual_minilm"),
    "softchat": ("supamem-softchat", "minilm"),
}


def _lang(text: str) -> str:
    cyr = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    alpha = sum(1 for c in text if c.isalpha())
    return "ru" if alpha and cyr / alpha > 0.3 else "en"


def build(out: Path) -> None:
    journal = Path.home() / "Library" / "Caches" / "supamem" / "injections.jsonl"
    roots = default_transcript_roots()
    events_cache: dict[str, list[dict[str, Any]]] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}  # (query, repo) → golden row
    stats = defaultdict(int)

    for line in journal.open(encoding="utf-8", errors="replace"):
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if row.get("kind") == "mcp":
            continue
        session = str(row.get("session") or "")
        if not session:
            continue
        repo = _repo_of(str(row.get("target") or ""))
        if repo in EXCLUDED_REPOS or repo not in REPO_CONFIG:
            stats["repo_skip"] += 1
            continue
        if session not in events_cache:
            t = find_transcript(session, roots)
            events_cache[session] = _iter_events(t) if t else []
        events = events_cache[session]

        for channel, chunks in (
            ("main", row.get("chunks") or []),
            ("shadow", (row.get("shadow") or {}).get("chunks") or []),
        ):
            if not chunks:
                continue
            probe = {"ts": row.get("ts"), "chunks": chunks}
            if not _chunks_used(probe, events):
                continue
            query = str(
                row.get("query") if channel == "main" else (row.get("shadow") or {}).get("query") or ""
            ).strip()
            if not query or len(query) < 4:
                continue
            positives = sorted(
                {
                    str(c.get("source") or "")
                    for c in chunks
                    if isinstance(c, dict) and c.get("source") and c.get("source") != "?"
                }
            )
            if not positives:
                continue
            stats[f"used_{channel}"] += 1
            key = (query, repo)
            g = rows.setdefault(
                key,
                {
                    "query": query,
                    "repo": repo,
                    "collection": REPO_CONFIG[repo][0],
                    "embedder": REPO_CONFIG[repo][1],
                    "positives": [],
                    "channels": [],
                    "lang": _lang(query),
                },
            )
            for p in positives:
                if p not in g["positives"]:
                    g["positives"].append(p)
            if channel not in g["channels"]:
                g["channels"].append(channel)

    with out.open("w", encoding="utf-8") as fh:
        for g in sorted(rows.values(), key=lambda r: (r["repo"], r["query"])):
            fh.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(
        f"golden rows: {len(rows)} | positives: {sum(len(g['positives']) for g in rows.values())} "
        f"| {dict(stats)}",
        file=sys.stderr,
    )
    by_repo = defaultdict(int)
    for g in rows.values():
        by_repo[g["repo"]] += 1
    print("by repo:", dict(by_repo), file=sys.stderr)


def _query_provider(cfg_overrides: dict[str, Any], text: str, k: int) -> list[Any]:
    from supamem.config import ResolvedConfig
    from supamem.daemon import client as daemon_client

    cfg = ResolvedConfig(**cfg_overrides)
    return daemon_client.query(cfg, text, k, timeout=90.0)


def _rrank(hit: Any, positives: set[str]) -> int | None:
    src = hit.source_path or hit.file_path or ""
    if not src:
        return None
    for p in positives:
        try:
            a, b = Path(src).resolve(), Path(p).resolve()
            if a == b:
                return 1
        except OSError:
            continue
        na, nb = src.replace("\\", "/"), p.replace("\\", "/")
        if na == nb or (nb.endswith(na) or na.endswith(nb)):
            return 1
    return None


def eval_providers(golden_path: Path, remote_model: str, limit: int) -> None:
    golden = [json.loads(l) for l in golden_path.open(encoding="utf-8")][:limit]
    base = {
        "local": {
            "qdrant_url": "http://localhost:6333",
            "reranker_name": "mxbai_v2",
            "reranker_model_id": "mixedbread-ai/mxbai-rerank-base-v2",
            "reranker_top_n": 30,
        },
        "remote": {
            "qdrant_url": "http://localhost:6333",
            "reranker_name": "openrouter",
            "reranker_model_id": remote_model,
            "reranker_top_n": 30,
        },
    }
    results: dict[str, dict[str, Any]] = {
        p: {"recall5": 0, "mrr": 0.0, "n": 0, "pos_scores": [], "lat_ms": []} for p in base
    }
    for g in golden:
        for prov, over in base.items():
            cfg = dict(collection=g["collection"], embedder=g["embedder"], **over)
            try:
                t0 = time.monotonic()
                hits = _query_provider(cfg, g["query"], 10)
                dt = (time.monotonic() - t0) * 1000
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {prov} {g['repo']} {g['query'][:40]!r}: {exc}", file=sys.stderr)
                continue
            r = results[prov]
            r["n"] += 1
            r["lat_ms"].append(dt)
            first = None
            for i, h in enumerate(hits[:5]):
                rk = _rrank(h, set(g["positives"]))
                if rk and first is None:
                    first = i + 1
            if first:
                r["recall5"] += 1
                r["mrr"] += 1.0 / first
            for h in hits[:5]:
                src = h.source_path or h.file_path or ""
                if _rrank(h, set(g["positives"])):
                    if h.rerank_score is not None:
                        r["pos_scores"].append(float(h.rerank_score))

    print(f"\n=== golden n={len(golden)} remote_model={remote_model} ===")
    print(f"{'provider':8s} {'n':>4s} {'recall@5':>9s} {'MRR':>6s} {'p50ms':>7s} {'p90ms':>7s}")
    for prov, r in results.items():
        if not r["n"]:
            continue
        lats = sorted(r["lat_ms"])
        print(
            f"{prov:8s} {r['n']:4d} {r['recall5'] / r['n']:9.3f} {r['mrr'] / r['n']:6.3f} "
            f"{lats[len(lats) // 2]:7.0f} {lats[int(len(lats) * 0.9)]:7.0f}"
        )
        scores = sorted(r["pos_scores"])
        if scores:
            print(
                f"         positive rerank scores: min={scores[0]:.3f} "
                f"p10={scores[int(len(scores) * 0.1)]:.3f} median={scores[len(scores) // 2]:.3f} "
                f"max={scores[-1]:.3f} (n={len(scores)})"
            )
            keep90 = scores[int(len(scores) * 0.1)]  # floor keeping >=90% of positives
            print(f"         suggested min_score (keeps >=90% positives): {keep90:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("eval")
    e.add_argument("--golden", type=Path, required=True)
    e.add_argument("--remote-model", default="cohere/rerank-5-fast")
    e.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.out)
    else:
        eval_providers(args.golden, args.remote_model, args.limit)


if __name__ == "__main__":
    main()
