"""Phase 15 Plan C Task C1 — envelope_from_results tests.

Locks:
- Envelope shape: 2 axes × 3 columns × 8 metric names + report_schema_version="coderag.v1".
- INV-A1: decision_rationale.fastapi_only is None → combined collapses to supamem_only.
- Empty peers default; peer rows preserve full shape.
- Envelope is JSON-serializable.
- PYTREC_TO_ENVELOPE name translation surface is exposed.
"""
from __future__ import annotations

import json

from supamem.eval.coderag import report
from supamem.eval.coderag.report import (
    AXIS_NAMES,
    COLUMN_NAMES,
    METRIC_NAMES,
    PYTREC_TO_ENVELOPE,
    REPORT_SCHEMA_VERSION,
    column_metrics,
    envelope_from_results,
)


# Helpers -----------------------------------------------------------------------


def _pytrec_dummy() -> dict[str, float]:
    return {
        "recall_1": 0.1,
        "recall_5": 0.3,
        "recall_10": 0.5,
        "recall_20": 0.7,
        "recip_rank": 0.4,
        "ndcg_cut_10": 0.42,
    }


def _full_axis_block(p50: float = 12.0, p95: float = 30.0) -> dict[str, dict | None]:
    cm = column_metrics(_pytrec_dummy(), p50, p95)
    return {col: cm for col in COLUMN_NAMES}


def _decision_axis_block_with_null_fastapi() -> dict[str, dict | None]:
    cm = column_metrics(_pytrec_dummy(), 11.0, 22.0)
    return {
        "supamem_only": cm,
        "fastapi_only": None,
        "combined": cm,  # caller may pass either; envelope_from_results enforces
    }


# Tests -------------------------------------------------------------------------


def test_pytrec_to_envelope_translation_surface_exposed() -> None:
    assert isinstance(PYTREC_TO_ENVELOPE, dict)
    # 6 pytrec keys → 6 envelope keys
    assert len(PYTREC_TO_ENVELOPE) == 6
    # Every envelope target name is present in METRIC_NAMES
    for envelope_key in PYTREC_TO_ENVELOPE.values():
        assert envelope_key in METRIC_NAMES


def test_column_metrics_translates_pytrec_keys() -> None:
    cm = column_metrics(_pytrec_dummy(), 5.0, 12.0)
    assert cm is not None
    # The 6 ranking metrics are present under their envelope names
    assert cm["recall_at_1"] == 0.1
    assert cm["recall_at_5"] == 0.3
    assert cm["recall_at_10"] == 0.5
    assert cm["recall_at_20"] == 0.7
    assert cm["mrr"] == 0.4
    assert cm["ndcg_at_10"] == 0.42
    assert cm["latency_ms_p50"] == 5.0
    assert cm["latency_ms_p95"] == 12.0


def test_column_metrics_none_passes_through() -> None:
    assert column_metrics(None, 1.0, 2.0) is None


def test_envelope_from_results_full_shape() -> None:
    per_axis = {
        "code_fact": _full_axis_block(),
        "decision_rationale": _full_axis_block(),
    }
    env = envelope_from_results(per_axis)
    assert env["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert REPORT_SCHEMA_VERSION == "coderag.v1"
    assert set(env["scores"].keys()) == set(AXIS_NAMES)
    for axis in AXIS_NAMES:
        assert set(env["scores"][axis].keys()) == set(COLUMN_NAMES)
        for col in COLUMN_NAMES:
            cell = env["scores"][axis][col]
            assert cell is not None
            assert set(cell.keys()) == set(METRIC_NAMES)


def test_envelope_decision_rationale_fastapi_only_is_null() -> None:
    # INV-A1
    per_axis = {
        "code_fact": _full_axis_block(),
        "decision_rationale": _decision_axis_block_with_null_fastapi(),
    }
    env = envelope_from_results(per_axis)
    dr = env["scores"]["decision_rationale"]
    assert dr["fastapi_only"] is None
    # combined collapses to supamem_only (deep equality)
    assert dr["combined"] == dr["supamem_only"]


def test_envelope_decision_rationale_fastapi_null_collapses_combined_even_if_caller_passes_none() -> None:
    # Caller passes combined=None too — envelope_from_results must still
    # collapse combined to supamem_only when fastapi_only is None.
    cm = column_metrics(_pytrec_dummy(), 1.0, 2.0)
    per_axis = {
        "code_fact": _full_axis_block(),
        "decision_rationale": {
            "supamem_only": cm,
            "fastapi_only": None,
            "combined": None,  # explicit; INV-A1 says collapse
        },
    }
    env = envelope_from_results(per_axis)
    dr = env["scores"]["decision_rationale"]
    assert dr["fastapi_only"] is None
    assert dr["combined"] == dr["supamem_only"]
    assert dr["supamem_only"] is cm or dr["supamem_only"] == cm


def test_envelope_peers_default_empty() -> None:
    env = envelope_from_results({"code_fact": _full_axis_block()})
    assert env["peers"] == {}


def test_envelope_peer_row_shape() -> None:
    peers = {"mem0": {"some": "axis_data"}}
    env = envelope_from_results({"code_fact": _full_axis_block()}, peers=peers)
    assert env["peers"] == peers


def test_envelope_serializes_to_json() -> None:
    per_axis = {
        "code_fact": _full_axis_block(),
        "decision_rationale": _decision_axis_block_with_null_fastapi(),
    }
    env = envelope_from_results(per_axis, peers={"mem0": {"placeholder": True}})
    # Round-trip via JSON — fail loud if any non-serializable type slipped in.
    blob = json.dumps(env)
    parsed = json.loads(blob)
    assert parsed["report_schema_version"] == "coderag.v1"
    assert parsed["scores"]["decision_rationale"]["fastapi_only"] is None


def test_empty_envelope_still_works() -> None:
    # 15-A baseline carry: empty_envelope() still callable and returns shape.
    env = report.empty_envelope()
    assert env["report_schema_version"] == "coderag.v1"
    assert set(env["scores"].keys()) == set(AXIS_NAMES)
