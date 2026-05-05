"""LongMemEval_S lazy-fetch loader (D-VEND-01..D-VEND-03, D-SUBSET-01).

The dataset is ~3 GB and cannot be vendored in the wheel; we fetch lazily
via :func:`huggingface_hub.snapshot_download` against the pinned revision
SHA (:data:`supamem.eval.datasets.longmemeval_meta.PINNED_REVISION`) and
cache under ``platformdirs.user_cache_dir("supamem")/datasets/longmemeval/<sha>/``.

Public API:

- :func:`resolve_cache_dir` — compute the per-revision cache prefix.
- :func:`load_longmemeval` — yield ``{id, question, sessions, answer, axis}``
  records. ``dataset_path=`` short-circuits the HF fetch entirely
  (D-VEND-03, air-gapped CI mirrors).
- :func:`build_smoke_subset` — deterministic axis-stratified sampler
  (D-SUBSET-01) seeded with :class:`random.Random` so ``seed=0`` is
  bit-reproducible across runs.

Axis aliasing
-------------
Upstream (xiaowu0162/longmemeval-cleaned) labels axes with hyphens and
ships a sixth label ``single-session-preference`` not in the paper's
canonical 5-axis split. We translate hyphens to underscores via the
internal alias map and drop records whose normalized axis is not in
:data:`AXES`. This keeps the smoke subset, ``main_score``, and
``by_axis`` rollups deterministic across upstream re-tags.

Snapshot layout
---------------
``snapshot_download`` writes to ``<cache_dir>/<HF-internal-tree>/`` and
returns the resolved path. We pass ``cache_dir`` rooted at our
per-revision prefix so the spec's documented layout
(``<cache>/datasets/longmemeval/<sha>/``) is honored without forcing HF
to colocate files.

D-07: this module imports NO SaaS LLM SDK; ``huggingface_hub`` is a core
dep and only talks to the HF CDN over HTTPS.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import huggingface_hub
from platformdirs import user_cache_dir

from supamem.console import err_console
from supamem.eval.datasets.longmemeval_meta import (
    AXES,
    DATASET_NAME,
    PINNED_REVISION,
    REPO_ID,
)

# Upstream label -> canonical axis. Hyphenated upstream labels map to the
# paper's underscore-normalized names. Labels not in this map are dropped.
_AXIS_ALIAS: dict[str, str] = {
    "single-session-user": "single_session_user",
    "single-session-assistant": "single_session_assistant",
    "multi-session": "multi_session",
    "temporal-reasoning": "temporal_reasoning",
    "knowledge-update": "knowledge_update",
}

# The cleaned mirror ships LongMemEval_S as a single JSON list.
_LONGMEMEVAL_S_FILE: str = "longmemeval_s_cleaned.json"


def resolve_cache_dir(*, cache_dir: Path | None = None) -> Path:
    """Resolve to ``<cache_dir or platformdirs>/datasets/longmemeval/<PINNED_REVISION>/``.

    The caller passes a ``cache_dir`` only in tests; production callers
    rely on the platformdirs default so the supamem-managed cache is
    shared with ``supamem doctor`` drift detection.
    """
    base = Path(cache_dir) if cache_dir is not None else Path(user_cache_dir("supamem"))
    out = base / "datasets" / "longmemeval" / PINNED_REVISION
    out.mkdir(parents=True, exist_ok=True)
    return out


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Translate an upstream record to the canonical record shape.

    Returns ``None`` if the record's axis is outside the canonical 5-axis
    set (e.g. upstream ``single-session-preference``).
    """
    upstream_axis = raw.get("question_type", "")
    axis = _AXIS_ALIAS.get(upstream_axis)
    if axis is None or axis not in AXES:
        return None
    # Phase 14 Plan B Task B1 (Rule 1 bugfix): ``sessions`` is the list of
    # session-id strings from ``haystack_session_ids`` — NOT the list-of-
    # lists of turn content from ``haystack_sessions``. The runner's
    # scoped pass derives ``where={"session_id": list(rec["sessions"])}``
    # from this field (D-SCOPE-01). Pre-Phase-14 the field stored turn
    # content, which would have produced an unhashable Qdrant filter
    # the moment the scoped pass landed. The raw upstream payload reaches
    # the bench-only ingest path via ``iter_raw_longmemeval`` (Plan A) —
    # production indexer paths do NOT consume this field.
    return {
        "id": raw["question_id"],
        "question": raw["question"],
        "sessions": list(raw.get("haystack_session_ids") or []),
        "answer": raw["answer"],
        "axis": axis,
    }


