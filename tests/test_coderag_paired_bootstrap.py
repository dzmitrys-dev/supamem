"""Phase 16 Plan C — paired_bootstrap_delta TDD acceptance.

Locks:
- D-BOOT-01: hand-rolled numpy + percentile CI; ~25 LOC; no scipy.
- D-BOOT-02: caller is responsible for pairing samples by query_id.
- D-BOOT-03: percentile CI sufficient at retrieval-eval scale.
- Sign convention: ``mean(samples_a) - mean(samples_b)``.
- Return-dict shape: ``{"delta", "ci_lower", "ci_upper", "n_resamples", "seed"}``.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest


def test_identical_inputs_yield_zero_delta_and_ci_brackets_zero() -> None:
    """Identical paired samples → delta == 0.0 exactly; CI brackets zero."""
    from supamem.eval.coderag.metrics import paired_bootstrap_delta

    samples_a = np.array([0.5, 0.7, 0.6, 0.4, 0.8] * 10, dtype=float)
    samples_b = samples_a.copy()
    result = paired_bootstrap_delta(samples_a, samples_b)
    assert result["delta"] == 0.0
    assert result["ci_lower"] <= 0.0 <= result["ci_upper"]


def test_a_uniformly_higher_than_b_yields_positive_delta_and_ci_excludes_zero() -> None:
    """samples_a = samples_b + 0.1 → delta ≈ 0.1, CI does not bracket zero."""
    from supamem.eval.coderag.metrics import paired_bootstrap_delta

    samples_a = np.array([0.6, 0.8, 0.7, 0.5, 0.9] * 10, dtype=float)
    samples_b = samples_a - 0.1
    result = paired_bootstrap_delta(samples_a, samples_b)
    assert result["delta"] > 0.0
    assert 0.099 <= result["delta"] <= 0.101
    assert result["ci_lower"] > 0.0


def test_return_shape_contract() -> None:
    """Result is a dict with exactly the documented keys + types."""
    from supamem.eval.coderag.metrics import paired_bootstrap_delta

    samples_a = np.array([0.5, 0.7, 0.6, 0.4, 0.8] * 10, dtype=float)
    samples_b = np.array([0.4, 0.6, 0.5, 0.3, 0.7] * 10, dtype=float)
    result = paired_bootstrap_delta(samples_a, samples_b)
    assert set(result.keys()) == {"delta", "ci_lower", "ci_upper", "n_resamples", "seed"}
    assert isinstance(result["delta"], float)
    assert isinstance(result["ci_lower"], float)
    assert isinstance(result["ci_upper"], float)
    assert isinstance(result["n_resamples"], int)
    assert isinstance(result["seed"], int)
    assert result["n_resamples"] == 10000
    assert result["seed"] == 42


def test_deterministic_seed_byte_identical_across_runs() -> None:
    """Two invocations with same inputs and seed=42 return byte-identical dicts."""
    from supamem.eval.coderag.metrics import paired_bootstrap_delta

    samples_a = np.array([0.5, 0.7, 0.6, 0.4, 0.8] * 10, dtype=float)
    samples_b = np.array([0.4, 0.6, 0.5, 0.3, 0.7] * 10, dtype=float)
    a = paired_bootstrap_delta(samples_a, samples_b)
    b = paired_bootstrap_delta(samples_a, samples_b)
    assert a == b
    for k in a:
        assert a[k] == b[k]


def test_no_scipy_import_path() -> None:
    """D-BOOT-01 / D-BOOT-05: importing metrics MUST NOT pull scipy in.

    Touches ``paired_bootstrap_delta`` first so the test fails in RED (the
    function does not exist yet) and only validates the no-scipy lock once
    the GREEN implementation lands.
    """
    sys.modules.pop("scipy", None)
    import importlib

    import supamem.eval.coderag.metrics as metrics_mod

    importlib.reload(metrics_mod)
    # RED: AttributeError until GREEN lands. GREEN: symbol exists, scipy stays out.
    assert hasattr(metrics_mod, "paired_bootstrap_delta")
    assert "scipy" not in sys.modules, "scipy leaked into metrics import path"


def test_custom_n_resamples_and_seed_override_echo() -> None:
    """Custom n_resamples + seed override are echoed in the result dict."""
    from supamem.eval.coderag.metrics import paired_bootstrap_delta

    samples_a = np.array([0.5, 0.7, 0.6, 0.4, 0.8] * 10, dtype=float)
    samples_b = np.array([0.4, 0.6, 0.5, 0.3, 0.7] * 10, dtype=float)
    result = paired_bootstrap_delta(samples_a, samples_b, n_resamples=500, seed=7)
    assert result["n_resamples"] == 500
    assert result["seed"] == 7
