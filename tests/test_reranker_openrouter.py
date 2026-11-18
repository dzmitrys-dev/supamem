"""OpenRouterReranker (v0.4.0a7) — remote rerank plugin, offline tests only.

Every test monkeypatches the plugin's ``_post_json`` transport: CI never
touches the network, never needs OPENROUTER_API_KEY. Locks the plan
(buzzing-conjuring-eclipse §Changes #1..#4):

- happy path: 0–1 ``relevance_score`` REPLACES score (mxbai pattern),
  output sorted by relevance, ``rerank_score`` set;
- ``reranker_top_n`` caps BOTH documents sent and chunks returned (cost);
- missing key env fails BEFORE any network attempt;
- one retry on 429/5xx/timeout, then ``RemoteRerankerError`` (the daemon's
  off-branch handles the rest — generic-exception off-branch is already
  locked by test_tuned_hybrid_rerank.py);
- ``rerank/remote_failed`` + ``rerank/rerank_latency_ms`` counters;
- config roundtrip through the ``[supamem.reranker]`` TOML table and the
  load-time entry-point gate accepting ``openrouter``;
- the score-scale footgun: ``score_scale_max = 1.0`` and the
  once-per-process floor-unreachable warning.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from supamem.config import ResolvedConfig


def _cfg(**over: Any) -> ResolvedConfig:
    base = {"qdrant_url": "http://localhost:6333", "collection": "t_or"}
    base.update(over)
    return ResolvedConfig(**base)


def _chunks(n: int = 3) -> list[Any]:
    from supamem.retrieval.types import RetrievedChunk

    return [RetrievedChunk(id=str(i), text=f"doc {i}", score=0.01 * i) for i in range(n)]


class _Reply:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float):
        return {"model": body["model"], "results": self._results}


# ── registration / config ────────────────────────────────────────────────────


def test_registered_entry_point():
    from supamem.rerankers import load_reranker

    cfg = _cfg()
    r = load_reranker("openrouter", cfg)
    assert r is not None and r.name == "openrouter"
    assert r.model_id == cfg.reranker_model_id


def test_config_toml_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from supamem.config import load_config

    (tmp_path / ".supamem").mkdir()
    (tmp_path / ".supamem" / "config.toml").write_text(
        "[supamem.reranker]\n"
        'name = "openrouter"\n'
        'model_id = "cohere/rerank-5-fast"\n'
        'remote_base_url = "https://example.test/v1"\n'
        'remote_api_key_env = "MY_OR_KEY"\n'
        "remote_timeout_ms = 5000\n"
    )
    monkeypatch.setenv("MY_OR_KEY", "k")
    cfg, chain = load_config(tmp_path)
    assert cfg.reranker_name == "openrouter"
    assert cfg.reranker_model_id == "cohere/rerank-5-fast"
    assert cfg.reranker_remote_base_url == "https://example.test/v1"
    assert cfg.reranker_remote_api_key_env == "MY_OR_KEY"
    assert cfg.reranker_remote_timeout_ms == 5000
    assert chain.reranker_name == "supamem_toml"


def test_remote_timeout_gate_rejects_nonpositive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """remote_timeout_ms <= 0 fails closed at load (SystemExit 2), like the daemon gate."""
    from supamem.config import load_config

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    (tmp_path / ".supamem").mkdir()
    (tmp_path / ".supamem" / "config.toml").write_text(
        '[supamem.reranker]\nname = "openrouter"\nremote_timeout_ms = 0\n'
    )
    with pytest.raises(SystemExit) as ei:
        load_config(tmp_path)
    assert ei.value.code == 2


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_replaces_score_and_sorts(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = _cfg()
    r = OpenRouterReranker(config=cfg)
    seen: list[dict[str, Any]] = []

    def fake_post(url, headers, body, timeout):
        seen.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        # deliberately NOT score-sorted: doc1 most relevant
        return {
            "model": body["model"],
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.4},
                {"index": 0, "relevance_score": 0.1},
            ],
        }

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", fake_post)
    out = r.rerank("q", _chunks(3))
    assert [c.id for c in out] == ["1", "2", "0"]
    assert out[0].score == pytest.approx(0.9)
    assert out[0].rerank_score == pytest.approx(0.9)
    body = seen[0]["body"]
    assert body["query"] == "q"
    assert body["documents"] == ["doc 0", "doc 1", "doc 2"]
    assert body["model"] == "mixedbread-ai/mxbai-rerank-base-v2"  # from config default
    assert "Bearer k" in seen[0]["headers"]["Authorization"]


def test_top_n_caps_documents_sent_and_chunks_returned(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = _cfg(reranker_top_n=3)
    r = OpenRouterReranker(config=cfg)
    seen: list[dict[str, Any]] = []

    def fake_post(url, headers, body, timeout):
        seen.append(body)
        return {"results": [{"index": i, "relevance_score": 0.5} for i in range(len(body["documents"]))]}

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", fake_post)
    out = r.rerank("q", _chunks(5))
    assert len(seen[0]["documents"]) == 3  # cost: only top_n docs travel
    assert len(out) == 3


def test_empty_candidates_short_circuit(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker

    r = OpenRouterReranker(config=_cfg())
    assert r.rerank("q", []) == []


# ── failure policy ───────────────────────────────────────────────────────────


def test_missing_key_raises_before_network(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls: list[Any] = []

    def spy_post(*a, **k):  # pragma: no cover — must never run
        calls.append(a)

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", spy_post)
    r = OpenRouterReranker(config=_cfg())
    with pytest.raises(RemoteRerankerError, match="OPENROUTER_API_KEY"):
        r.rerank("q", _chunks())
    assert calls == []


def _http_error(code: int) -> Exception:
    import urllib.error

    return urllib.error.HTTPError("https://x.test/rerank", code, "boom", None, None)


def test_server_error_retries_once_then_raises(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("supamem.rerankers.openrouter._RETRY_DELAY_S", 0)
    calls: list[int] = []

    def flaky(url, headers, body, timeout):
        calls.append(1)
        raise _http_error(500)

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", flaky)
    r = OpenRouterReranker(config=_cfg())
    with pytest.raises(RemoteRerankerError):
        r.rerank("q", _chunks())
    assert len(calls) == 2  # exactly one retry


def test_429_retry_then_success(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("supamem.rerankers.openrouter._RETRY_DELAY_S", 0)
    state = {"n": 0}

    def flaky(url, headers, body, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise _http_error(429)
        return {"results": [{"index": 0, "relevance_score": 0.7}]}

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", flaky)
    r = OpenRouterReranker(config=_cfg())
    out = r.rerank("q", _chunks())
    assert out and out[0].rerank_score == pytest.approx(0.7)


def test_non_retryable_4xx_fails_fast(monkeypatch: pytest.MonkeyPatch):
    """401 (bad key) must NOT retry — the key won't heal in 250 ms."""
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("supamem.rerankers.openrouter._RETRY_DELAY_S", 0)
    calls: list[int] = []

    def denied(url, headers, body, timeout):
        calls.append(1)
        raise _http_error(401)

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", denied)
    r = OpenRouterReranker(config=_cfg())
    with pytest.raises(RemoteRerankerError, match="401|HTTP Error"):
        r.rerank("q", _chunks())
    assert len(calls) == 1


