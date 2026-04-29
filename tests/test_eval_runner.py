"""Tests for the eval bench runner (Plan 80.6-12)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from supamem.config import ResolvedConfig
from supamem.eval.auto_goldens import assert_no_saas_llm_env, derive_required_substrings
from supamem.eval.runner import BUNDLED_GOLDENS, _load_goldens, run_bench


def _cfg(**overrides: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "test_eval"}
    base.update(overrides)
    return ResolvedConfig(**base)


def _hit(text: str) -> Any:
    """Build a RetrievedChunk-shaped object for mocked backend.query results."""
    from supamem.retrieval.types import RetrievedChunk

    return RetrievedChunk(id="x", text=text, score=0.9)


def test_run_bench_loads_bundled_goldens() -> None:
    """The bundled JSONL must resolve via importlib.resources and contain 33 records."""
    records = _load_goldens(None)
    assert len(records) == 33
    assert all("query" in r and "required_substrings" in r for r in records)


def test_run_bench_external_goldens_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user-supplied --goldens path overrides the bundled corpus."""
    p = tmp_path / "custom.jsonl"
    p.write_text(
        json.dumps({"id": "c1", "query": "hello", "required_substrings": ["world"]}) + "\n",
        encoding="utf-8",
    )

    fake = MagicMock()
    fake.query.return_value = [_hit("hello world from supamem")]

    import supamem.eval.runner as mod

    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    rc = run_bench(regress=False, goldens_path=str(p), config=_cfg())
    assert rc == 0
    fake.query.assert_called_once()


def test_run_bench_regress_passes_when_recall_above_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock backend always returns chunks with all required substrings → recall=1.0."""
    import supamem.eval.runner as mod

    fake = MagicMock()

    def _query(query: str, k: int = 5) -> list[Any]:
        # Build a "perfect recall" chunk text by concatenating the required
        # substrings of THIS query — but the runner doesn't pass them. We
        # instead return a chunk whose text contains every substring used in
        # the bundled corpus, guaranteeing 100% recall.
        records = _load_goldens(None)
        all_subs = " ".join(s for r in records for s in r.get("required_substrings", []))
        return [_hit(all_subs)]

    fake.query.side_effect = _query
    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    rc = run_bench(regress=True, goldens_path=None, config=_cfg())
    assert rc == 0


def test_run_bench_regress_fails_when_recall_below_baseline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mock backend returning empty chunks → recall=0 → exit 1 with reason printed."""
    import supamem.eval.runner as mod

    fake = MagicMock()
    fake.query.return_value = []
    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    rc = run_bench(regress=True, goldens_path=None, config=_cfg())
    assert rc == 1
    out = capsys.readouterr().out
    assert "REGRESSION" in out
    assert "mean_recall_at_5" in out


def test_run_bench_emits_report_with_recall_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import supamem.eval.runner as mod

    fake = MagicMock()
    fake.query.return_value = [_hit("some chunk text that probably does not match anything")]
    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    run_bench(regress=False, goldens_path=None, config=_cfg())
    out = capsys.readouterr().out
    assert "mean recall@5" in out
    assert "total tokens" in out


