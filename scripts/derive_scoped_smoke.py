"""Manual derivation script for the bundled scoped-smoke fixture (Phase 14, Plan C).

Run once, commit output. **NOT shipped in the wheel** (lives under repo-root
``scripts/``, which is excluded from ``[tool.hatch.build.targets.wheel].packages``
in ``pyproject.toml`` — the wheel only packages ``src/supamem``). **NOT invoked
by CI.** Re-derivation is a manual operation per D-SMOKE-02.

Pipeline (per RESEARCH §Q5 + D-SMOKE-01..04):

1. Load full LongMemEval_S via :func:`supamem.eval.datasets.longmemeval_loader.iter_raw_longmemeval`
   (raw shape — we need ``haystack_session_ids`` + ``haystack_sessions`` paired).
2. Spin up a fresh local Qdrant (``docker compose up qdrant``) and call
   :func:`supamem.eval.longmemeval_ingest.ingest` on the records to bootstrap
   the bench collection (D-SCOPE-05 isolated ``supamem_eval_longmemeval_s``).
3. For each question, run unscoped (``backend.query(q, k=5)``) and scoped
   (``backend.query(q, k=5, where={"session_id": question.sessions})``) passes.
4. Compute per-pass tpca via the same formulas the runner uses
   (:func:`supamem.eval.runner._estimate_tokens` +
   :func:`supamem.eval.runner._heuristic_recall_at_5`).
5. Filter to questions where ``scoped.tpca < unscoped.tpca`` by >= 10% relative.
6. Pick top-5 by margin, axis-diversified (<=2 per axis).
7. For each picked question, capture the haystack content for ONLY the
   sessions referenced by ``question.sessions`` (truncate per turn to <=200
   chars to stay under 200 KB total).
8. Emit ``src/supamem/eval/datasets/longmemeval_scoped_smoke.json`` matching
   the schema in :file:`.planning/phases/14-bench-harness-where-filter-pass/14-RESEARCH.md`
   Q5.
9. Print a one-line JSON summary to stdout (question count, total bytes,
   expected mean gain). Status messages route through
   :data:`supamem.console.err_console` per CLAUDE.md (NEVER bare ``print``
   for non-stdout chatter).

Usage::

    python scripts/derive_scoped_smoke.py \\
        --dataset-path ~/.cache/supamem/datasets/longmemeval/<sha>/ \\
        --output src/supamem/eval/datasets/longmemeval_scoped_smoke.json \\
        --qdrant-url http://localhost:6333

The script is intentionally light on configurability — it encodes the
locked decisions (D-SMOKE-01..04, D-SCOPE-01..05) so re-runs produce a
diffable fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Note: imports of supamem.* are deferred into main() so --help works even
# in a partial-install dev environment.

_TURN_TRUNCATE_CHARS = 200  # D-SMOKE-01 size lock — keeps fixture under 200 KB.
_GAIN_FLOOR_RELATIVE = 0.10  # 10% relative gain threshold per Plan C.
_PICK_TOP_N = 5  # D-SMOKE-01 hard ceiling.
_PER_AXIS_CAP = 2  # axis-diversification rule.
_TOLERANCE_RELATIVE = 0.05  # D-SMOKE-01 — locked at 5% relative.


def _err(msg: str) -> None:
    """Route status messages through ``err_console`` per CLAUDE.md."""
    from supamem.console import err_console

    err_console.print(msg)


def _truncate_turn(role: str, content: str) -> dict[str, str]:
    """Truncate a haystack turn's content to the size lock."""
    if len(content) > _TURN_TRUNCATE_CHARS:
        content = content[: _TURN_TRUNCATE_CHARS - 1] + "…"
    return {"role": role, "content": content}


