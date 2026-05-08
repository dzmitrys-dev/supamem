"""Auto-query extractor: code-fact (PR) + decision-rationale (ADR) axes.

Phase 15 Plan B Task B2.

Two synthesis paths:

* **code_fact** — for each merge / first-parent commit at the pinned SHA,
  produce a query whose text is the commit title + body and whose gold-set
  is the union of files modified by that commit, MINUS the global exclude
  globs (``tests/**``, ``*.lock``, ``dist/**``, ``build/**``, ``*.generated.*``,
  planning artifacts). Queries with empty gold are dropped.
* **decision_rationale** — for each ``docs/adr/*.md`` file, take the first
  paragraph under the ``## Problem`` / ``## Why`` / ``## Context`` heading as
  the query text; gold = the ADR file path itself + every backtick-cited
  relative path matching ``([^`]+/[^`]+\\.(?:py|ts|md))`` inside the
  ``## Decision`` section. Per A-D-HAY-04 this pass is supamem-only —
  fastapi has no ADRs and the function returns ``[]`` for repos with no
  ``docs/adr/`` directory.

Pitfall 4 mitigation: the ADR-citation regex requires a ``/`` so prose
".py" mentions don't match. Cited paths missing at the pinned SHA are
dropped with an err_console warning.

Down-sample: stratified by (axis, repo) with ``random.Random(42)`` for
deterministic CI reproducibility (D-QGEN-05).
"""
from __future__ import annotations

import random
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from supamem.console import err_console
from supamem.eval.coderag.corpus import is_excluded

# ----------------------------- regexes --------------------------------------

# Anchored to require '/' (Pitfall 4 mitigation): prose ".py" doesn't match.
ADR_PATH_RE = re.compile(r"`([^`]+/[^`]+\.(?:py|ts|md))`")

# Match the first paragraph under '## Problem' / '## Why' / '## Context'.
PROBLEM_HEADER_RE = re.compile(
    r"(?ms)^##\s+(?:Problem|Why|Context)\s*\n+(.+?)(?:\n##|\Z)"
)

# Match the body of the '## Decision' section (citations live here).
DECISION_HEADER_RE = re.compile(r"(?ms)^##\s+Decision\s*\n+(.+?)(?:\n##|\Z)")


# ----------------------------- subprocess helper ----------------------------