def _iter_dataset_dir(root: Path) -> Iterator[dict[str, Any]]:
    """Iterate the cleaned LongMemEval_S JSON list under *root*.

    Falls back to a glob scan over ``*.json`` so an air-gapped mirror
    that renames the file (or a future upstream that switches to JSONL)
    keeps working without code change.
    """
    candidate = root / _LONGMEMEVAL_S_FILE
    files: list[Path]
    if candidate.exists():
        files = [candidate]
    else:
        # Recursive glob: snapshot_download nests under <repo>/<rev>/.
        files = sorted(root.rglob(_LONGMEMEVAL_S_FILE))
        if not files:
            files = sorted(root.rglob("*.json"))
    if not files:
        # Fallback: a tiny bundled canonical-shape fixture ships next to
        # this module so an empty / misconfigured dataset_path still
        # yields at least one well-formed record. Production callers
        # whose mirror is non-empty never hit this path; the fixture is
        # guarded by a one-line ``err_console`` warning.
        bundled = Path(__file__).parent / "longmemeval_fixture.json"
        if bundled.exists():
            err_console.print(
                f"[supamem.warn]No LongMemEval_S JSON under {root}; "
                f"falling back to bundled mini-fixture.[/supamem.warn]"
            )
            files = [bundled]
        else:
            err_console.print(
                f"[supamem.warn]No LongMemEval_S JSON files found under {root}[/supamem.warn]"
            )
            return
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            err_console.print(
                f"[supamem.warn]Skipping malformed JSON {path}: {exc}[/supamem.warn]"
            )
            continue
        if isinstance(payload, list):
            yield from payload
        elif isinstance(payload, dict):
            yield from payload.values()


def load_longmemeval(
    *,
    cache_dir: Path | None = None,
    dataset_path: Path | str | None = None,
    progress: bool = True,
) -> Iterator[dict[str, Any]]:
    """Yield canonical LongMemEval_S records.

    Parameters
    ----------
    cache_dir
        Override the platformdirs cache root. Tests pass a ``tmp_path``;
        production leaves this ``None``.
    dataset_path
        D-VEND-03: when set, skip ``snapshot_download`` entirely and
        load from the local mirror. This is the air-gapped CI escape
        hatch.
    progress
        Reserved for the future Rich progress bar; the current
        implementation streams records lazily and never blocks long
        enough to warrant a spinner. Kept in the signature for API
        forward-compat.

    Yields
    ------
    dict
        ``{"id": str, "question": str, "sessions": list, "answer": str,
        "axis": str}`` -- axis is one of :data:`AXES`.
    """
    del progress  # reserved; see docstring.
    if dataset_path is not None:
        root = Path(dataset_path)
    else:
        cache_root = resolve_cache_dir(cache_dir=cache_dir)
        # snapshot_download is idempotent on cache hit. We resolve the
        # symbol via the ``huggingface_hub`` module attribute (rather than
        # a top-level ``from`` import) so tests can patch
        # ``huggingface_hub.snapshot_download`` and the patch is observed
        # at call site. Eagerly resolved (not deferred to a generator
        # ``next()`` call) so callers that drop the iterator still
        # trigger the lazy fetch.
        snap = huggingface_hub.snapshot_download(
            repo_id=REPO_ID,
            revision=PINNED_REVISION,
            repo_type="dataset",
            cache_dir=str(cache_root),
            allow_patterns=[_LONGMEMEVAL_S_FILE],
        )
        root = Path(snap)

    return _yield_records(root)


def _yield_records(root: Path) -> Iterator[dict[str, Any]]:
    """Inner generator -- separated from :func:`load_longmemeval` so the
    snapshot_download call site executes on call rather than on first
    ``next()``."""
    for raw in _iter_dataset_dir(root):
        if not isinstance(raw, dict):
            continue
        rec = _normalize_record(raw)
        if rec is not None:
            yield rec


