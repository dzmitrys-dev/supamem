"""Phase 14 Plan B Task B1 — RED tests for the dual-pass per-record loop.

The scoped pass is emitted at the SINGLE LongMemEval call site
(``runner.py:_run_longmemeval`` per-record loop). Both passes execute
per loop iteration; smoke vs full is decided by the existing
``smoke_ids`` filter at ``:417`` — there is no second physical call site.

Pinned contract:

- Per-record dict carries nested ``metrics: {"unscoped", "scoped"}`` and
  ``latency_ms: {"unscoped", "scoped"}``.
- Scoped pass passes ``where={"session_id": list(rec.sessions)}`` to
  the backend (D-SCOPE-01 / D-SCOPE-03).
- Empty sessions skip the scoped pass entirely.
- Scoped pass runs with rerank-OFF cfg (D-FUT24-01 — strict isolation
  from FUTURE-24).
- Both per-pass sub-dicts carry exactly the 9 REPORT_METRIC_NAMES.
- ``filters.py`` is byte-identical (D-SCOPE-03) — covered by
  test_runner_goldens_legacy_byte_identical.py's filter-lock test.
"""
from __future__ import annotations

from typing import Any

import pytest

import supamem.eval.runner as runner_mod
from supamem.config import ResolvedConfig
from supamem.retrieval.types import RetrievedChunk


# --------------------------------------------------------------------------- #
# Helpers


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {
        "qdrant_url": "http://localhost:6333",
        "collection": "user_project",
        "reranker_name": "off",
    }
    base.update(overrides)
    return ResolvedConfig(**base)


def _chunk(text: str, *, cid: str = "x") -> RetrievedChunk:
    return RetrievedChunk(id=cid, text=text, score=0.9)


class _WhereAwareBackend:
    """Mock backend whose ``query`` returns different chunks per ``where``.

    - No ``where`` (unscoped): returns chunks A, B, C.
    - ``where={'session_id': [...]}`` (scoped): returns chunks B, D.

    Records every call's kwargs into ``calls`` for assertions.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        self.calls.append({"text": text, "k": k, "where": where})
        if where is None:
            return [
                _chunk("alpha unscoped chunk", cid="A"),
                _chunk("beta shared chunk", cid="B"),
                _chunk("gamma unscoped chunk", cid="C"),
            ]
        return [
            _chunk("beta shared chunk", cid="B"),
            _chunk("delta scoped chunk", cid="D"),
        ]


def _fake_record(
    *,
    rid: str = "q1",
    question: str = "What did I run on Tuesday?",
    answer: str = "5km at 6am",
    axis: str = "single_session_user",
    sessions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": rid,
        "question": question,
        "answer": answer,
        "axis": axis,
        "sessions": list(sessions) if sessions is not None else ["s_001", "s_002"],
    }


def _patch_loader(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    from supamem.eval import suite_loader as suite_loader_mod

    def _fake_load_longmemeval(*, dataset_path=None, cache_dir=None):
        for r in records:
            yield r

    monkeypatch.setattr(
        suite_loader_mod, "load_longmemeval", _fake_load_longmemeval
    )


def _capture_envelopes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from supamem.eval import report as report_mod

    captured: list[dict[str, Any]] = []

    def _fake_write_report(envelope: dict[str, Any], out_dir=None):
        captured.append(envelope)
        from pathlib import Path

        return Path("/tmp/fake-envelope.json")

    monkeypatch.setattr(report_mod, "write_report", _fake_write_report)
    return captured


def _run(
    monkeypatch: pytest.MonkeyPatch,
    backend: Any,
    records: list[dict[str, Any]],
    *,
    cfg: ResolvedConfig | None = None,
    track_backend_cfgs: list[ResolvedConfig] | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or _cfg()

    def _fake_build_backend(config, *, suite=None):
        if track_backend_cfgs is not None:
            track_backend_cfgs.append(config)
        return backend

    monkeypatch.setattr(runner_mod, "_build_backend", _fake_build_backend)

    _patch_loader(monkeypatch, records)
    captured = _capture_envelopes(monkeypatch)

    rc = runner_mod.run_bench(
        suite="longmemeval_s",
        full=True,
        config=cfg,
        verbose=True,
    )
    assert rc == 0, f"run_bench returned {rc}"
    assert captured, "no envelope captured"
    return list(captured[-1].get("per_question") or [])


# --------------------------------------------------------------------------- #
# Tests


def test_dual_pass_emitted_per_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """One record means backend.query called twice; per_record carries
    nested metrics dict reflecting different chunk lists per pass."""
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001"])
    per_record = _run(monkeypatch, backend, [rec])

    assert len(per_record) == 1
    row = per_record[0]
    assert "metrics" in row
    metrics = row["metrics"]
    assert isinstance(metrics, dict)
    assert "unscoped" in metrics and "scoped" in metrics

    u_wc = metrics["unscoped"]["write_cost"]
    s_wc = metrics["scoped"]["write_cost"]
    assert u_wc != s_wc, (
        f"expected differing write_cost across passes; got both {u_wc}"
    )


def test_scoped_pass_filter_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scoped call passes ``where={'session_id': [...verbatim...]}``."""
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001", "s_042", "s_xyz"])
    _run(monkeypatch, backend, [rec])

    scoped_calls = [c for c in backend.calls if c["where"] is not None]
    assert len(scoped_calls) == 1
    where = scoped_calls[0]["where"]
    assert set(where.keys()) == {"session_id"}, (
        f"scoped where must carry only 'session_id'; got {sorted(where)}"
    )
    assert where["session_id"] == ["s_001", "s_042", "s_xyz"]


