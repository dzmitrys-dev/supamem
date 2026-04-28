"""Indexer entry point — ports ``embed-dev-memories.py`` to the D-25 hybrid schema.

``run_index(target, force, sources, config)`` walks the source globs, chunks
each Markdown doc, computes both dense + sparse vectors, and upserts to the
configured Qdrant collection. Fail-soft: any Qdrant or fastembed import / RPC
error short-circuits to ``return 0`` so calling hooks never break.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Iterable, Literal

from supamem.config import ResolvedConfig
from supamem.embedders import build_dense_embedder, build_sparse_embedder
from supamem.indexer.chunker import CHUNK_MIN_TOKENS, _token_count, chunk_markdown
from supamem.indexer.manifest import Manifest

try:
    from qdrant_client import QdrantClient  # noqa: F401
except ImportError:  # qdrant-client missing — fail-soft path still works
    QdrantClient = None  # type: ignore[assignment, misc]

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
DEFAULT_VECTOR_SIZE = 384

Target = Literal["prod", "tuned", "both"]

__all__ = [
    "DENSE_VECTOR",
    "SPARSE_VECTOR",
    "QdrantClient",
    "build_dense_embedder",
    "build_sparse_embedder",
    "run_index",
]


def _expand_sources(sources: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for s in sources:
        p = Path(s)
        if p.is_file() and p.suffix == ".md":
            out.append(p.resolve())
        elif p.is_dir():
            out.extend(sorted(q.resolve() for q in p.rglob("*.md")))
    return sorted(set(out))


def _compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _chunk_id(file_path: str, idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}#chunk={idx}"))


def _manifest_path(config: ResolvedConfig) -> Path:
    base = Path(config.cache_dir) if config.cache_dir else Path.home() / ".cache" / "supamem"
    return base / "manifest.json"


def _index_doc(
    *,
    client: Any,
    dense: Any,
    sparse: Any,
    path: Path,
    body: str,
    sha: str,
    collection: str,
) -> int:
    """Chunk a single doc, embed both arms, upsert hybrid points. Returns chunks written."""
    from qdrant_client.http import models as qmodels

    chunks = chunk_markdown(body)
    if not chunks:
        return 0

    abs_path = str(path.resolve())
    points: list[Any] = []
    for idx, chunk in enumerate(chunks):
        if _token_count(chunk) < CHUNK_MIN_TOKENS:
            continue
        dense_vec = [float(x) for x in next(dense.embed([chunk]))]
        sparse_vec = next(sparse.embed([chunk]))
        sparse_obj = qmodels.SparseVector(
            indices=[int(i) for i in sparse_vec.indices],
            values=[float(v) for v in sparse_vec.values],
        )
        points.append(
            qmodels.PointStruct(
                id=_chunk_id(abs_path, idx),
                vector={DENSE_VECTOR: dense_vec, SPARSE_VECTOR: sparse_obj},
                payload={
                    "file_path": abs_path,
                    "chunk_idx": idx,
                    "content_hash": sha,
                    "document": chunk,
                },
            )
        )
    if not points:
        return 0
    client.upsert(collection_name=collection, points=points)
    return len(points)


def run_index(
    *,
    target: Target = "tuned",
    force: bool = False,
    sources: list[str] | None = None,
    config: ResolvedConfig | None = None,
) -> int:
    """Embed Markdown sources into Qdrant. Fail-soft: returns 0 on any external failure."""
    cfg = config or ResolvedConfig()
    src_list = sources if sources is not None else cfg.sources or []
    files = _expand_sources(src_list)
    if not files:
        return 0

    # Fail-soft: Qdrant unreachable or qdrant-client missing.
    try:
        if QdrantClient is None:
            return 0
        client = QdrantClient(url=cfg.qdrant_url, timeout=60)
        client.get_collections()
    except Exception:  # noqa: BLE001 — fail-soft per plan acceptance criterion
        return 0

    try:
        dense = build_dense_embedder()
        sparse = build_sparse_embedder()
    except Exception:  # noqa: BLE001 — embedders may pull heavy deps
        return 0

    manifest_path = _manifest_path(cfg)
    manifest = Manifest.load(manifest_path)

    written = 0
    failed = 0
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            failed += 1
            continue
        sha = _compute_hash(raw)
        abs_path = str(path.resolve())

        if not force and not manifest.needs_index(abs_path, sha, target):
            continue

        try:
            n = _index_doc(
                client=client,
                dense=dense,
                sparse=sparse,
                path=path,
                body=raw,
                sha=sha,
                collection=cfg.collection,
            )
            if n > 0:
                manifest.update(abs_path, target, sha)
                written += n
        except Exception:  # noqa: BLE001 — keep going; one bad doc shouldn't kill the run
            failed += 1
            continue

    try:
        manifest.save(manifest_path)
    except OSError:
        pass

    return 0 if failed == 0 else 0  # fail-soft contract — never propagate
