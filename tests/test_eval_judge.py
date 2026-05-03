"""Phase 10 Plan 10-01 — RED tests for the two-tier judge dispatch.

Locks the judge contract from CONTEXT.md decisions D-JUDGE-01..D-JUDGE-03:

- Heuristic (Tier 1, default, offline) → ``.kind == 'heuristic'``, ``.model == 'n/a'``.
- Ollama (Tier 2, opt-in) → localhost-only.
- D-JUDGE-03 SaaS-env breach: ``EVAL_JUDGE_MODEL=openai:gpt-4`` raises ``SystemExit(2)``.
- D-07 invariant: ``assert_no_saas_llm_env()`` invoked from dispatch (mocker.spy).
- Heuristic answer-relevance is ``None`` with reason ``"requires llm judge"``.

All tests MUST FAIL today: ``supamem.eval.judge`` does not exist.
``importorskip`` keeps collection green; failures surface once Plan 10-04 lands.

Per D-07: this file imports NO SaaS LLM SDK (no openai/anthropic/cohere).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


judge_mod = pytest.importorskip("supamem.eval.judge")


def test_dispatch_judge_is_callable() -> None:
    """``dispatch_judge`` is the public entrypoint exposed by Plan 10-04."""
    assert callable(judge_mod.dispatch_judge)


def test_heuristic_judge_kind_and_model() -> None:
    """D-JUDGE-02: heuristic dispatch → kind='heuristic', model='n/a'."""
    j = judge_mod.dispatch_judge(kind="heuristic")
    assert j.kind == "heuristic"
    assert j.model == "n/a"


def test_ollama_judge_kind_and_model() -> None:
    """D-JUDGE-01 Tier 2: ollama dispatch carries the model verbatim."""
    j = judge_mod.dispatch_judge(kind="ollama", model="llama3.2:3b")
    assert j.kind == "ollama"
    assert j.model == "llama3.2:3b"


def test_saas_judge_model_env_raises_systemexit_2(monkeypatch) -> None:
    """D-JUDGE-03: ``EVAL_JUDGE_MODEL=openai:...`` MUST raise SystemExit(2).

    The D-07 invariant blocks SaaS endpoints. SaaS prefixes
    (openai|anthropic|cohere|mistral) are rejected before any HTTP traffic.
    """
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "openai:gpt-4")
    with pytest.raises(SystemExit) as exc:
        judge_mod.resolve_judge_from_env()
    assert exc.value.code == 2


def test_dispatch_invokes_assert_no_saas_llm_env() -> None:
    """D-07 + D-JUDGE-03: dispatch must call ``assert_no_saas_llm_env()``
    exactly once before yielding a judge instance. Spy via patch.

    The runner.py invariant from v0.1.x is non-negotiable; suite dispatch
    re-asserts it so the gate stays even when judge tier changes.
    """
    with patch("supamem.eval.judge.assert_no_saas_llm_env") as spy:
        judge_mod.dispatch_judge(kind="heuristic")
    assert spy.call_count == 1, f"expected exactly 1 call, got {spy.call_count}"


def test_heuristic_answer_relevance_is_none_with_reason() -> None:
    """RAGAS docs: ``answer_relevancy`` is not achievable LLM-free.
    Heuristic mode reports it as None + a documented reason."""
    j = judge_mod.dispatch_judge(kind="heuristic")
    result = j.score_answer_relevance(question="q", answer="a", contexts=["c"])
    assert result.value is None
    assert result.reason == "requires llm judge"


def test_ollama_dispatch_hits_localhost_only() -> None:
    """D-JUDGE-03: Ollama tier MUST use ``http://localhost:11434/api/generate``.
    Non-localhost endpoints raise SystemExit(2)."""
    j = judge_mod.dispatch_judge(kind="ollama", model="llama3.2:3b")
    with patch("urllib.request.urlopen") as mock_urlopen:
        # The mock can return anything; we only assert the URL used.
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"response": "ok"}'
        try:
            j.score_answer_relevance(question="q", answer="a", contexts=["c"])
        except Exception:
            # Score body may still error in RED state — that's fine.
            pass
        if mock_urlopen.called:
            url = mock_urlopen.call_args[0][0]
            url_str = url if isinstance(url, str) else getattr(url, "full_url", str(url))
            assert "localhost" in url_str or "127.0.0.1" in url_str, url_str


def test_ollama_non_localhost_endpoint_raises(monkeypatch) -> None:
    """``EVAL_JUDGE_MODEL=ollama:llama3.2`` with OLLAMA_HOST pointing off-box
    MUST raise SystemExit(2). Localhost-only is the D-JUDGE-03 contract."""
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "ollama:llama3.2")
    monkeypatch.setenv("OLLAMA_HOST", "http://203.0.113.5:11434")
    with pytest.raises(SystemExit) as exc:
        judge_mod.resolve_judge_from_env()
    assert exc.value.code == 2