def test_empty_sessions_skips_scoped_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty rec.sessions: backend.query called ONCE (unscoped only)."""
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=[])
    per_record = _run(monkeypatch, backend, [rec])

    assert len(backend.calls) == 1, (
        f"expected exactly one query call; got {len(backend.calls)}"
    )
    assert backend.calls[0]["where"] is None

    metrics = per_record[0]["metrics"]
    scoped = metrics.get("scoped")
    if scoped is None:
        return
    unscoped = metrics["unscoped"]
    duplicate = all(
        scoped.get(k) == unscoped.get(k)
        for k in ("write_cost", "tokens_per_correct_answer")
    )
    assert not duplicate, (
        "scoped block must not silently mirror unscoped when no sessions"
    )


def test_scoped_pass_uses_rerank_off_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when unscoped backend cfg has rerank-on, scoped backend cfg has
    reranker_name='off' (D-FUT24-01)."""
    cfgs_seen: list[ResolvedConfig] = []
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001"])
    cfg_on = _cfg(reranker_name="mxbai_v2")
    _run(
        monkeypatch,
        backend,
        [rec],
        cfg=cfg_on,
        track_backend_cfgs=cfgs_seen,
    )

    off_cfgs = [c for c in cfgs_seen if getattr(c, "reranker_name", None) == "off"]
    assert off_cfgs, (
        f"expected at least one backend constructed with reranker_name='off' "
        f"(D-FUT24-01); cfgs seen: "
        f"{[getattr(c, 'reranker_name', None) for c in cfgs_seen]}"
    )


def test_per_record_metrics_nested_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both sub-dicts carry exactly the 9 REPORT_METRIC_NAMES."""
    from supamem.eval.report import REPORT_METRIC_NAMES

    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001"])
    per_record = _run(monkeypatch, backend, [rec])

    metrics = per_record[0]["metrics"]
    assert set(metrics.keys()) >= {"unscoped", "scoped"}
    for name in ("unscoped", "scoped"):
        sub = metrics[name]
        if sub is None:
            continue
        assert set(sub.keys()) == set(REPORT_METRIC_NAMES), (
            f"{name} sub-dict drifted from REPORT_METRIC_NAMES"
        )


def test_per_record_latency_ms_nested_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """latency_ms is dict with keys 'unscoped' and 'scoped'."""
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001"])
    per_record = _run(monkeypatch, backend, [rec])

    lat = per_record[0]["latency_ms"]
    assert isinstance(lat, dict)
    assert set(lat.keys()) >= {"unscoped", "scoped"}
    assert isinstance(lat["unscoped"], float)
    assert lat["scoped"] is None or isinstance(lat["scoped"], float)


def test_smoke_vs_full_via_smoke_ids_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke filter at runner.py:~446 is the single physical call site gate."""
    backend = _WhereAwareBackend()
    rec_a = _fake_record(rid="keep-me", sessions=["s_a"])
    rec_b = _fake_record(rid="skip-me", sessions=["s_b"])

    monkeypatch.setattr(runner_mod, "_resolve_smoke_ids", lambda: {"keep-me"})

    cfg = _cfg()

    def _fake_build_backend(config, *, suite=None):
        return backend

    monkeypatch.setattr(runner_mod, "_build_backend", _fake_build_backend)
    _patch_loader(monkeypatch, [rec_a, rec_b])
    captured = _capture_envelopes(monkeypatch)

    rc = runner_mod.run_bench(suite="longmemeval_s", full=False, config=cfg, verbose=True)
    assert rc == 0
    per_record = captured[-1].get("per_question") or []
    rids_seen = [r["id"] for r in per_record]
    assert rids_seen == ["keep-me"], rids_seen
    assert len(backend.calls) == 2


def test_record_metrics_helper_exists() -> None:
    """A helper that computes the 9 metrics for one chunk list is exposed."""
    assert hasattr(runner_mod, "_record_metrics"), (
        "expected runner_mod._record_metrics helper "
        "(plan task B1 action item 1)"
    )


def test_aggregate_by_axis_per_pass_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aggregator surfaces per-pass structure on the envelope's by_axis."""
    backend = _WhereAwareBackend()
    rec = _fake_record(sessions=["s_001"])
    _run(monkeypatch, backend, [rec])

    from supamem.eval import report as report_mod

    captured: list[dict[str, Any]] = []

    def _fake_write_report(envelope, out_dir=None):
        captured.append(envelope)
        from pathlib import Path

        return Path("/tmp/fake.json")

    monkeypatch.setattr(report_mod, "write_report", _fake_write_report)

    def _fake_build_backend(config, *, suite=None):
        return backend

    monkeypatch.setattr(runner_mod, "_build_backend", _fake_build_backend)
    _patch_loader(monkeypatch, [rec])

    runner_mod.run_bench(suite="longmemeval_s", full=True, config=_cfg(), verbose=False)
    assert captured
    by_axis = captured[-1]["by_axis"]
    assert isinstance(by_axis, dict)
    assert "unscoped" in by_axis or "scoped" in by_axis or any(
        isinstance(v, dict) and ("unscoped" in v or "scoped" in v)
        for v in by_axis.values()
    ), (
        f"by_axis must surface per-pass structure; got top-level keys "
        f"{sorted(by_axis)}"
    )