def _pair_haystack(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair ``haystack_session_ids[i]`` with ``haystack_sessions[i]``."""
    sids = raw.get("haystack_session_ids") or []
    sessions = raw.get("haystack_sessions") or []
    out: list[dict[str, Any]] = []
    for sid, turns in zip(sids, sessions):
        if not isinstance(sid, str) or not turns:
            continue
        out.append(
            {
                "session_id": sid,
                "turns": [
                    _truncate_turn(str(t.get("role", "")), str(t.get("content", "")))
                    for t in turns
                    if isinstance(t, dict) and t.get("content")
                ],
            }
        )
    return out


def _measure(
    *,
    backend: Any,
    question: str,
    answer: str,
    where: dict[str, Any] | None,
) -> float:
    """Run one retrieval pass + compute tpca exactly as the runner does."""
    from supamem.eval.runner import _estimate_tokens, _heuristic_recall_at_5

    if where is not None:
        chunks = backend.query(question, k=5, where=where)
    else:
        chunks = backend.query(question, k=5)
    ctx_texts = [c.text or "" for c in chunks[:5]]
    in_tokens = _estimate_tokens(question) + sum(_estimate_tokens(t) for t in ctx_texts)
    recall = _heuristic_recall_at_5(chunks, answer)
    if recall > 0:
        return in_tokens / recall
    return float(in_tokens)


def _derive(
    *,
    dataset_path: Path,
    qdrant_url: str,
    output: Path,
    api_key: str | None,
) -> int:
    """Run the manual ritual end-to-end. Returns 0 on success, 1 on failure."""
    # Lazy imports — avoid pulling Qdrant client into --help.
    from qdrant_client import QdrantClient

    from supamem.config import ResolvedConfig
    from supamem.eval.datasets.longmemeval_loader import iter_raw_longmemeval
    from supamem.eval.longmemeval_ingest import eval_collection_name, ingest
    from supamem.eval.runner import _build_backend

    cfg = ResolvedConfig(qdrant_url=qdrant_url, qdrant_api_key=api_key or "")

    _err(
        f"[supamem.info]bench-derive: loading raw LongMemEval_S from "
        f"{dataset_path}[/supamem.info]"
    )
    raw_records = list(iter_raw_longmemeval(dataset_path=dataset_path))
    if not raw_records:
        _err("[supamem.err]bench-derive: no raw records loaded - abort[/supamem.err]")
        return 1

    _err(f"[supamem.info]bench-derive: {len(raw_records)} raw records loaded[/supamem.info]")

    client = QdrantClient(
        url=qdrant_url,
        api_key=api_key or None,
        check_compatibility=False,
        timeout=120,
    )
    bench_coll = eval_collection_name(cfg, "longmemeval_s")
    _err(f"[supamem.info]bench-derive: ingesting into {bench_coll}[/supamem.info]")
    upserted = ingest(cfg, raw_records, client=client, suite="longmemeval_s")
    _err(f"[supamem.info]bench-derive: upserted {upserted} chunks[/supamem.info]")

    backend = _build_backend(cfg, suite="longmemeval_s")

    candidates: list[dict[str, Any]] = []
    # Re-use the canonical alias map from the loader once.
    from supamem.eval.datasets.longmemeval_loader import _AXIS_ALIAS

    for raw in raw_records:
        sessions = raw.get("haystack_session_ids") or []
        if len(sessions) < 3:  # rich haystack only.
            continue
        question = str(raw.get("question") or "").strip()
        answer = str(raw.get("answer") or "").strip()
        axis_upstream = raw.get("question_type", "")
        axis = _AXIS_ALIAS.get(axis_upstream)
        if not axis:
            continue
        if not question or not answer:
            continue

        unscoped_tpca = _measure(backend=backend, question=question, answer=answer, where=None)
        scoped_tpca = _measure(
            backend=backend,
            question=question,
            answer=answer,
            where={"session_id": list(sessions)},
        )

        if unscoped_tpca <= 0:
            continue
        gain = (unscoped_tpca - scoped_tpca) / unscoped_tpca
        if gain < _GAIN_FLOOR_RELATIVE:
            continue
        candidates.append(
            {
                "id": raw["question_id"],
                "question": question,
                "answer": answer,
                "axis": axis,
                "sessions": list(sessions),
                "haystack": _pair_haystack(raw),
                "expected_unscoped_tpca": unscoped_tpca,
                "expected_scoped_tpca": scoped_tpca,
                "_gain": gain,
            }
        )

    if not candidates:
        _err(
            "[supamem.err]bench-derive: no questions met the >=10% scoped-gain floor"
            "[/supamem.err]"
        )
        return 1

    # Axis-diversify with a 2-per-axis cap, then pick top-N by gain.
    candidates.sort(key=lambda q: q["_gain"], reverse=True)
    by_axis: dict[str, int] = defaultdict(int)
    picked: list[dict[str, Any]] = []
    for cand in candidates:
        if len(picked) >= _PICK_TOP_N:
            break
        if by_axis[cand["axis"]] >= _PER_AXIS_CAP:
            continue
        picked.append(cand)
        by_axis[cand["axis"]] += 1

    if not picked:
        _err(
            "[supamem.err]bench-derive: axis-diversification yielded zero picks"
            "[/supamem.err]"
        )
        return 1

    # Strip the internal ``_gain`` scratch field before emitting.
    expected_gains = [p["_gain"] for p in picked]
    questions_out: list[dict[str, Any]] = []
    sessions_total: set[str] = set()
    for p in picked:
        sessions_total.update(p["sessions"])
        questions_out.append(
            {
                "id": p["id"],
                "question": p["question"],
                "answer": p["answer"],
                "axis": p["axis"],
                "sessions": p["sessions"],
                "haystack": p["haystack"],
                "expected_unscoped_tpca": round(p["expected_unscoped_tpca"], 4),
                "expected_scoped_tpca": round(p["expected_scoped_tpca"], 4),
            }
        )

    fixture = {
        "meta": {
            "source": "longmemeval_s slice",
            "session_count": len(sessions_total),
            "expected_scoped_gain_tpca": round(sum(expected_gains) / len(expected_gains), 4),
            "tolerance_relative": _TOLERANCE_RELATIVE,
        },
        "questions": questions_out,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    size_bytes = output.stat().st_size
    if size_bytes > 200 * 1024:
        _err(
            f"[supamem.warn]bench-derive: emitted fixture is {size_bytes} bytes "
            f"(> 200 KB ceiling). Tighten _TURN_TRUNCATE_CHARS or _PICK_TOP_N."
            "[/supamem.warn]"
        )

    summary = {
        "questions": len(questions_out),
        "bytes": size_bytes,
        "mean_gain_relative": round(sum(expected_gains) / len(expected_gains), 4),
        "axes": sorted({p["axis"] for p in picked}),
    }
    # Single sanctioned stdout print — the JSON summary is the script's
    # primary success signal, parseable by downstream tooling.
    print(json.dumps(summary))  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 14 Plan C - manual derivation of longmemeval_scoped_smoke.json"
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        required=True,
        help="Path to lazy-fetched LongMemEval_S corpus on local disk",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/supamem/eval/datasets/longmemeval_scoped_smoke.json"),
        help="Where to write the bundled smoke fixture",
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Local Qdrant URL (docker compose up qdrant)",
    )
    parser.add_argument(
        "--qdrant-api-key",
        default=None,
        help="Optional Qdrant API key",
    )
    args = parser.parse_args(argv)
    return _derive(
        dataset_path=args.dataset_path,
        qdrant_url=args.qdrant_url,
        output=args.output,
        api_key=args.qdrant_api_key,
    )


if __name__ == "__main__":
    sys.exit(main())
