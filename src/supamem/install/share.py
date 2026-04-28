"""Sync packaged share/ artifacts to the user's ~/.supamem/share/ tree.

The supamem package bundles canonical rules / skills / commands / hooks /
cursor-rules under ``src/supamem/share/`` (also force-included into the
wheel). On first install (or version upgrade), ``ensure_share_dir`` copies
these into ``~/.supamem/share/`` so client configs can reference one
canonical path across every project — the SC-3 reference-not-copy contract.

Idempotent: files identical-by-sha are skipped.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from importlib import resources
from pathlib import Path

log = logging.getLogger("supamem.install.share")

DEFAULT_SHARE_DIR = Path.home() / ".supamem" / "share"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _packaged_share_root() -> Path:
    """Return the on-disk path to the packaged ``supamem.share`` tree."""
    files = resources.files("supamem.share")
    # ``files()`` returns a Traversable; for filesystem packages it has __fspath__.
    if hasattr(files, "__fspath__"):
        return Path(os.fspath(files))
    raise RuntimeError(
        "supamem.share is not on the filesystem — install via pip/uv, not zipapp"
    )


def ensure_share_dir(target: Path | None = None) -> list[Path]:
    """Sync packaged share assets into ``target`` (default ~/.supamem/share/).

    Returns the list of paths that were actually written (skipped sha-matches
    are NOT included). Files in target that are not in the packaged tree are
    left in place — users may add their own.
    """
    target = (target or DEFAULT_SHARE_DIR).resolve()
    target.mkdir(parents=True, exist_ok=True)
    src_root = _packaged_share_root()
    written: list[Path] = []

    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        # Skip Python package markers + bytecode — they're internal.
        if src.name == "__init__.py" or "__pycache__" in rel.parts or src.suffix == ".pyc":
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and _sha256(dst) == _sha256(src):
            continue
        shutil.copy2(src, dst)
        # Preserve executable bits for shell hooks.
        if src.suffix == ".sh":
            dst.chmod(0o755)
        written.append(dst)
        log.debug("synced %s → %s", src, dst)

    return written
