"""Tests for ``supamem.retrieval.tuned_hybrid_hyde`` (Phase 17-C, Req-03).

Locks the HyDE retrieval wrapper contract:

- D-HYDE-01: prompt template is locked verbatim.
- D-HYDE-02: HyDE applies to every query in v1 (no axis gate).
- D-HYDE-03: Ollama unreachable / timeout → fallback to ``tuned_hybrid``
  with the ORIGINAL query (not the hypo doc), with an ``err_console`` warning.
- D-HYDE-04: every Ollama POST sets ``keep_alive=-1``.
- D-07 invariant: localhost-only Ollama guard inherited from
  ``supamem.eval.judge._resolve_ollama_host`` (raises ``SystemExit(2)`` on
  non-localhost host).
- T-17-04: composition over inheritance — ``TunedHybridHyDEBackend`` does
  NOT subclass ``TunedHybridBackend``; it composes via ``self._inner``.
- Latency telemetry: ``stats.counter.bump("hyde", ...)`` records elapsed_ms.
"""
from __future__ import annotations

import importlib.metadata
import json
import socket
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest


# D-HYDE-01: pinned verbatim template. Tests assert string equality with the
# production constant — NOT paraphrase, NOT ``.strip()``.
EXPECTED_HYDE_PROMPT_TEMPLATE = """\
Write a 3-5 sentence decision rationale that would answer this question, as if extracted from an ADR or code comment. Be specific and technical.

Question: {query}

Rationale:
"""


def _cfg(**overrides: Any):
    """Minimal ResolvedConfig — mirrors test_tuned_hybrid.py:_cfg."""
    from supamem.config import ResolvedConfig

    return ResolvedConfig(
        qdrant_url="http://localhost:6333",
        collection=overrides.pop("collection", "test_hyde_collection"),
        **overrides,
    )