def test_malformed_reply_raises(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        "supamem.rerankers.openrouter._post_json",
        lambda *a, **k: {"results": "garbage"},
    )
    r = OpenRouterReranker(config=_cfg())
    with pytest.raises(RemoteRerankerError, match="malformed"):
        r.rerank("q", _chunks())


def test_out_of_range_index_raises(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        "supamem.rerankers.openrouter._post_json",
        lambda *a, **k: {"results": [{"index": 99, "relevance_score": 0.5}]},
    )
    r = OpenRouterReranker(config=_cfg())
    with pytest.raises(RemoteRerankerError):
        r.rerank("q", _chunks())


# ── counters ─────────────────────────────────────────────────────────────────


def test_counters_on_success_and_failure(monkeypatch: pytest.MonkeyPatch):
    from supamem.rerankers.openrouter import OpenRouterReranker, RemoteRerankerError

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr("supamem.rerankers.openrouter._RETRY_DELAY_S", 0)
    bumps: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "supamem.stats.counter.bump",
        lambda **kw: bumps.append(kw),
    )
    ok = {"results": [{"index": 0, "relevance_score": 0.5}]}
    r = OpenRouterReranker(config=_cfg())
    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", lambda *a, **k: ok)
    r.rerank("q", _chunks())
    assert {"kind": "rerank", "source": "rerank_latency_ms"} in [
        {k: v for k, v in b.items() if k in ("kind", "source")} for b in bumps
    ]

    bumps.clear()

    def dead(url, headers, body, timeout):
        raise _http_error(503)

    monkeypatch.setattr("supamem.rerankers.openrouter._post_json", dead)
    with pytest.raises(RemoteRerankerError):
        r.rerank("q", _chunks())
    assert {"kind": "rerank", "source": "remote_failed"} in [
        {k: v for k, v in b.items() if k in ("kind", "source")} for b in bumps
    ]


