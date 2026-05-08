"""Phase 16 Plan D — envelope.peers + envelope.comparisons schema (Req-04 part 2).

Locks (D-PEER-01..03):

- D-PEER-01: ``envelope["peers"][peer]["scores"]`` mirrors ``envelope["scores"]``
  nesting verbatim (axis × repo_column × metric).
- D-PEER-02: ``envelope["comparisons"]["{peer}_vs_supamem"][axis][col][metric]``
  carries ``{delta, ci_lower, ci_upper, n_resamples, seed, qualitative}``.
  Sign: positive delta ⇒ peer is better (``mean(peer) - mean(supamem)``).
- D-PEER-03: non-peer runs emit ``peers: {}`` AND ``comparisons: {}`` —
  empty dicts, not absent keys.

Qualitative derivation rule (mechanical, no human judgement):

- ``qualitative == "win"``  ⇔ ``ci_lower > 0``  (peer beats supamem)
- ``qualitative == "loss"`` ⇔ ``ci_upper < 0``  (peer loses to supamem)
- ``qualitative == "tie"``  otherwise            (CI brackets zero)
"""
from __future__ import annotations

from supamem.eval.coderag.report import (
    AXIS_NAMES,
    COLUMN_NAMES,
    METRIC_NAMES,
    column_metrics,
    envelope_from_results,
)


# Synthetic-input helpers --------------------------------------------------------


def _pytrec_dummy(scale: float = 1.0) -> dict[str, float]:
    return {
        "recall_1": 0.1 * scale,
        "recall_5": 0.3 * scale,
        "recall_10": 0.5 * scale,
        "recall_20": 0.7 * scale,
        "recip_rank": 0.4 * scale,
        "ndcg_cut_10": 0.42 * scale,
    }


def _full_axis_block(scale: float = 1.0) -> dict[str, dict | None]:
    cm = column_metrics(_pytrec_dummy(scale), 12.0, 30.0)
    return {col: cm for col in COLUMN_NAMES}


def _full_per_axis(scale: float = 1.0) -> dict[str, dict[str, dict | None]]:
    return {axis: _full_axis_block(scale) for axis in AXIS_NAMES}


def _per_query_metrics(value: float, n_queries: int = 50) -> dict:
    """Build per_query_metrics dict matching axis × col × metric × q_id nesting.

    Every (q_id) maps to ``value`` so the paired-bootstrap delta is deterministic
    by construction.
    """
    return {
        axis: {
            col: {
                metric: {f"q{i}": value for i in range(n_queries)}
                for metric in METRIC_NAMES
            }
            for col in COLUMN_NAMES
        }
        for axis in AXIS_NAMES
    }


# Test 1 — non-peer schema-compat (D-PEER-03) ------------------------------------


def test_non_peer_envelope_has_empty_peers_and_comparisons() -> None:
    """D-PEER-03: non-`--peer` runs emit BOTH ``peers: {}`` and ``comparisons: {}``."""
    env = envelope_from_results(_full_per_axis())
    assert env["peers"] == {}
    assert env["comparisons"] == {}
    # Both keys present, not absent (schema-compat lock).
    assert "peers" in env
    assert "comparisons" in env


# Test 2 — peers nesting mirrors scores (D-PEER-01) ------------------------------


