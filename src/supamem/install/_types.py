"""Shared types for installer modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InstallResult:
    written_files: list[Path] = field(default_factory=list)
    backup_files: list[Path] = field(default_factory=list)
    diff: str = ""
    no_op: bool = False
    # SM-7c: count of targets whose content differs (WriteResult.diff
    # non-empty / text differs) — the SAME accounting a real run uses to
    # decide writes, so a dry run's would-write prediction cannot diverge
    # from what the real run actually writes.
    would_write: int = 0
