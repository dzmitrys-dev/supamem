"""Reranker plugin loader for supamem (entry-point group: supamem.reranker).

Plugin contract (D-CONTRACT-01..05 / D-POOL-02 / D-CONFIG-03):

    rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]

``name == "off"`` is the reserved sentinel meaning "no plugin" — the loader
returns ``None`` *without* iterating entry-points (cheap fast-path; preserves
pre-Phase-8 byte-identical retrieval behavior).
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supamem.config import ResolvedConfig
    from supamem.retrieval.types import RetrievedChunk


@runtime_checkable
class RerankerProtocol(Protocol):
    """Public surface every supamem.reranker plugin satisfies."""

    name: str
    model_id: str

    def rerank(
        self, query: str, candidates: list["RetrievedChunk"]
    ) -> list["RetrievedChunk"]:
        ...


def load_reranker(name: str, config: "ResolvedConfig") -> Optional[Any]:
    """Resolve and instantiate a registered ``supamem.reranker`` plugin.

    Returns ``None`` for the sentinel ``"off"`` without iterating entry-points.
    Raises :class:`LookupError` for an unknown name with the list of registered
    plugin names included in the message.
    """
    if name == "off":
        return None
    registered: list[str] = []
    for ep in entry_points(group="supamem.reranker"):
        registered.append(ep.name)
        if ep.name == name:
            cls = ep.load()
            return cls(config=config)
    raise LookupError(
        f"supamem: no reranker plugin registered for name={name!r} "
        f"(known: {sorted(registered) or '[]'})"
    )


# --- Eager-fetch helper (D-FETCH-01..07) -------------------------------------
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import platformdirs  # noqa: E402
from filelock import FileLock, Timeout  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402

from supamem.console import err_console  # noqa: E402

_BACKOFF_BASE_S: float = 1.0  # overridden in tests for speed
_MAX_RETRIES: int = 3
_LOCK_TIMEOUT_S: int = 3600
_ALLOW_PATTERNS: list[str] = [
    "*.safetensors",
    "*.json",
    "tokenizer*",
    "*.txt",
    "*.model",
]


def _model_cache_dir() -> Path:
    """Return the supamem-owned model cache root.

    Honors ``SUPAMEM_CACHE_DIR`` env override (used by tests). Defaults to
    ``platformdirs.user_cache_dir("supamem")/models`` (D-FETCH-05).
    """
    override = os.environ.get("SUPAMEM_CACHE_DIR")
    if override:
        return Path(override) / "models"
    return Path(platformdirs.user_cache_dir("supamem")) / "models"


def _write_expected_manifest(snapshot_dir: Path) -> None:
    """Record file list + cumulative byte count for partial-download detection.

    Plan 08-03's doctor reads this to surface partial-download warnings.
    """
    files: dict[str, int] = {}
    total = 0
    for p in snapshot_dir.rglob("*"):
        if p.is_file() and p.name != "_expected_manifest.json":
            size = p.stat().st_size
            files[p.relative_to(snapshot_dir).as_posix()] = size
            total += size
    manifest = {"files": files, "total_bytes": total, "schema": 1}
    (snapshot_dir / "_expected_manifest.json").write_text(
        json.dumps(manifest, indent=2)
    )


def _manifest_matches(model_id: str) -> bool:
    """Return True iff a healthy snapshot exists matching its manifest.

    Used by ``prepare()`` to short-circuit the network roundtrip on
    already-cached models (D-FETCH-03 idempotency for ``repair()``).
    Returns False on: missing snapshot dir, missing manifest, byte-count
    mismatch, or any missing file from the manifest.
    """
    cache_root = _model_cache_dir()
    slug = model_id.replace("/", "--")
    # HF layout: models--<slug>/snapshots/<rev>/  ; tests may also write <slug>/<dir>/
    candidates = list(cache_root.glob(f"models--{slug}/snapshots/*")) or list(
        cache_root.glob(f"{slug}/*")
    )
    for snap in candidates:
        manifest_path = snap / "_expected_manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            m = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        expected = m.get("files", {})
        ok = True
        for rel, expected_size in expected.items():
            p = snap / rel
            if not p.is_file() or p.stat().st_size != expected_size:
                ok = False
                break
        if ok:
            return True
    return False


def prepare(model_id: str, *, progress=None) -> Optional[Path]:
    """Eager-fetch a HuggingFace model into supamem's cache.

    Idempotent: if ``_manifest_matches(model_id)`` returns True, returns the
    existing snapshot path WITHOUT calling snapshot_download (D-FETCH-03).
    Otherwise uses huggingface_hub.snapshot_download under a filelock with
    3-attempt exponential backoff retry. Writes ``_expected_manifest.json``
    next to the snapshot for Plan 08-03's doctor partial-download detector.

    Raises ``RuntimeError`` on terminal failure (after retries exhausted) with
    an actionable error message routed via ``err_console``.
    """
    # D-FETCH-03 idempotency gate: if manifest already matches, skip.
    if _manifest_matches(model_id):
        cache_root = _model_cache_dir()
        slug = model_id.replace("/", "--")
        existing = list(cache_root.glob(f"models--{slug}/snapshots/*")) or list(
            cache_root.glob(f"{slug}/*")
        )
        if existing:
            return existing[0]

    cache_dir = _model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".lock"
    try:
        lock = FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_S)
        with lock:
            last_err: Optional[Exception] = None
            for attempt in range(_MAX_RETRIES):
                try:
                    # Honor HF_HUB_OFFLINE at call-time (the env var is
                    # read once at module-import by huggingface_hub, so
                    # tests that set it post-import need explicit forwarding).
                    offline = os.environ.get("HF_HUB_OFFLINE", "") in ("1", "true", "True")
                    local = snapshot_download(
                        repo_id=model_id,
                        cache_dir=str(cache_dir),
                        allow_patterns=_ALLOW_PATTERNS,
                        local_files_only=offline,
                    )
                    snap = Path(local)
                    _write_expected_manifest(snap)
                    return snap
                except Exception as exc:  # noqa: BLE001 — retry layer
                    last_err = exc
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_BACKOFF_BASE_S * (2 ** attempt))
            err_console.print(
                f"[supamem.err]reranker fetch failed for {model_id!r} "
                f"after {_MAX_RETRIES} attempts: {last_err!r}. "
                f"Re-run `supamem repair` once network is available, or "
                f"set retrieval.reranker = 'off'."
            )
            raise RuntimeError(
                f"supamem: snapshot_download failed for {model_id}"
            ) from last_err
    except Timeout as exc:
        err_console.print(
            f"[supamem.err]reranker cache lock timeout at {lock_path}. "
            f"Another supamem install may be running. Retry shortly or "
            f"delete the stale lock if no install is active."
        )
        raise RuntimeError(
            f"supamem: cache lock timeout for {model_id}"
        ) from exc
