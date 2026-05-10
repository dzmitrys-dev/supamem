"""tuned_hybrid_hyde — HyDE query expansion wrapper around TunedHybridBackend.

Phase 17, Track B (Req-03). Closes the
``decision_rationale.supamem_only.recall_at_1 = 0.0000`` collapse named in
ADR-0002 §8 by rewriting each user query as a K=1 hypothetical
decision-rationale doc via the existing localhost Ollama ``llama3.2:3b``
adapter (D-07 invariant — no ``OPENAI_API_KEY`` introduced).

Composition (NOT subclassing): ``self._inner = TunedHybridBackend(config)``
keeps ``tuned_hybrid.py`` byte-identical for opt-out users (T-17-04). The
plugin is discovered lazily via the ``supamem.retrieval`` entry-point
group, so default users never import this module.

D-HYDE-01: prompt template is locked verbatim — tests pin string equality
(``tests/test_tuned_hybrid_hyde.py::test_hyde_prompt_template_verbatim``).

D-HYDE-03 (fallback): Ollama unreachable / timeout / any other failure →
return ``None`` from ``_generate_hyde`` and route the ORIGINAL query
through the inner backend, with a single ``err_console`` warning. Never
raise into the retrieval hot loop.

D-HYDE-04 (T-17-02 mitigation): every POST sets ``keep_alive=-1`` to keep
``llama3.2:3b`` warm in VRAM between phases (Ollama default unloads after
5 minutes — RESEARCH Pitfall 2). 600 ms timeout + 1 retry leaves headroom
under the eval suite's 5000 ms budget; doctor warm-pool surface (17-D)
adds the diagnostic complement.

The localhost-only guard is inherited from
``supamem.eval.judge._resolve_ollama_host`` — sibling-module reuse of the
``_``-prefixed helper is intentional (do NOT widen access).
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from supamem.config import ResolvedConfig
from supamem.console import err_console
from supamem.eval.judge import _resolve_ollama_host  # localhost guard, D-JUDGE-03
from supamem.retrieval.filters import WhereDict
from supamem.retrieval.tuned_hybrid import TunedHybridBackend
from supamem.retrieval.types import RetrievedChunk
from supamem.stats.counter import bump

# D-HYDE-01 — LOCKED VERBATIM. Tests assert string equality with this exact
# byte sequence (trailing newline after "Rationale:" included). Do NOT
# paraphrase, do NOT ``.strip()``.
HYDE_PROMPT_TEMPLATE = """\
Write a 3-5 sentence decision rationale that would answer this question, as if extracted from an ADR or code comment. Be specific and technical.

Question: {query}

Rationale:
"""

# D-HYDE-04 — Claude's-Discretion knobs (CONTEXT G2): 600 ms p95 budget
# leaves ~200 ms for retrieval inside the 800 ms HyDE-on suite cap; one
# retry covers transient Ollama hiccups without busting the budget.
HYDE_MODEL = "llama3.2:3b"
HYDE_TIMEOUT_S = 0.6
HYDE_RETRIES = 1


class TunedHybridHyDEBackend:
    """HyDE query-expansion wrapper composing TunedHybridBackend.

    On ``query(text)``: generate a hypothetical decision-rationale doc via
    Ollama (D-HYDE-01 prompt), then forward the hypo doc as the retrieval
    query to ``self._inner``. On any Ollama failure, forward the ORIGINAL
    query (D-HYDE-03 fallback) and log a single warning to ``err_console``.

    Composition (NOT subclassing) — see module docstring + T-17-04.
    """

    name = "tuned_hybrid_hyde"

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config
        # Composition — keeps tuned_hybrid.py byte-identical for opt-out
        # users (T-17-04). Mirrors FilteredDenseBackend.__init__.
        self._inner = TunedHybridBackend(config=config)
        # Resolve + validate Ollama host at construction time so a
        # misconfigured ``OLLAMA_HOST=non.localhost`` fails fast with
        # SystemExit(2) before any user query is dispatched (D-07 invariant).
        self._ollama_url = _resolve_ollama_host() + "/api/generate"

    def query(
        self,
        text: str,
        k: int = 5,
        *,
        where: Optional[WhereDict] = None,
    ) -> list[RetrievedChunk]:
        t0 = time.perf_counter()
        hypo = self._generate_hyde(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # Latency telemetry — Welford counter + process-local ring buffer.
        # Failures swallowed by ``bump`` itself (fail-soft contract); this
        # call MUST NOT raise into the retrieval hot loop.
        try:
            bump("hyde", "hyde_latency_ms", 0, elapsed_ms)
        except Exception:  # noqa: BLE001 — observability never blocks retrieval
            pass

        if hypo is None:
            # D-HYDE-03 fallback — forward the ORIGINAL query, NOT the hypo.
            return self._inner.query(text, k, where=where)
        return self._inner.query(hypo, k, where=where)

    def _generate_hyde(self, query: str) -> Optional[str]:
        """POST to Ollama and return the generated hypothetical doc.

        Returns ``None`` on any failure (timeout, connection error, empty
        response, parse error). Caller treats ``None`` as the fallback
        signal per D-HYDE-03.
        """
        prompt = HYDE_PROMPT_TEMPLATE.format(query=query)
        payload_obj: dict[str, Any] = {
            "model": HYDE_MODEL,
            "prompt": prompt,
            "stream": False,
            "keep_alive": -1,  # D-HYDE-04 — T-17-02 mitigation, every POST.
        }
        payload = json.dumps(payload_obj).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — localhost-only, validated
            self._ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        last_exc: Optional[BaseException] = None
        for attempt in range(HYDE_RETRIES + 1):
            try:
                with urllib.request.urlopen(  # noqa: S310 — localhost-only
                    req, timeout=HYDE_TIMEOUT_S
                ) as resp:
                    body = resp.read()
                parsed = json.loads(body.decode("utf-8"))
                text = (parsed.get("response") or "").strip()
                if not text:
                    last_exc = RuntimeError("ollama empty response")
                    continue
                return text
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_exc = exc
                if attempt < HYDE_RETRIES:
                    continue
                break
            except Exception as exc:  # noqa: BLE001 — surface, never raise
                last_exc = exc
                break

        # D-HYDE-03 — single warning, then fall back. Never raise.
        reason = type(last_exc).__name__ if last_exc is not None else "unknown"
        err_console.print(
            f"[supamem.warn]tuned_hybrid_hyde: ollama {reason}, "
            f"falling back to tuned_hybrid (original query)"
        )
        return None

    @classmethod
    def kind(cls) -> str:
        return "tuned_hybrid_hyde"


__all__ = ["TunedHybridHyDEBackend", "HYDE_PROMPT_TEMPLATE"]
