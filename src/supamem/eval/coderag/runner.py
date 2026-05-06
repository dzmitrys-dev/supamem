"""``_run_coderag`` mirrors ``_run_longmemeval`` shape (A-D-PLAN-01: by FUNCTION
NAME, not line number).

Plan 15-A scope: returns the empty three-column-axis envelope. Plan 15-C wires
gold-doc scoring via :mod:`pytrec_eval`.
"""
from __future__ import annotations

from typing import Any


def _run_coderag(records, backend, *, k: int = 20, **kwargs) -> dict[str, Any]:  # noqa: ANN001, ANN003, ARG001
    """Per-query: 3 retrieval passes (supamem_only / fastapi_only / combined).

    Plan 15-A scope: returns the empty envelope shape.
    Plan 15-C scope: scoring + invariants.
    """
    from supamem.eval.coderag.report import empty_envelope

    return empty_envelope()
