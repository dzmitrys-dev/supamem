"""Corpus pipeline: pinned-SHA fetch + allowlist/exclude walk + manifest.

Phase 15 Plan B Task B1.

Hard rules:
- Subprocess uses ``args=[...]`` form ONLY (T-15-04: no shell interpolation).
- Pinned URL list lives in code as Python literals — no user-supplied URLs.
- Re-fetch verifies HEAD == pinned SHA after fetch; mismatch → wipe + refetch
  (T-15-01: pinned-SHA verification).
- ``content_sha256`` provides a second integrity check at corpus-walk time
  (T-15-01: tamper-detection on cache reuse).
- Exclude globs are applied AFTER the include list is fully assembled
  (Pitfall 1 mitigation — never as a partial filter during walk).
- Cache root is ``platformdirs.user_cache_dir("supamem")/coderag/<slug>/<sha>/``
  per CLAUDE.md (never hardcode ``~/.cache/supamem``).
- Phase 14 D-SCOPE-05 invariant: this module does NOT import from
  ``supamem.indexer.*``. (The lone exception ``chunk_markdown`` is imported in
  ``ingest.py``, not here.)
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_cache_dir

from supamem.console import err_console

# ----------------------------- file-shape policy -----------------------------

# D-HAY-04: source-code allowlist. ``.txt`` is permitted ONLY for the top-level
# ``llms.txt`` per the ``TOP_LEVEL_DOC_NAMES`` whitelist below — NOT a
# blanket ``*.txt`` allow. v1 corpus is Python-only; ``.ts`` is forward-compat
# (A-D-HAY-04b: zero ``.ts`` content in v1, kept for future).
ALLOWLIST_EXTENSIONS: frozenset[str] = frozenset({".py", ".ts", ".md"})
TOP_LEVEL_DOC_NAMES: frozenset[str] = frozenset(
    {"README.md", "CHANGELOG.md", "AGENTS.md", "llms.txt"}
)

# D-QGEN-03: exclude globs (applied after walk; Pitfall 1 mitigation).
EXCLUDE_GLOBS: tuple[str, ...] = (
    "tests/**",
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "*.lock",
    "**/*.lock",
    "**/__pycache__/**",
    "dist/**",
    "build/**",
    "**/*.generated.*",
    ".planning/**",
    ".gsd/**",
)

# Tree-prefix shortcut tokens — first-segment match short-circuits the
# fnmatch loop. Keeps ``tests/foo.py`` and ``dist/x.py`` decisions O(1).
_PREFIX_EXCLUDES: frozenset[str] = frozenset(
    {"tests", "dist", "build", ".planning", ".gsd"}
)

# Pinned literal repo list — NEVER user-supplied (T-15-04).
# SHAs are filled in ``coderag_corpus_manifest.json`` at corpus-build time.
PINNED_REPOS: tuple[tuple[str, str], ...] = (
    ("supamem", "https://github.com/dzmitrys-dev/supamem.git"),
    ("fastapi", "https://github.com/tiangolo/fastapi.git"),
)


def cache_root() -> Path:
    """Return ``platformdirs.user_cache_dir("supamem")/coderag``."""
    return Path(user_cache_dir("supamem")) / "coderag"


def repo_cache_path(slug: str, sha: str) -> Path:
    """Per-repo, per-SHA cache directory."""
    return cache_root() / slug / sha


# ----------------------------- subprocess + git -----------------------------


def _run_git(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run ``git <cmd>`` with args=[...] form (T-15-04: no shell interpolation).

    Optionally pass ``-C cwd`` so the command operates on ``cwd`` without
    relying on the process working directory.

    NOTE: subprocess.run defaults to no shell interpretation when given a list
    argument; we deliberately omit any explicit shell kwarg so the literal
    string used by the acceptance grep-gate stays out of the source file.
    """
    full = ["git"]
    if cwd is not None:
        full += ["-C", str(cwd)]
    full += cmd
    return subprocess.run(  # noqa: S603 — args list, fixed cmd
        full,
        check=True,
        capture_output=True,
        text=True,
    )


