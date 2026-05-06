"""Phase 15 Plan B Task B1 — corpus manifest round-trip + content_sha256 tests."""
from __future__ import annotations

from pathlib import Path

from supamem.eval.coderag.corpus import (
    compute_content_sha256,
    read_manifest,
    write_manifest,
)


def _write_files(root: Path, files: dict[str, str]) -> list[Path]:
    out: list[Path] = []
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        out.append(p)
    return out


def test_manifest_round_trip(tmp_path):
    manifest_path = tmp_path / "m.json"
    entries = [
        {
            "slug": "supamem",
            "repo_url": "https://example.test/supamem.git",
            "commit_sha": "abc123",
            "file_count": 3,
            "content_sha256": "deadbeef",
        }
    ]
    write_manifest(manifest_path, entries)
    parsed = read_manifest(manifest_path)
    assert parsed["repos"] == entries
    assert parsed["version"] == 1
    assert "captured_at" in parsed


def test_manifest_content_sha256_deterministic(tmp_path):
    files_a = _write_files(tmp_path / "a", {"src/x.py": "a\n", "src/y.py": "b\n"})
    files_b = _write_files(tmp_path / "b", {"src/y.py": "b\n", "src/x.py": "a\n"})
    # Same set of relative paths + bytes → same digest regardless of input order.
    h_a = compute_content_sha256(tmp_path / "a", files_a)
    h_b = compute_content_sha256(tmp_path / "b", files_b)
    assert h_a == h_b
    # Mutating one byte changes the digest.
    (tmp_path / "a" / "src" / "x.py").write_text("a!\n")
    h_a2 = compute_content_sha256(tmp_path / "a", files_a)
    assert h_a2 != h_a
