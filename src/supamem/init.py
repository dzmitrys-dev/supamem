"""Greenfield bootstrap for supamem (`supamem init`).

Probes Qdrant, creates a per-project sparse+dense collection, writes
``.supamem/config.toml`` with discovered defaults, and runs the first
indexer pass over auto-detected source paths.

T-80.6-08-02: refuses to overwrite ``.supamem/config.toml`` without
``--force``. T-80.6-08-04: refuses to create a collection that already
exists without ``--force``.
"""
from __future__ import annotations

import logging
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import tomli_w

from supamem.config import ResolvedConfig
from supamem.console import banner, err, info, ok, step, warn

log = logging.getLogger("supamem.init")

DOCKER_RECIPE = (
    "docker run -d --name qdrant -p 6333:6333 -p 6334:6334 "
    "-v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest"
)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
DEFAULT_VECTOR_SIZE = 384

# Auto-detected source path candidates (D-38).
_SOURCE_CANDIDATES: tuple[str, ...] = (
    ".claude/insights",
    ".claude/rules",
    "docs",
)


def _slugify(name: str) -> str:
    """``cwd.basename`` lowercased, non-alnum collapsed to ``-``."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "supamem"


def probe_qdrant(url: str, timeout: float = 2.0) -> bool:
    """Return True iff ``GET <url>/healthz`` returns 200 within ``timeout``."""
    target = url.rstrip("/") + "/healthz"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:  # noqa: S310 — explicit URL
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout, OSError):
        return False


def create_collection(client: Any, name: str, *, force: bool = False) -> bool:
    """Create a hybrid (dense + sparse) Qdrant collection.

    Returns True if created, False if it already existed (and force=False).
    """
    from qdrant_client.http import models as qmodels

    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        if not force:
            return False
        client.delete_collection(collection_name=name)

    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: qmodels.VectorParams(
                size=DEFAULT_VECTOR_SIZE,
                distance=qmodels.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                modifier=qmodels.Modifier.IDF,
            ),
        },
    )
    return True


def _detect_sources(cwd: Path) -> list[str]:
    out: list[str] = []
    for rel in _SOURCE_CANDIDATES:
        p = cwd / rel
        if p.exists():
            out.append(rel)
    readme = cwd / "README.md"
    if readme.is_file():
        out.append("README.md")
    return out


def _write_config(target: Path, *, collection: str, sources: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "supamem": {
            "collection": collection,
            "embedder": "minilm",
            "chunker": "markdown_header",
            "sources": sources,
        }
    }
    target.write_text(tomli_w.dumps(body), encoding="utf-8")


def _get_client(qdrant_url: str, api_key: str = "") -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(
        url=qdrant_url,
        api_key=api_key or None,
        check_compatibility=False,
        timeout=30,
    )


def run_init(
    cwd: Path,
    *,
    yes: bool = False,
    qdrant_url: Optional[str] = None,
    force: bool = False,
    skip_models: bool = False,
) -> int:
    """Greenfield bootstrap. Returns 0 on success, non-zero on hard failure."""
    cwd = cwd.resolve()
    url = (qdrant_url or "http://localhost:6333").rstrip("/")

    banner("supamem init", f"bootstrapping in {cwd}")

    # ── 1. Probe Qdrant ────────────────────────────────────────────────────
    info(f"probing Qdrant at {url}")
    if not probe_qdrant(url):
        warn(f"Qdrant unreachable at {url}")
        info("Start Qdrant with:")
        step(DOCKER_RECIPE)
        if not yes:
            err("aborting — re-run with --yes to skip the prompt, or start Qdrant first")
            return 2
        warn("--yes set: continuing under the assumption Qdrant will be reachable shortly")

    # ── 1b. Eager-fetch ML prerequisites (D-FETCH-01) ──────────────────────
    if not skip_models:
        try:
            from supamem.config import load_config
            from supamem.rerankers import prepare

            cfg, _ = load_config()
            if getattr(cfg, "reranker_name", "off") != "off":
                info("pre-fetching reranker model (idempotent on cache hit)")
                prepare(cfg.reranker_model_id)
                ok("reranker model cached")
        except RuntimeError:
            warn("reranker model fetch failed — run `supamem repair` later")
        except Exception as exc:  # noqa: BLE001 — non-fatal
            log.debug("model pre-fetch skipped: %r", exc)
    else:
        info("--skip-models: skipping ML model pre-fetch")

    # ── 2. Slug + collection name ──────────────────────────────────────────
    slug = _slugify(cwd.name)
    collection = f"supamem-{slug}"
    info(f"project slug: [supamem.brand]{slug}[/supamem.brand]")
    info(f"collection:   [supamem.brand]{collection}[/supamem.brand]")

    # ── 3. Refuse to overwrite existing config ─────────────────────────────
    config_path = cwd / ".supamem" / "config.toml"
    if config_path.exists() and not force:
        err(f".supamem/config.toml already exists at {config_path}")
        info("re-run with --force to overwrite, or edit manually")
        return 3

    # ── 4. Create collection ───────────────────────────────────────────────
    try:
        client = _get_client(url)
        created = create_collection(client, collection, force=force)
        if not created:
            err(f"collection {collection!r} already exists")
            info("re-run with --force to recreate, or use `supamem migrate`")
            return 4
        ok(f"collection {collection!r} created")
    except Exception as exc:  # noqa: BLE001
        err(f"failed to create collection: {exc}")
        return 5

    # ── 5. Auto-detect sources ─────────────────────────────────────────────
    sources = _detect_sources(cwd)
    if sources:
        info(f"auto-detected sources: {', '.join(sources)}")
    else:
        warn("no source paths auto-detected (.claude/insights, .claude/rules, docs/, README.md)")

    # ── 6. Write .supamem/config.toml ──────────────────────────────────────
    _write_config(config_path, collection=collection, sources=sources)
    ok(f"wrote {config_path}")

    # ── 7. Initial indexer pass ────────────────────────────────────────────
    if sources and yes:
        info("running initial indexer pass")
        cfg = ResolvedConfig(
            qdrant_url=url,
            collection=collection,
            sources=[str(cwd / s) for s in sources],
        )
        try:
            from supamem.indexer import run_index

            run_index(target="tuned", force=True, sources=cfg.sources, config=cfg)
            ok("initial indexing complete")
        except Exception as exc:  # noqa: BLE001
            warn(f"initial indexing skipped: {exc}")
    elif sources:
        info("skipping initial indexer (run `supamem index` to populate)")

    # ── 8. Next-step prompt ────────────────────────────────────────────────
    info("next: wire your AI client")
    step("supamem install --client claude-code")
    step("supamem install --client cursor")
    step("supamem install --client opencode")
    return 0


__all__ = ["create_collection", "probe_qdrant", "run_init"]