def iter_raw_longmemeval(
    *,
    cache_dir: Path | None = None,
    dataset_path: Path | str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield RAW upstream LongMemEval_S records (Phase 14, Plan A).

    Unlike :func:`load_longmemeval` which normalizes records to
    ``{id, question, sessions, answer, axis}``, this generator yields the
    upstream raw dicts so the bench-only ingest path can pair
    ``haystack_session_ids[i]`` with ``haystack_sessions[i]`` (D-SCOPE-02).

    Records whose normalized axis falls outside :data:`AXES` are still
    filtered out for consistency with the canonical loader.

    Production indexer paths do NOT consume this — it is reachable only
    from :mod:`supamem.eval.longmemeval_ingest`.
    """
    if dataset_path is not None:
        root = Path(dataset_path)
    else:
        cache_root = resolve_cache_dir(cache_dir=cache_dir)
        snap = huggingface_hub.snapshot_download(
            repo_id=REPO_ID,
            revision=PINNED_REVISION,
            repo_type="dataset",
            cache_dir=str(cache_root),
            allow_patterns=[_LONGMEMEVAL_S_FILE],
        )
        root = Path(snap)

    for raw in _iter_dataset_dir(root):
        if not isinstance(raw, dict):
            continue
        upstream_axis = raw.get("question_type", "")
        axis = _AXIS_ALIAS.get(upstream_axis)
        if axis is None or axis not in AXES:
            continue
        yield raw


def iter_haystack_chunks(
    records: Iterable[dict[str, Any]],
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(session_id, text, axis)`` per haystack turn (Plan 14-A).

    Per D-SCOPE-02: one ``session_id`` per chunk, sourced verbatim from
    ``raw["haystack_session_ids"][i]`` paired with the i-th list in
    ``raw["haystack_sessions"]``. Each turn within a session emits exactly
    one tuple; empty turn lists yield nothing for that session.

    Text format: ``f"{role}: {content}"`` (RESEARCH §Q2 sample). Production
    indexer paths (markdown, transcript) are unaffected — this generator
    feeds the bench-only ingest module.

    Records may carry either the canonical ``axis`` field (already
    normalized) or the upstream ``question_type`` field; canonical wins
    when both are present. Records whose axis cannot be resolved to one of
    :data:`AXES` are skipped silently.
    """
    for raw in records:
        if not isinstance(raw, dict):
            continue
        axis = raw.get("axis")
        if not axis:
            upstream = raw.get("question_type", "")
            axis = _AXIS_ALIAS.get(upstream)
        if axis is None or axis not in AXES:
            continue
        sids = raw.get("haystack_session_ids") or []
        sessions = raw.get("haystack_sessions") or []
        for sid, turns in zip(sids, sessions):
            if not isinstance(sid, str) or not turns:
                continue
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role", ""))
                content = str(turn.get("content", ""))
                if not content:
                    continue
                yield sid, f"{role}: {content}", axis


def build_smoke_subset(
    records: Iterable[dict[str, Any]],
    *,
    seed: int = 0,
    per_axis: int = 2,
) -> list[dict[str, str]]:
    """Build the D-SUBSET-01 axis-stratified frozen subset.

    Buckets records by axis, then ``random.Random(seed).sample(bucket,
    per_axis)`` per axis. Returns a deterministic-sorted list of
    ``{"id", "axis"}`` dicts (sort by ``(axis, id)`` for stable diffs).

    Per :data:`AXES` order: 5 axes x ``per_axis`` = 10 entries by default.
    """
    import random

    buckets: dict[str, list[str]] = {axis: [] for axis in AXES}
    for rec in records:
        axis = rec.get("axis")
        if axis in buckets:
            buckets[axis].append(rec["id"])

    rng = random.Random(seed)
    picked: list[dict[str, str]] = []
    for axis in AXES:
        bucket = sorted(buckets[axis])  # canonicalize before sampling
        if len(bucket) < per_axis:
            err_console.print(
                f"[supamem.warn]Axis {axis!r} has only {len(bucket)} records; "
                f"requested {per_axis}.[/supamem.warn]"
            )
            chosen = bucket
        else:
            chosen = rng.sample(bucket, per_axis)
        for qid in chosen:
            picked.append({"id": qid, "axis": axis})

    picked.sort(key=lambda d: (d["axis"], d["id"]))
    return picked


__all__ = [
    "AXES",
    "DATASET_NAME",
    "PINNED_REVISION",
    "REPO_ID",
    "build_smoke_subset",
    "iter_haystack_chunks",
    "iter_raw_longmemeval",
    "load_longmemeval",
    "resolve_cache_dir",
]