def test_auto_goldens_no_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """assert_no_saas_llm_env must raise if any SaaS LLM key env var is set."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    with pytest.raises(RuntimeError, match="D-07"):
        assert_no_saas_llm_env()


def test_auto_goldens_passes_when_no_saas_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert_no_saas_llm_env()  # must not raise


def test_derive_required_substrings_is_deterministic() -> None:
    text = "supamem.indexer.run_index uses TunedHybridBackend with k=5"
    out1 = derive_required_substrings(text)
    out2 = derive_required_substrings(text)
    assert out1 == out2
    assert any("supamem" in s for s in out1)


def test_bundled_goldens_constant() -> None:
    assert BUNDLED_GOLDENS.endswith(".jsonl")


# ── v0.1.2: project-tunable regress baselines ────────────────────────────────


def test_resolve_baseline_uses_config_defaults() -> None:
    from supamem.eval.runner import _resolve_baseline

    cfg = ResolvedConfig()
    out = _resolve_baseline(cfg)
    assert out["mean_recall_at_5"] == 0.60
    assert out["total_tokens"] == 4000
    assert out["p95_latency_ms"] == 500


def test_resolve_baseline_config_override() -> None:
    from supamem.eval.runner import _resolve_baseline

    cfg = ResolvedConfig(
        regress_baseline_recall_at_5=0.5,
        regress_baseline_total_tokens=20000,
        regress_baseline_p95_latency_ms=1000,
    )
    out = _resolve_baseline(cfg)
    assert out["mean_recall_at_5"] == 0.5
    assert out["total_tokens"] == 20000
    assert out["p95_latency_ms"] == 1000


def test_resolve_baseline_env_override_beats_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from supamem.eval.runner import _resolve_baseline

    cfg = ResolvedConfig(regress_baseline_total_tokens=10000)
    monkeypatch.setenv("SUPAMEM_BASELINE_TOTAL_TOKENS", "25000")
    monkeypatch.setenv("SUPAMEM_BASELINE_RECALL_AT_5", "0.40")
    out = _resolve_baseline(cfg)
    assert out["total_tokens"] == 25000
    assert out["mean_recall_at_5"] == 0.40


def test_resolve_baseline_malformed_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supamem.eval.runner import _resolve_baseline

    monkeypatch.setenv("SUPAMEM_BASELINE_TOTAL_TOKENS", "not-a-number")
    out = _resolve_baseline(ResolvedConfig())
    assert out["total_tokens"] == 4000  # config default preserved


def test_run_bench_regress_uses_overridden_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Custom baseline: token usage that would breach default passes a higher cap."""
    p = tmp_path / "g.jsonl"
    p.write_text(
        json.dumps({"id": "c1", "query": "x", "required_substrings": ["chunk"]}) + "\n",
        encoding="utf-8",
    )

    big_chunk = "chunk " + ("a" * 20_000)  # 5000+ tokens single hit
    fake = MagicMock()
    fake.query.return_value = [_hit(big_chunk)]

    import supamem.eval.runner as mod

    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    # Default baseline (4000 tokens) would breach
    rc_default = run_bench(regress=True, goldens_path=str(p), config=_cfg())
    assert rc_default == 1
    assert "REGRESSION" in capsys.readouterr().out

    # Project-tunable baseline raises the cap → passes
    cfg_high = _cfg(regress_baseline_total_tokens=100_000)
    rc_override = run_bench(regress=True, goldens_path=str(p), config=cfg_high)
    assert rc_override == 0
    assert "regress: PASS" in capsys.readouterr().out


def test_run_bench_uses_config_goldens_path_when_flag_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cfg.goldens_path is used as fallback when --goldens flag not passed (v0.1.2)."""
    p = tmp_path / "g.jsonl"
    p.write_text(
        json.dumps({"id": "c1", "query": "hello", "required_substrings": ["world"]}) + "\n",
        encoding="utf-8",
    )

    fake = MagicMock()
    fake.query.return_value = [_hit("hello world")]

    import supamem.eval.runner as mod

    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    cfg = _cfg(goldens_path=str(p))
    rc = run_bench(regress=False, goldens_path=None, config=cfg)
    assert rc == 0
    fake.query.assert_called_once()


def test_run_bench_cli_flag_beats_config_goldens_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit --goldens still wins over cfg.goldens_path."""
    cfg_path = tmp_path / "from-config.jsonl"
    cfg_path.write_text(
        json.dumps({"id": "from-config", "query": "ignored", "required_substrings": ["x"]})
        + "\n",
        encoding="utf-8",
    )
    cli_path = tmp_path / "from-flag.jsonl"
    cli_path.write_text(
        json.dumps({"id": "from-flag", "query": "actually-used", "required_substrings": ["x"]})
        + "\n",
        encoding="utf-8",
    )

    seen_queries: list[str] = []

    def query(q: str, **_: Any) -> list[Any]:
        seen_queries.append(q)
        return [_hit("x")]

    fake = MagicMock()
    fake.query.side_effect = query

    import supamem.eval.runner as mod

    monkeypatch.setattr(mod, "_build_backend", lambda cfg: fake)

    cfg = _cfg(goldens_path=str(cfg_path))
    run_bench(regress=False, goldens_path=str(cli_path), config=cfg)
    assert seen_queries == ["actually-used"]


def test_eval_nested_table_loads_baseline_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[supamem.eval] baseline_* keys land in ResolvedConfig fields."""
    from supamem.config import load_config

    (tmp_path / ".supamem").mkdir()
    (tmp_path / ".supamem" / "config.toml").write_text(
        '[supamem]\ncollection = "x"\n[supamem.eval]\n'
        'baseline_recall_at_5 = 0.55\n'
        'baseline_total_tokens = 18000\n'
        'baseline_p95_latency_ms = 750\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("SUPAMEM_CONFIG", raising=False)
    cfg, chain = load_config(cwd=tmp_path)
    assert cfg.regress_baseline_recall_at_5 == 0.55
    assert cfg.regress_baseline_total_tokens == 18000
    assert cfg.regress_baseline_p95_latency_ms == 750
    assert chain.regress_baseline_recall_at_5 == "supamem_toml"
