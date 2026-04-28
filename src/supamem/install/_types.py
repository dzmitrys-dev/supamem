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