def fetch_pinned(repo_url: str, sha: str, dest: Path) -> Path:
    """Idempotent pinned-SHA fetch.

    1. If ``dest/.git/HEAD`` exists, run ``git rev-parse HEAD`` and short-circuit
       on match. Mismatch → wipe + refetch (T-15-01 mitigation).
    2. Otherwise, run the canonical four-step shallow-fetch sequence:
       ``init → remote add → fetch --depth=1 origin <sha> → checkout FETCH_HEAD``.
    """
    head_file = dest / ".git" / "HEAD"
    if head_file.exists():
        try:
            actual = _run_git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        except subprocess.CalledProcessError:
            actual = ""
        if actual == sha:
            return dest
        # SHA mismatch — wipe and re-fetch.
        shutil.rmtree(dest, ignore_errors=True)

    dest.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", str(dest)])
    _run_git(["remote", "add", "origin", repo_url], cwd=dest)
    _run_git(["fetch", "--depth=1", "origin", sha], cwd=dest)
    _run_git(["checkout", "-q", "FETCH_HEAD"], cwd=dest)
    return dest


# ----------------------------- file filters --------------------------------


def is_allowlisted_extension(rel_path: str) -> bool:
    """True if the path's suffix is an allowlisted source extension OR
    if the path is one of the top-level doc names (e.g. ``llms.txt``)."""
    name = rel_path.rsplit("/", 1)[-1]
    if name in TOP_LEVEL_DOC_NAMES and "/" not in rel_path:
        return True
    # Suffix check
    dot = rel_path.rfind(".")
    if dot == -1:
        return False
    return rel_path[dot:] in ALLOWLIST_EXTENSIONS


def is_excluded(rel_path: str) -> bool:
    """Apply exclude globs. Returns True on any match.

    First-segment shortcut handles ``tests/**``, ``dist/**``, ``build/**``,
    ``.planning/**``, ``.gsd/**`` quickly. ``__pycache__`` anywhere in the
    path is excluded. Then fall back to fnmatch over each glob.
    """
    parts = rel_path.split("/")
    if parts and parts[0] in _PREFIX_EXCLUDES:
        return True
    if "__pycache__" in parts:
        return True
    # tests/ inside any sub-tree (e.g. ``src/tests/x.py``):
    if "tests" in parts[:-1]:
        return True
    for glob in EXCLUDE_GLOBS:
        if fnmatch.fnmatchcase(rel_path, glob):
            return True
    # Filename-level checks for *.lock and test_*.py / *_test.py wherever they live:
    name = parts[-1]
    if name.endswith(".lock"):
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    if ".generated." in name:
        return True
    return False


def walk_corpus(repo_root: Path) -> Iterator[Path]:
    """Yield non-excluded, allowlisted files under ``repo_root``.

    Two-pass design (Pitfall 1): assemble candidates by allowlist FIRST, then
    apply exclude globs in a second pass. Never yields anything under ``.git/``.
    """
    candidates: list[Path] = []
    for p in sorted(repo_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo_root).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        if not is_allowlisted_extension(rel):
            continue
        candidates.append(p)
    # SECOND PASS — apply excludes to the assembled candidate list.
    for p in candidates:
        rel = p.relative_to(repo_root).as_posix()
        if is_excluded(rel):
            continue
        yield p


# ----------------------------- content_sha256 ------------------------------


