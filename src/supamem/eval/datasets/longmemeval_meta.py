"""LongMemEval_S dataset metadata constants (D-VEND-02).

Pinning the upstream HuggingFace revision SHA in source isolates this
phase from upstream re-uploads / silent re-shuffles. ``supamem doctor``
compares the cached revision SHA against :data:`PINNED_REVISION` to
surface drift, and the bench runner records the resolved SHA in the
report envelope (D-REPORT-01 ``dataset.revision``).

The 5-axis taxonomy below matches the LongMemEval paper
(https://arxiv.org/abs/2410.10813). Upstream ships an additional sixth
label ``single-session-preference`` — that label is excluded from
:data:`AXES` because Phase 10's milestone gate is defined against the
canonical 5-axis split. The loader's internal alias map drops records
that fall outside this set so the smoke subset and main_score remain
deterministic across upstream re-tags.

Constants are module-level (not enums) so that tooling — doctor,
report writer, smoke-subset builder — can ``import`` without paying a
class-construction cost on every CLI invocation.
"""
from __future__ import annotations

# HuggingFace dataset coordinates.
REPO_ID: str = "xiaowu0162/longmemeval-cleaned"

# Pinned revision SHA — resolved once via
# ``HfApi().dataset_info(REPO_ID).sha`` against ``main`` and frozen here.
# NEVER bump without coordinating with the doctor drift surface and the
# baseline JSON in ``src/supamem/eval/baselines/`` (D-BASE-01).
PINNED_REVISION: str = "98d7416c24c778c2fee6e6f3006e7a073259d48f"

# Canonical axis taxonomy (paper-aligned, underscore-normalized).
AXES: tuple[str, ...] = (
    "single_session_user",
    "single_session_assistant",
    "multi_session",
    "temporal_reasoning",
    "knowledge_update",
)

# Internal name; surfaced in the report envelope as ``dataset.name``.
DATASET_NAME: str = "longmemeval_s"

# Informational; the loader yields whatever the upstream revision contains
# after axis filtering. Hard-asserting this would couple us to the upstream
# count, which has shifted across re-uploads.
EXPECTED_QUESTION_COUNT: int = 500
