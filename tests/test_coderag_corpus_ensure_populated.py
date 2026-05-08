"""Phase 16 Plan A — ensure_populated_manifest TDD (Req-02 placeholder realization).

Contract under test (Plan 16-A):

    ``ensure_populated_manifest(manifest_path: Path) -> dict``

- Reads a bundled-shape manifest carrying ``<EXECUTOR_FILLS_AT_BUILD_TIME>``
  placeholders for ``commit_sha`` / ``content_sha256`` per repo entry.
- Realizes the corpus into ``cache_root() / "manifest.json"`` (user-cache).
- Leaves the bundled file byte-identical (Req-02: package source stays
  reproducible-by-rebuild — placeholders only ship in the package).
- Idempotent: a second invocation against an already-populated user-cache
  manifest is a byte-identical no-op and does NOT call ``fetch_pinned``.
- Honors carry-pinned commit SHAs (e.g. fastapi ``622b6356b``): only fills
  fields whose value is the literal placeholder token.
- Returns the realized manifest as a ``dict`` (planner's chosen shape).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


PLACEHOLDER = "<EXECUTOR_FILLS_AT_BUILD_TIME>"


def _bundled_manifest_text(entries: list[dict]) -> str:
    """Produce the same JSON envelope ``write_manifest`` emits."""
    payload = {
        "version": 1,
        "captured_at": "2026-05-06T00:00:00+00:00",
        "repos": entries,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_bundled(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "bundled" / "coderag_corpus_manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_bundled_manifest_text(entries), encoding="utf-8")
    return p


@pytest.fixture
def patched_corpus(tmp_path, monkeypatch):
    """Redirect ``cache_root`` to ``tmp_path`` and stub ``fetch_pinned``.

    The stub creates a single fixture file under the destination so
    ``walk_corpus`` + ``compute_content_sha256`` produce a deterministic,
    non-empty hash.
    """
    from supamem.eval.coderag import corpus as corpus_mod

    cache_dir = tmp_path / "cache" / "coderag"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(corpus_mod, "cache_root", lambda: cache_dir)

    fetch_mock = MagicMock()

    def _fake_fetch(repo_url: str, sha: str, dest: Path) -> Path:  # noqa: ARG001
        fetch_mock(repo_url, sha, dest)
        dest.mkdir(parents=True, exist_ok=True)
        # A single deterministic file so compute_content_sha256 is stable.
        (dest / "README.md").write_text(
            f"# corpus fixture for {repo_url}@{sha}\n",
            encoding="utf-8",
        )
        return dest

    monkeypatch.setattr(corpus_mod, "fetch_pinned", _fake_fetch)
    return corpus_mod, cache_dir, fetch_mock


# ---------------------------------------------------------------------------
# Test 1 — placeholder realization
# ---------------------------------------------------------------------------
def test_ensure_populated_manifest_realizes_placeholders(tmp_path, patched_corpus):
    corpus_mod, cache_dir, _fetch_mock = patched_corpus
    bundled = _write_bundled(
        tmp_path,
        [
            {
                "slug": "supamem",
                "repo_url": "https://example.test/supamem.git",
                "commit_sha": "0" * 40,  # non-placeholder pinned SHA
                "content_sha256": PLACEHOLDER,
            },
            {
                "slug": "fastapi",
                "repo_url": "https://example.test/fastapi.git",
                "commit_sha": "1" * 40,  # non-placeholder pinned SHA
                "content_sha256": PLACEHOLDER,
            },
        ],
    )
    out = corpus_mod.ensure_populated_manifest(bundled)

    user_cache_manifest = cache_dir / "manifest.json"
    assert user_cache_manifest.exists()
    parsed = json.loads(user_cache_manifest.read_text(encoding="utf-8"))
    for entry in parsed["repos"]:
        assert entry["commit_sha"] != PLACEHOLDER
        assert entry["content_sha256"] != PLACEHOLDER
        assert len(entry["content_sha256"]) == 64  # hex sha256 length
    # Return shape: dict with repos list.
    assert isinstance(out, dict)
    assert "repos" in out


# ---------------------------------------------------------------------------
# Test 2 — bundled file untouched (Req-02)
# ---------------------------------------------------------------------------
def test_ensure_populated_manifest_leaves_bundled_byte_identical(
    tmp_path, patched_corpus
):
    corpus_mod, _cache_dir, _fetch_mock = patched_corpus
    bundled = _write_bundled(
        tmp_path,
        [
            {
                "slug": "supamem",
                "repo_url": "https://example.test/supamem.git",
                "commit_sha": "0" * 40,
                "content_sha256": PLACEHOLDER,
            }
        ],
    )
    before = bundled.read_bytes()
    before_sha = hashlib.sha256(before).hexdigest()
    corpus_mod.ensure_populated_manifest(bundled)
    after = bundled.read_bytes()
    after_sha = hashlib.sha256(after).hexdigest()
    assert before_sha == after_sha
    assert before == after


# ---------------------------------------------------------------------------
# Test 3 — idempotent (second call is byte-identical no-op + no re-fetch)
# ---------------------------------------------------------------------------
def test_ensure_populated_manifest_is_idempotent(tmp_path, patched_corpus):
    corpus_mod, cache_dir, fetch_mock = patched_corpus
    bundled = _write_bundled(
        tmp_path,
        [
            {
                "slug": "supamem",
                "repo_url": "https://example.test/supamem.git",
                "commit_sha": "0" * 40,
                "content_sha256": PLACEHOLDER,
            }
        ],
    )
    corpus_mod.ensure_populated_manifest(bundled)
    user_cache_manifest = cache_dir / "manifest.json"
    first_bytes = user_cache_manifest.read_bytes()
    first_call_count = fetch_mock.call_count

    # Second invocation — must not re-fetch and must produce identical bytes.
    corpus_mod.ensure_populated_manifest(bundled)
    second_bytes = user_cache_manifest.read_bytes()

    assert first_bytes == second_bytes, "second invocation rewrote the manifest"
    assert fetch_mock.call_count == first_call_count, (
        f"fetch_pinned re-invoked on idempotent call "
        f"(first={first_call_count}, after={fetch_mock.call_count})"
    )


# ---------------------------------------------------------------------------
# Test 4 — carry-pin honored (real commit_sha preserved verbatim)
# ---------------------------------------------------------------------------
def test_ensure_populated_manifest_honors_carry_pinned_commit_sha(
    tmp_path, patched_corpus
):
    corpus_mod, cache_dir, _fetch_mock = patched_corpus
    pinned_sha = "622b6356b" + "0" * 31  # 40-char real-shaped SHA
    bundled = _write_bundled(
        tmp_path,
        [
            {
                "slug": "fastapi",
                "repo_url": "https://example.test/fastapi.git",
                "commit_sha": pinned_sha,
                "content_sha256": PLACEHOLDER,
            }
        ],
    )
    corpus_mod.ensure_populated_manifest(bundled)
    parsed = json.loads(
        (cache_dir / "manifest.json").read_text(encoding="utf-8")
    )
    [entry] = parsed["repos"]
    assert entry["commit_sha"] == pinned_sha, (
        "carry-pinned commit_sha was rewritten; only PLACEHOLDER fields may be filled"
    )
    assert entry["content_sha256"] != PLACEHOLDER
    assert len(entry["content_sha256"]) == 64


# ---------------------------------------------------------------------------
# Test 5 — return shape: dict with repos[*] {slug, repo_url, commit_sha, content_sha256}
# ---------------------------------------------------------------------------
def test_ensure_populated_manifest_return_shape(tmp_path, patched_corpus):
    corpus_mod, _cache_dir, _fetch_mock = patched_corpus
    bundled = _write_bundled(
        tmp_path,
        [
            {
                "slug": "supamem",
                "repo_url": "https://example.test/supamem.git",
                "commit_sha": "a" * 40,
                "content_sha256": PLACEHOLDER,
            }
        ],
    )
    result = corpus_mod.ensure_populated_manifest(bundled)
    assert isinstance(result, dict)
    assert "repos" in result and isinstance(result["repos"], list)
    [entry] = result["repos"]
    for key in ("slug", "repo_url", "commit_sha", "content_sha256"):
        assert key in entry, f"missing key {key!r} in returned manifest entry"
