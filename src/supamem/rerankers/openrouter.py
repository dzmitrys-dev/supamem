"""OpenRouterReranker — remote rerank plugin via the OpenRouter rerank API (v0.4.0a7).

Opt-in per project (``[supamem.reranker] name = "openrouter"``); the default stays the
local mxbai_v2 (privacy policy, ADR-0006). Rationale: the local reranker is fp32-on-CPU
and dominates warm latency (p50 ~24 s); ``cohere/rerank-5-fast`` measured 0.6–0.8 s warm
at ~$0.0001/call (2026-11-18 spike) and the reranker model never loads locally
(daemon RSS ~2.9 GB → ~1 GB).

Contract: same as every ``supamem.reranker`` plugin (``rerankers/__init__.py``
RerankerProtocol). Import-light by construction — stdlib only, no torch/fastembed.

Failure policy (the safety core):

* any transport/HTTP/parse failure retries ONCE, then raises :class:`RemoteRerankerError`;
* ``tuned_hybrid`` catches it with a one-line warn (no traceback — network blips are
  routine) and falls back to the off-branch (hybrid RRF order, no ``rerank_score``,
  so the score floor does not apply);
* a missing key env var fails BEFORE any network attempt;
* the API key value never travels in config/journal/log lines — only the env VAR NAME
  is configured, and the Authorization header exists only for the duration of a call.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from supamem.config import ResolvedConfig
from supamem.retrieval.types import RetrievedChunk

_RETRY_DELAY_S = 0.25  # between the two attempts; tests pin to 0
_RETRYABLE_HTTP = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class RemoteRerankerError(RuntimeError):
    """Terminal remote-rerank failure (after the single retry). Caller must skip, never hang."""


def _post_json(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """One POST; returns the parsed reply dict. Raises urllib errors / ValueError on failure."""
    req = urllib.request.Request(  # noqa: S310 — https base URL from config
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


class OpenRouterReranker:
    """Remote cross-encoder rerank through ``POST {base_url}/rerank`` (OpenRouter, 2026-11)."""

    name = "openrouter"
    # Cohere/qwen families return relevance_score in [0, 1] — unlike mxbai raw logits.
    # tuned_hybrid warns when the hook score floor is unreachable on this scale.
    score_scale_max = 1.0
    # No HuggingFace snapshot to pre-fetch on install/repair (ADR-0006): the model
    # lives behind the remote API. Local plugins leave this unset (True).
    requires_local_model = False

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config
        self.model_id = config.reranker_model_id

    def rerank(
        self, query: str, candidates: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        key_env = self.config.reranker_remote_api_key_env
        key = os.environ.get(key_env, "")
        if not key:
            raise RemoteRerankerError(
                f"remote rerank: env {key_env} is not set — "
                "set it in the environment that spawns the daemon (supamem daemon stop)"
            )

        # Cost cap: only the first reranker_top_n candidates (already RRF-ordered)
        # travel; the reply is capped to the same window.
        top_n = min(self.config.reranker_top_n, len(candidates))
        window = candidates[:top_n]
        url = self.config.reranker_remote_base_url.rstrip("/") + "/rerank"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {
            "model": self.model_id,
            "query": query,
            "documents": [c.text for c in window],
            "top_n": top_n,
        }
        timeout_s = self.config.reranker_remote_timeout_ms / 1000.0

        t0 = time.perf_counter()
        reply: dict[str, Any] | None = None
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                reply = _post_json(url, headers, body, timeout_s)
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in _RETRYABLE_HTTP:
                    break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_exc = exc
            if attempt == 1:
                time.sleep(_RETRY_DELAY_S)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if reply is None:
            self._bump("remote_failed", tokens=top_n, latency_ms=elapsed_ms)
            raise RemoteRerankerError(
                f"remote rerank failed after retry: {type(last_exc).__name__}: {last_exc}"
            )

        results = reply.get("results")
        if not isinstance(results, list) or not results:
            self._bump("remote_failed", tokens=top_n, latency_ms=elapsed_ms)
            raise RemoteRerankerError("remote rerank: malformed reply (no results list)")

        out: list[RetrievedChunk] = []
        for r in results:
            if not isinstance(r, dict):
                self._bump("remote_failed", tokens=top_n, latency_ms=elapsed_ms)
                raise RemoteRerankerError("remote rerank: malformed results entry")
            idx = r.get("index")
            score = r.get("relevance_score")
            if not isinstance(idx, int) or not (0 <= idx < len(window)) or score is None:
                self._bump("remote_failed", tokens=top_n, latency_ms=elapsed_ms)
                raise RemoteRerankerError(
                    f"remote rerank: index {idx!r} out of range or score missing"
                )
            src = window[idx]
            s = float(score)
            # frozen RetrievedChunk: always model_copy(update=...) (RESEARCH Pitfall 4).
            out.append(src.model_copy(update={"score": s, "rerank_score": s}))
        self._bump("rerank_latency_ms", tokens=top_n, latency_ms=elapsed_ms)
        return out

    @staticmethod
    def _bump(source: str, *, tokens: int, latency_ms: float) -> None:
        try:
            from supamem.stats.counter import bump  # noqa: PLC0415

            bump(kind="rerank", source=source, tokens=tokens, latency_ms=latency_ms)
        except Exception:  # noqa: BLE001 — non-essential telemetry
            pass
