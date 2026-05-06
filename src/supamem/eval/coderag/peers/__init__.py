"""Peer adapters for the coderag suite.

Sub-package marker. Each peer adapter (e.g. ``mem0_adapter``) is imported
LAZILY by the caller — this ``__init__`` MUST NOT import any optional peer
dependency (``mem0``, etc.) at the module top level. Doing so would force
a hard dep on every ``import supamem.eval.coderag.peers`` and break the
``mem0ai`` peers-extras opt-in.

Plan 15-D Task D1.
"""
from __future__ import annotations

__all__ = ["mem0_adapter"]