def _stub_inner(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``TunedHybridBackend`` with a MagicMock so we can introspect
    what query text was forwarded by the HyDE wrapper, without standing up
    Qdrant or fastembed.
    """
    inner_class = MagicMock(name="TunedHybridBackend")
    inner_instance = MagicMock(name="TunedHybridBackend_instance")
    inner_instance.query.return_value = []
    inner_class.return_value = inner_instance
    monkeypatch.setattr(
        "supamem.retrieval.tuned_hybrid_hyde.TunedHybridBackend",
        inner_class,
    )
    return inner_instance


def _fake_urlopen_response(payload: dict) -> Any:
    """Build a context-manager mock matching ``urllib.request.urlopen(...)``."""
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    cm.__exit__.return_value = False
    return cm


# ---------------------------------------------------------------------------
# 1. Plugin entry-point registration
# ---------------------------------------------------------------------------
def test_plugin_registered() -> None:
    """``tuned_hybrid_hyde`` is discoverable via the supamem.retrieval EP group."""
    eps = importlib.metadata.entry_points(group="supamem.retrieval")
    names = {ep.name for ep in eps}
    assert "tuned_hybrid_hyde" in names, (
        f"tuned_hybrid_hyde missing from supamem.retrieval entry-points; got {names!r}"
    )


# ---------------------------------------------------------------------------
# 2. D-HYDE-01: prompt template locked verbatim
# ---------------------------------------------------------------------------
def test_hyde_prompt_template_verbatim() -> None:
    """``HYDE_PROMPT_TEMPLATE`` matches D-HYDE-01 EXACTLY (string-equality)."""
    from supamem.retrieval.tuned_hybrid_hyde import HYDE_PROMPT_TEMPLATE

    assert HYDE_PROMPT_TEMPLATE == EXPECTED_HYDE_PROMPT_TEMPLATE


# ---------------------------------------------------------------------------
# 3. D-HYDE-04: every Ollama POST has keep_alive=-1
# ---------------------------------------------------------------------------
def test_keep_alive_minus_one_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every HyDE POST body MUST contain ``"keep_alive": -1`` (T-17-02 mitigation)."""
    _stub_inner(monkeypatch)

    captured: dict[str, Any] = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["body"] = req.data
        return _fake_urlopen_response({"response": "hypo doc"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    backend = TunedHybridHyDEBackend(config=_cfg())
    backend.query("what is the decision rationale for X?", k=5)

    assert "body" in captured, "urlopen was not called"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["keep_alive"] == -1, f"expected keep_alive=-1, got {body.get('keep_alive')!r}"


# ---------------------------------------------------------------------------
# 4. D-HYDE-02: HyDE-rewritten query is forwarded to inner on success
# ---------------------------------------------------------------------------
def test_query_uses_hypothetical_doc_when_ollama_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _stub_inner(monkeypatch)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return _fake_urlopen_response({"response": "hypo decision rationale text"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    backend = TunedHybridHyDEBackend(config=_cfg())
    backend.query("original question", k=5)

    assert inner.query.called, "inner backend was not invoked"
    forwarded_text = inner.query.call_args[0][0]
    assert forwarded_text == "hypo decision rationale text", (
        f"expected HyDE-rewritten text forwarded to inner, got {forwarded_text!r}"
    )


# ---------------------------------------------------------------------------
# 5. D-HYDE-03: fallback on URLError
# ---------------------------------------------------------------------------
def test_fallback_on_ollama_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inner = _stub_inner(monkeypatch)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    backend = TunedHybridHyDEBackend(config=_cfg())
    backend.query("original question", k=5)

    assert inner.query.called, "inner backend should have been called via fallback"
    forwarded_text = inner.query.call_args[0][0]
    assert forwarded_text == "original question", (
        f"fallback MUST forward ORIGINAL query, got {forwarded_text!r}"
    )
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    assert "ollama" in combined and "fallback" in combined, (
        f"expected err_console warning mentioning 'ollama' + 'fallback'; got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# 6. D-HYDE-03: fallback on timeout
# ---------------------------------------------------------------------------
def test_fallback_on_ollama_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = _stub_inner(monkeypatch)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        raise socket.timeout("read timeout")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    backend = TunedHybridHyDEBackend(config=_cfg())
    backend.query("the original q", k=3)

    assert inner.query.called
    forwarded_text = inner.query.call_args[0][0]
    assert forwarded_text == "the original q"


# ---------------------------------------------------------------------------
# 7. D-07 invariant: localhost-only guard inherited from _resolve_ollama_host
# ---------------------------------------------------------------------------
def test_localhost_only_guard_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_inner(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://evil.example.com:11434")

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    with pytest.raises(SystemExit) as exc_info:
        TunedHybridHyDEBackend(config=_cfg())
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 8. Latency telemetry recorded on success
# ---------------------------------------------------------------------------
def test_latency_telemetry_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_inner(monkeypatch)

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        return _fake_urlopen_response({"response": "hypo text"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    bump_calls: list[tuple] = []

    def fake_bump(*args, **kwargs):
        bump_calls.append((args, kwargs))

    monkeypatch.setattr("supamem.retrieval.tuned_hybrid_hyde.bump", fake_bump)

    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    backend = TunedHybridHyDEBackend(config=_cfg())
    backend.query("q", k=5)

    assert bump_calls, "expected stats.counter.bump to be called at least once"
    # First positional arg is "hyde"; latency_ms is the 4th positional.
    args, _kwargs = bump_calls[0]
    assert args[0] == "hyde"
    elapsed_ms = args[3]
    assert elapsed_ms >= 0, f"elapsed_ms must be non-negative, got {elapsed_ms!r}"


# ---------------------------------------------------------------------------
# 9. Class attribute / plugin name
# ---------------------------------------------------------------------------
def test_class_attribute_name() -> None:
    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    assert TunedHybridHyDEBackend.name == "tuned_hybrid_hyde"


# ---------------------------------------------------------------------------
# 10. T-17-04: composition, NOT subclassing
# ---------------------------------------------------------------------------
def test_composition_not_subclassing() -> None:
    """``TunedHybridHyDEBackend`` MUST compose, not subclass — keeps
    ``tuned_hybrid.py`` byte-identical for non-HyDE users (T-17-04)."""
    from supamem.retrieval.tuned_hybrid import TunedHybridBackend
    from supamem.retrieval.tuned_hybrid_hyde import TunedHybridHyDEBackend

    assert TunedHybridBackend not in TunedHybridHyDEBackend.__mro__, (
        "TunedHybridHyDEBackend must NOT inherit from TunedHybridBackend "
        "(use composition via self._inner)"
    )
