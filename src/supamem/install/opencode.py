"""opencode installer — full implementation lands in Plan 80.6-10 Task 2/3."""
from __future__ import annotations

from supamem.install._types import InstallResult


def install(*, dry_run: bool = False) -> InstallResult:  # noqa: ARG001
    raise NotImplementedError("supamem install --client opencode: lands in next commit")


def uninstall() -> int:
    raise NotImplementedError("supamem uninstall --client opencode: lands in next commit")
