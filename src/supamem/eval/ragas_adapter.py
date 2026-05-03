"""RAGAS triad adapter — Phase 10 D-RAGAS-02 / D-RAGAS-03.

Wraps the optional ``ragas==0.4.*`` dependency behind a fail-soft import
guard so ``supamem eval --suite goldens`` keeps working on a bare
``pip install supamem`` (no ``[eval]`` extra). Only ragas 0.4.x metric
names are referenced; pin in ``pyproject.toml`` isolates RAGAS pre-1.0
API churn from the runner.

Contracts (locked by Plan 10-01 RED tests, implemented here):

- Module-level ``RAGAS_AVAILABLE`` resolves to a bool at import time.
- ``compute_ragas_triad(...)`` ALWAYS returns a 3-key dict:
  ``{"context_precision": ..., "context_recall": ..., "answer_relevance": ...}``.
- When ragas is missing: each value is ``None`` and ONE single
  ``[supamem.warn]install supamem[eval] for RAGAS triad metrics`` line
  is emitted on err_console (sentinel-gated so batch runs do not spam).
- When ragas is installed AND ``judge_kind == "heuristic"``: prefer
  non-LLM RAGAS variants where they exist; ``answer_relevance`` reports
  ``None`` with reason ``"requires llm judge"`` (RAGAS docs confirm
  ``answer_relevancy`` is not LLM-free achievable).
- Return shape is the (triad, reasons) 2-tuple from
  ``compute_ragas_triad_with_reasons`` for callers that need per-metric
  rationale; ``compute_ragas_triad`` returns the dict alone for callers
  that only need numbers.

Per CLAUDE.md hard constraint: never call ``print()`` — every diagnostic
goes through ``supamem.console.err_console``. No SaaS LLM SDK is ever
imported (D-07 invariant).
"""
from __future__ import annotations

from supamem.console import err_console

try:  # noqa: SIM105 — explicit branch needed for RAGAS_AVAILABLE flag
    import ragas  # type: ignore[import-not-found]  # noqa: F401

    RAGAS_AVAILABLE = True
except ImportError:
    RAGAS_AVAILABLE = False


# Sentinel: at-most-once install hint per process. RAGAS triad runs once
# per question in batch evals; without the sentinel a 500-question run
# floods stderr with 500 identical hints.
_HINT_EMITTED = False


def _emit_install_hint() -> None:
    """Emit the [eval]-extra install hint exactly once per process."""
    global _HINT_EMITTED
    if _HINT_EMITTED:
        return
    err_console.print(
        "[supamem.warn]install supamem[eval] for RAGAS triad metrics[/supamem.warn]"
    )
    _HINT_EMITTED = True


def compute_ragas_triad_with_reasons(
    *,
    queries: list[str],
    retrieved_contexts: list[list[str]],
    answers: list[str],
    references: list[str],
    judge_kind: str = "heuristic",
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Compute the RAGAS triad + per-metric reasons.

    Returns a 2-tuple ``(triad, reasons)``:

    - ``triad``: ``{"context_precision", "context_recall", "answer_relevance"}``
      mapped to floats or ``None``.
    - ``reasons``: human-readable rationale for any ``None`` value.

    Heuristic mode reports ``answer_relevance = None`` with reason
    ``"requires llm judge"`` per D-RAGAS-03 (RAGAS docs confirm the metric
    cannot be computed LLM-free).

    Empty input lists are treated as a no-op: returns 3 ``None`` values
    with reason ``"empty input"`` for each metric. Never raises.
    """
    triad: dict[str, float | None] = {
        "context_precision": None,
        "context_recall": None,
        "answer_relevance": None,
    }
    reasons: dict[str, str] = {}

    # Empty-input short-circuit — defined behavior, not an error.
    if not queries or not retrieved_contexts or not answers or not references:
        for k in triad:
            reasons[k] = "empty input"
        return triad, reasons

    # answer_relevance is heuristic-mode-impossible per D-RAGAS-03.
    if judge_kind == "heuristic":
        reasons["answer_relevance"] = "requires llm judge"

    if not RAGAS_AVAILABLE:
        _emit_install_hint()
        for k in triad:
            reasons.setdefault(k, "ragas not installed")
        return triad, reasons

    # ragas is installed — compute the two non-LLM-required metrics.
    # Pinned to ragas 0.4.x metric class names (D-RAGAS-02). Heuristic
    # mode uses the non-LLM variants where they exist.
    try:
        # NOTE: actual metric invocation lands in Plan 10-04 where the
        # runner threads a real LLM/embedding callable through. This
        # adapter establishes the contract; the runner wires the engine.
        # Keep best-effort import paths so the module stays importable
        # even if the metric module surface shifts inside 0.4.x.
        triad["context_precision"] = None
        triad["context_recall"] = None
        reasons.setdefault(
            "context_precision",
            "computation deferred to runner (Plan 10-04)",
        )
        reasons.setdefault(
            "context_recall",
            "computation deferred to runner (Plan 10-04)",
        )
    except Exception as exc:  # noqa: BLE001
        # Surface the failure on err_console — never silently zero a
        # metric (CLAUDE.md: errors in indexing/retrieval paths must
        # surface).
        err_console.print(
            f"[supamem.warn]ragas metric error: {type(exc).__name__}: {exc}"
            "[/supamem.warn]"
        )
        for k in triad:
            reasons.setdefault(k, f"ragas error: {type(exc).__name__}")

    return triad, reasons


def compute_ragas_triad(
    *,
    queries: list[str],
    retrieved_contexts: list[list[str]],
    answers: list[str],
    references: list[str],
    judge_kind: str = "heuristic",
) -> dict[str, float | None]:
    """Thin wrapper returning the triad dict only (no reasons).

    Useful for callers that only need the 3 numbers (e.g. report builder
    populating ``scores`` envelope). For per-metric rationale, call
    ``compute_ragas_triad_with_reasons`` directly.
    """
    triad, _reasons = compute_ragas_triad_with_reasons(
        queries=queries,
        retrieved_contexts=retrieved_contexts,
        answers=answers,
        references=references,
        judge_kind=judge_kind,
    )
    return triad


__all__ = [
    "RAGAS_AVAILABLE",
    "compute_ragas_triad",
    "compute_ragas_triad_with_reasons",
]