def test_peers_scores_mirror_supamem_scores_nesting() -> None:
    """D-PEER-01: envelope.peers.mem0.scores has identical axis × col × metric nesting."""
    sup_per_axis = _full_per_axis(scale=1.0)
    peer_scores_payload = _full_per_axis(scale=1.1)  # arbitrary mirror payload

    # Build a peer scores blob in the same axis × col × metric shape as supamem's
    # rendered envelope[scores]. The caller (16-E) is responsible for shaping
    # this — we feed it through verbatim.
    peer_scores_blob = {
        axis: {
            col: peer_scores_payload[axis][col]
            for col in COLUMN_NAMES
        }
        for axis in AXIS_NAMES
    }
    peer_run_data = {
        "mem0": {
            "scores": peer_scores_blob,
            "per_query_metrics": _per_query_metrics(0.5),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(sup_per_axis, peer_run_data=peer_run_data)
    assert "mem0" in env["peers"]
    peer_scores = env["peers"]["mem0"]["scores"]
    # Every leaf path in supamem scores must also exist under peers.mem0.scores.
    for axis in AXIS_NAMES:
        assert axis in peer_scores
        for col in COLUMN_NAMES:
            assert col in peer_scores[axis]
            for metric in METRIC_NAMES:
                assert metric in peer_scores[axis][col]


# Test 3 — comparisons keying + leaf-key set (D-PEER-02) -------------------------


def test_comparisons_keying_and_leaf_key_set() -> None:
    """D-PEER-02: comparisons keyed `<peer>_vs_supamem` with exact leaf-key set."""
    expected_leaf_keys = {
        "delta",
        "ci_lower",
        "ci_upper",
        "n_resamples",
        "seed",
        "qualitative",
    }
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(scale=1.0),
            "per_query_metrics": _per_query_metrics(0.6),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    assert "mem0_vs_supamem" in env["comparisons"]
    comp = env["comparisons"]["mem0_vs_supamem"]
    for axis in AXIS_NAMES:
        assert axis in comp
        for col in COLUMN_NAMES:
            assert col in comp[axis]
            for metric in METRIC_NAMES:
                leaf = comp[axis][col][metric]
                assert set(leaf.keys()) == expected_leaf_keys, (
                    f"unexpected leaf keys at {axis}.{col}.{metric}: {leaf.keys()}"
                )


# Test 4 — qualitative derivation rule (win / loss / tie) ------------------------


def test_qualitative_win_when_peer_uniformly_higher() -> None:
    """Peer uniformly higher by 0.1 across all queries → ci_lower > 0 → qualitative='win'."""
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(scale=1.1),
            "per_query_metrics": _per_query_metrics(0.6),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    leaf = env["comparisons"]["mem0_vs_supamem"]["code_fact"]["supamem_only"]["recall_at_5"]
    assert leaf["ci_lower"] > 0
    assert leaf["qualitative"] == "win"


def test_qualitative_loss_when_peer_uniformly_lower() -> None:
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(scale=0.9),
            "per_query_metrics": _per_query_metrics(0.4),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    leaf = env["comparisons"]["mem0_vs_supamem"]["code_fact"]["supamem_only"]["recall_at_5"]
    assert leaf["ci_upper"] < 0
    assert leaf["qualitative"] == "loss"


def test_qualitative_tie_when_peer_equals_supamem() -> None:
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(),
            "per_query_metrics": _per_query_metrics(0.5),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    leaf = env["comparisons"]["mem0_vs_supamem"]["code_fact"]["supamem_only"]["recall_at_5"]
    # Identical paired arrays → delta exactly 0; CI brackets zero.
    assert leaf["delta"] == 0.0
    assert leaf["ci_lower"] <= 0 <= leaf["ci_upper"]
    assert leaf["qualitative"] == "tie"


# Test 5 — sign convention (D-PEER-02) -------------------------------------------


def test_sign_convention_peer_better_means_positive_delta() -> None:
    """`<peer>_vs_supamem` = mean(peer) - mean(supamem); peer > supamem ⇒ delta > 0."""
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(scale=1.1),
            "per_query_metrics": _per_query_metrics(0.6),  # peer
            "supamem_per_query_metrics": _per_query_metrics(0.5),  # supamem
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    leaf = env["comparisons"]["mem0_vs_supamem"]["code_fact"]["supamem_only"]["recall_at_5"]
    assert leaf["delta"] > 0, "D-PEER-02 sign: peer better ⇒ delta > 0"


def test_sign_convention_peer_worse_means_negative_delta() -> None:
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(scale=0.9),
            "per_query_metrics": _per_query_metrics(0.4),  # peer
            "supamem_per_query_metrics": _per_query_metrics(0.5),  # supamem
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    leaf = env["comparisons"]["mem0_vs_supamem"]["code_fact"]["supamem_only"]["recall_at_5"]
    assert leaf["delta"] < 0, "D-PEER-02 sign: peer worse ⇒ delta < 0"


# Test 6 — n_resamples + seed echoed in every comparisons leaf -------------------


def test_n_resamples_and_seed_default_propagate_to_envelope() -> None:
    """paired_bootstrap_delta defaults (n_resamples=10000, seed=42) appear at every leaf."""
    peer_run_data = {
        "mem0": {
            "scores": _full_per_axis(),
            "per_query_metrics": _per_query_metrics(0.5),
            "supamem_per_query_metrics": _per_query_metrics(0.5),
        },
    }
    env = envelope_from_results(_full_per_axis(), peer_run_data=peer_run_data)
    comp = env["comparisons"]["mem0_vs_supamem"]
    for axis in AXIS_NAMES:
        for col in COLUMN_NAMES:
            for metric in METRIC_NAMES:
                leaf = comp[axis][col][metric]
                assert leaf["n_resamples"] == 10000
                assert leaf["seed"] == 42