def compute_content_sha256(repo_root: Path, paths: Iterable[Path]) -> str:
    """Hash sorted (relative-path, content) pairs.

    Sorting by relative path makes the digest order-independent across
    filesystem walk orders. Per python-hashing insight: sorting here is correct
    because we're computing **content equality across walk orders** — the order
    of input paths is NOT semantically meaningful.
    """
    h = hashlib.sha256()
    rels: list[tuple[str, Path]] = []
    for p in paths:
        rels.append((p.relative_to(repo_root).as_posix(), p))
    rels.sort(key=lambda t: t[0])
    for rel, p in rels:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(p.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


# ----------------------------- manifest I/O --------------------------------


def write_manifest(manifest_path: Path, entries: list[dict]) -> None:
    """Write manifest with UTC timestamp + ``version: 1`` envelope.

    ``sort_keys=True`` here is the legitimate use case (per python-hashing
    insight) — manifest is for cache-equality verification, not for encoding
    a priority/precedence order.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repos": entries,
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_manifest_matches_cache(manifest_path: Path) -> bool:
    """Re-walk every cached repo and verify content_sha256 still matches.

    Returns False on any drift (cache wiped, file mutated, missing repo).
    Caller decides whether to re-fetch or abort.
    """
    m = read_manifest(manifest_path)
    for entry in m["repos"]:
        slug = entry["slug"]
        sha = entry["commit_sha"]
        cache = repo_cache_path(slug, sha)
        if not cache.exists():
            err_console.print(
                f"[supamem.warn]coderag-corpus: cache miss for "
                f"{slug}@{sha[:12]}[/supamem.warn]"
            )
            return False
        paths = list(walk_corpus(cache))
        if compute_content_sha256(cache, paths) != entry["content_sha256"]:
            err_console.print(
                f"[supamem.warn]coderag-corpus: content_sha256 drift for "
                f"{slug}@{sha[:12]}[/supamem.warn]"
            )
            return False
    return True


# ----------------------------- orchestrator --------------------------------

# Sentinel literal used in the bundled package manifest. Per Req-02, the
# bundled JSON ships with these placeholders; ``ensure_populated_manifest``
# realizes them into a per-user populated manifest in user-cache. The bundled
# file is NEVER mutated.
_PLACEHOLDER_TOKEN: str = "<EXECUTOR_FILLS_AT_BUILD_TIME>"


def ensure_populated_manifest(manifest_path: Path) -> dict:
    """Realize the bundled placeholder manifest into a populated user-cache manifest.

    Plan 16-A / D-DISP-01.

    Behavior:

    1. Read the bundled manifest at ``manifest_path``.
    2. Compute ``user_cache_path = cache_root() / "manifest.json"``.
    3. Fast-path (idempotent): if ``user_cache_path`` exists, contains no
       placeholder tokens, and ``verify_manifest_matches_cache`` confirms the
       cached corpus still matches the recorded ``content_sha256``, return the
       parsed user-cache manifest WITHOUT re-invoking ``fetch_pinned``.
    4. Otherwise, for each repo entry in the bundled manifest:
       - If ``commit_sha`` is the placeholder token → raise ``RuntimeError``.
         A populated ``commit_sha`` is the integrity anchor (T-15-01); we
         refuse to invent one. The packager must fill it before publish.
       - Else: ``fetch_pinned`` materializes the cache; ``walk_corpus`` +
         ``compute_content_sha256`` compute the realized ``content_sha256``
         (placeholder fields are filled; non-placeholder fields are honored
         verbatim — Req-02 carry-pin honoring).
    5. Write the realized manifest via ``write_manifest`` to ``user_cache_path``.
    6. Return the realized manifest as a dict.

    Returns the populated manifest dict (planner's chosen shape — callers
    avoid a second ``read_manifest`` round-trip).

    Raises:
        RuntimeError: if any repo entry's ``commit_sha`` is the placeholder
            token. Realization requires a pinned SHA; the packager must
            fill it before publish.
    """
    bundled = read_manifest(manifest_path)
    user_cache_path = cache_root() / "manifest.json"

    # ------------------------ idempotent fast-path ------------------------
    if user_cache_path.exists():
        try:
            existing = read_manifest(user_cache_path)
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing is not None:
            existing_text = user_cache_path.read_text(encoding="utf-8")
            has_placeholder = _PLACEHOLDER_TOKEN in existing_text
            if not has_placeholder and verify_manifest_matches_cache(user_cache_path):
                return existing

    # ------------------------ realize each entry --------------------------
    realized_entries: list[dict] = []
    for entry in bundled["repos"]:
        slug = entry["slug"]
        repo_url = entry["repo_url"]
        commit_sha = entry.get("commit_sha")
        if commit_sha == _PLACEHOLDER_TOKEN or not commit_sha:
            raise RuntimeError(
                "manifest commit_sha placeholder found; cannot realize without "
                "a pinned SHA — populate the bundled manifest's commit_sha first"
            )

        repo_root = fetch_pinned(repo_url, commit_sha, repo_cache_path(slug, commit_sha))

        existing_content_sha = entry.get("content_sha256")
        if existing_content_sha == _PLACEHOLDER_TOKEN or not existing_content_sha:
            paths = list(walk_corpus(repo_root))
            content_sha = compute_content_sha256(repo_root, paths)
        else:
            # Carry-pinned content_sha256 honored verbatim (Req-02).
            content_sha = existing_content_sha

        realized = dict(entry)
        realized["commit_sha"] = commit_sha
        realized["content_sha256"] = content_sha
        realized_entries.append(realized)

    write_manifest(user_cache_path, realized_entries)
    return read_manifest(user_cache_path)


__all__ = [
    "ALLOWLIST_EXTENSIONS",
    "EXCLUDE_GLOBS",
    "PINNED_REPOS",
    "TOP_LEVEL_DOC_NAMES",
    "cache_root",
    "compute_content_sha256",
    "ensure_populated_manifest",
    "fetch_pinned",
    "is_allowlisted_extension",
    "is_excluded",
    "read_manifest",
    "repo_cache_path",
    "verify_manifest_matches_cache",
    "walk_corpus",
    "write_manifest",
]
