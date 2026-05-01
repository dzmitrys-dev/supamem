"""Path-component classifier for indexer chunks.

Per D-01 / D-01a / D-11 / D-12 (Phase 7 CONTEXT.md): pure, in-tree helper —
NOT a plugin entry-point in v1. First-match-wins by ``rooms`` insertion order
(TOML key order, preserved by tomllib + Python dict semantics per PEP 468).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def classify_room(
    file_path: str | Path,
    rooms: dict[str, list[str]],
) -> Optional[str]:
    """First-match-wins path-component classifier.

    Iterate ``rooms`` in insertion order; for each room, return its name
    if any keyword appears as an EXACT path component of ``file_path``.
    Returns ``None`` when no room matches.

    Uses ``Path.parts`` (OS-aware) and set intersection so substring
    matches like ``data/chest_xray/img.png`` cannot trigger ``tests``
    via ``"test" in "chest_xray"`` (CLASS-02 negative case).

    Note (Path.parts gotcha, RESEARCH Pitfall 6): absolute paths include
    the root marker (``"/"`` on POSIX, ``"C:\\"`` on Windows) as the
    first part. Set intersection ignores it because no valid keyword
    equals the root marker; do NOT special-case.
    """
    parts = set(Path(file_path).parts)
    for room, keywords in rooms.items():
        if parts & set(keywords):
            return room
    return None