# ── score-scale footgun ──────────────────────────────────────────────────────


def test_score_scale_max_declared():
    from supamem.rerankers.openrouter import OpenRouterReranker

    assert OpenRouterReranker.score_scale_max == 1.0


def _capture_err_console(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record err_console.print calls (Rich caches the stderr handle, so capsys
    cannot see it once another test has already printed)."""
    import supamem.console as sc

    lines: list[str] = []
    monkeypatch.setattr(
        sc.err_console, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))
    )
    return lines


def test_floor_unreachable_warn_fires_once(monkeypatch: pytest.MonkeyPatch):
    from supamem.retrieval import tuned_hybrid as th

    class _FakeR:
        name = "openrouter"
        score_scale_max = 1.0

    lines = _capture_err_console(monkeypatch)
    monkeypatch.setattr(th, "_FLOOR_WARNED", False)
    cfg = _cfg(hook_min_score=2.5)
    th._warn_floor_unreachable(cfg, _FakeR())
    th._warn_floor_unreachable(cfg, _FakeR())
    assert sum("hook.min_score" in ln for ln in lines) == 1  # once per process

    # reachable floor on the 0–1 scale → no warn
    monkeypatch.setattr(th, "_FLOOR_WARNED", False)
    n = len(lines)
    th._warn_floor_unreachable(_cfg(hook_min_score=0.5), _FakeR())
    assert len(lines) == n


def test_local_reranker_scale_is_unbounded():
    from supamem.rerankers.mxbai_v2 import MxbaiV2Reranker

    assert getattr(MxbaiV2Reranker, "score_scale_max", None) is None


def test_remote_failure_warn_is_one_line(monkeypatch: pytest.MonkeyPatch):
    """RemoteRerankerError prints a short warn WITHOUT a traceback (network blips are routine)."""
    from supamem.rerankers.openrouter import RemoteRerankerError
    from supamem.retrieval import tuned_hybrid as th

    lines = _capture_err_console(monkeypatch)
    th._warn_remote_failure("openrouter", RemoteRerankerError("no route"))
    assert len(lines) == 1
    assert "openrouter" in lines[0] and "no route" in lines[0]
    assert "Traceback" not in lines[0]


def test_install_skips_local_prefetch_for_remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """install/repair must NOT HuggingFace-fetch a remote provider's model_id (there is
    no snapshot — 3 doomed retries + a misleading warning on every repair otherwise)."""
    import supamem.install as inst

    class _Remote:
        name = "openrouter"
        requires_local_model = False

    prepared: list[str] = []

    def fake_prepare(model_id, **k):  # pragma: no cover — must never run
        prepared.append(model_id)

    monkeypatch.setattr("supamem.rerankers.prepare", fake_prepare)
    monkeypatch.setattr(
        "supamem.rerankers.load_reranker", lambda name, cfg: _Remote()
    )
    monkeypatch.setattr(
        "supamem.config.load_config",
        lambda *a, **k: (_cfg(reranker_name="openrouter"), None),
    )
    inst._maybe_prepare_models(skip_models=False)
    assert prepared == []