def _git(args: list[str], cwd: Path) -> str:
    """Run ``git -C cwd <args>`` with args=[...] form."""
    return subprocess.run(  # noqa: S603 — args list, fixed cmd
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


# ----------------------------- code_fact axis -------------------------------


def extract_pr_queries(
    repo_root: Path,
    repo_slug: str,
    *,
    max_queries: int | None = None,
) -> list[dict]:
    """Walk first-parent commits; emit code-fact queries with PR-derived gold.

    Each query carries:

    - ``id``: ``"pr_{slug}_{sha[:12]}"``
    - ``axis``: ``"code_fact"``
    - ``repo``: ``repo_slug``
    - ``text``: ``"{title}\\n\\n{body}".strip()``
    - ``gold``: sorted unique list of repo-relative paths NOT matched by
      :func:`supamem.eval.coderag.corpus.is_excluded`.

    Queries with empty ``gold`` are dropped (D-QGEN-03).
    """
    log = _git(
        ["log", "--first-parent", "--format=%H%x09%s%x09%b%x1e", "HEAD"],
        cwd=repo_root,
    )
    queries: list[dict] = []
    for entry in log.split("\x1e"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("\t", 2)
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        title = parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ""
        files_raw = _git(["show", "--name-only", "--format=", sha], cwd=repo_root)
        files = [f.strip() for f in files_raw.splitlines() if f.strip()]
        gold = sorted({f for f in files if not is_excluded(f)})
        if not gold:
            continue
        text = f"{title}\n\n{body}".strip() if body else title
        queries.append(
            {
                "id": f"pr_{repo_slug}_{sha[:12]}",
                "axis": "code_fact",
                "repo": repo_slug,
                "text": text,
                "gold": gold,
                # Phase 16 D-DISP-03: per-record provenance tag.
                "query_origin": "pr_first_parent",
                # Phase 16 D-DISP-03: conservative default — the field's
                # PRESENCE is the contract; downstream consumers can
                # refine the value without breaking schema.
                "training_leakage_suspected": False,
            }
        )
        if max_queries and len(queries) >= max_queries:
            break
    return queries


# ----------------------------- decision_rationale axis ----------------------


def extract_adr_queries(repo_root: Path, repo_slug: str) -> list[dict]:
    """Extract decision-rationale queries from ``docs/adr/*.md``.

    Per A-D-HAY-04: this pass is supamem-only. Repos without a
    ``docs/adr/`` directory (e.g. fastapi) return ``[]`` — never raise.

    Pitfall 4: the citation regex requires a ``/`` so prose-only ".py"
    mentions are not picked up as paths. Cited paths that don't exist
    at the pinned SHA are dropped with an err_console warning.
    """
    adr_dir = repo_root / "docs" / "adr"
    if not adr_dir.is_dir():
        return []
    queries: list[dict] = []
    for adr_path in sorted(adr_dir.glob("*.md")):
        text = adr_path.read_text(encoding="utf-8")
        problem = PROBLEM_HEADER_RE.search(text)
        if not problem:
            continue
        decision = DECISION_HEADER_RE.search(text)
        cited = ADR_PATH_RE.findall(decision.group(1)) if decision else []
        rel_adr = adr_path.relative_to(repo_root).as_posix()
        gold: set[str] = {rel_adr}
        for cited_path in cited:
            if (repo_root / cited_path).exists():
                gold.add(cited_path)
            else:
                err_console.print(
                    f"[supamem.warn]coderag-adr: cited path missing at pinned SHA: "
                    f"{cited_path} (in {adr_path.name})[/supamem.warn]"
                )
        # First paragraph of the Problem/Why section.
        first_para = problem.group(1).strip().split("\n\n")[0].strip()
        queries.append(
            {
                "id": f"adr_{repo_slug}_{adr_path.stem}",
                "axis": "decision_rationale",
                "repo": repo_slug,
                "text": first_para,
                "gold": sorted(gold),
                # Phase 16 D-DISP-03: per-record provenance tag.
                "query_origin": "adr_problem_section",
                # Phase 16 D-DISP-03: conservative default — see PR-axis
                # comment for rationale.
                "training_leakage_suspected": False,
            }
        )
    return queries


# ----------------------------- downsample -----------------------------------


def downsample_stratified(
    queries: list[dict], target: int, *, seed: int = 42
) -> list[dict]:
    """Stratified down-sample by ``(axis, repo)`` with deterministic seed.

    If ``len(queries) <= target`` returns the input unchanged. Otherwise:

    1. Bucket by ``(axis, repo)``; deterministic-sort each bucket by ``id``
       (input order from git log is not always stable).
    2. Allocate per-bucket quota proportional to bucket size; minimum 1.
    3. Use :class:`random.Random` (seed=42) to shuffle within each bucket;
       take the first quota.
    4. Trim/extend to exactly ``target`` to absorb rounding drift.
    """
    if len(queries) <= target:
        return list(queries)
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for q in queries:
        buckets[(q["axis"], q["repo"])].append(q)
    total = len(queries)
    out: list[dict] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda q: q["id"])
        rng.shuffle(group)
        quota = max(1, round(target * len(buckets[key]) / total))
        out.extend(group[:quota])
    # Trim to exact target if rounding drift overshot; otherwise pad with
    # the lexicographically next remaining queries (stable, deterministic).
    out_sorted = sorted(out, key=lambda q: q["id"])
    if len(out_sorted) >= target:
        return out_sorted[:target]
    # Pad with leftover queries (rare — only when rounding undershot).
    chosen_ids = {q["id"] for q in out_sorted}
    leftover = [q for q in queries if q["id"] not in chosen_ids]
    leftover.sort(key=lambda q: q["id"])
    rng.shuffle(leftover)
    out_sorted.extend(leftover[: target - len(out_sorted)])
    return sorted(out_sorted, key=lambda q: q["id"])[:target]


__all__ = [
    "ADR_PATH_RE",
    "DECISION_HEADER_RE",
    "PROBLEM_HEADER_RE",
    "downsample_stratified",
    "extract_adr_queries",
    "extract_pr_queries",
]
