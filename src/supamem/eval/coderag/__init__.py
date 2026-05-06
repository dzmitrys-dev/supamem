"""Phase 15 coderag suite — agentic-coding eval skeleton.

Bench-only. NEVER imports symbols from ``supamem.indexer.*`` (D-SCOPE-05
carry-lock). Plan 15-A wires the entry-point + skeleton; plan 15-B fills
the corpus-ingest body; plan 15-C wires gold scoring; 15-D plugs in the
mem0 peer adapter; 15-E ships docs.
"""
from __future__ import annotations

from supamem.eval.coderag.runner import _run_coderag


class CodeRAGSuite:
    """Suite class resolved by the ``supamem.eval`` entry-point group."""

    name = "coderag"
    report_schema_version = "coderag.v1"

    @staticmethod
    def run(records, backend, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return _run_coderag(records, backend, *args, **kwargs)


__all__ = ["CodeRAGSuite", "_run_coderag"]
