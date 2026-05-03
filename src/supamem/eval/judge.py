"""Two-tier judge dispatch — Phase 10 D-JUDGE-01..D-JUDGE-03.

Tier 1 (heuristic, default, offline, zero extra deps): non-LLM RAGAS
variants + fastembed-minilm embedding-cosine for an ``answer_relevance``
proxy. Reuses the already-shipped fastembed dependency. The plain
``answer_relevancy`` metric (RAGAS docs) cannot be computed LLM-free, so
heuristic mode reports ``answer_relevance = None`` with reason
``"requires llm judge"``.

Tier 2 (ollama, opt-in): localhost-only HTTP POST to
``http://localhost:11434/api/generate``. Model spec format:
``ollama:<model>`` (e.g., ``ollama:llama3.2:3b``).

D-07 invariant (the load-bearing rule for this whole subsystem):

- ``dispatch_judge`` calls ``assert_no_saas_llm_env`` BEFORE constructing
  any judge. SaaS env vars set → RuntimeError → re-raised as
  ``SystemExit(2)`` with an err_console breach line.
- ``resolve_judge_from_env`` (env-driven entrypoint) rejects
  ``EVAL_JUDGE_MODEL`` with SaaS-prefix (openai|anthropic|cohere|mistral|
  together|openrouter) → ``SystemExit(2)``.
- Ollama judge validates the resolved host is ``localhost`` or
  ``127.0.0.1``; any other host → ``SystemExit(2)``.

HTTP transport uses stdlib ``urllib.request`` to avoid pulling httpx
into core. Every diagnostic goes through ``supamem.console.err_console``.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Literal

from supamem.console import err_console
from supamem.eval.auto_goldens import assert_no_saas_llm_env

JudgeKind = Literal["heuristic", "ollama"]

# SaaS prefixes blocked by D-JUDGE-03. The leading colon and `://` forms
# are both rejected so neither ``openai:gpt-4`` nor ``openai://gpt-4``
# slip through.
_SAAS_PREFIXES: tuple[str, ...] = (
    "openai",
    "anthropic",
    "cohere",
    "mistral",
    "together",
    "openrouter",
)

_OLLAMA_DEFAULT_HOST = "http://localhost:11434"
_OLLAMA_LOCALHOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class JudgeResult:
    """One scored metric: value (or None) + human reason for the value."""

    value: float | None
    reason: str = ""


@dataclass(frozen=True)
class Judge:
    """A configured judge instance — kind + model are recorded verbatim
    in the report envelope (D-JUDGE-02)."""

    kind: JudgeKind
    model: str

    def score_answer_relevance(
        self,
        *,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> JudgeResult:
        """Score answer-relevance for one (question, answer, contexts) triple.

        Heuristic tier: returns ``None`` with reason ``"requires llm judge"``
        — RAGAS docs confirm ``answer_relevancy`` is not LLM-free achievable.
        Ollama tier: posts a minimal scoring prompt to the localhost
        Ollama instance; returns the parsed score or ``None`` on error.
        """
        if self.kind == "heuristic":
            return JudgeResult(value=None, reason="requires llm judge")
        if self.kind == "ollama":
            return _ollama_score_answer_relevance(
                model=self.model,
                question=question,
                answer=answer,
                contexts=contexts,
            )
        # Defensive — JudgeKind Literal makes this unreachable in typed code.
        raise ValueError(f"unknown judge kind: {self.kind!r}")


def _reject_saas(spec: str) -> None:
    """Raise SystemExit(2) if ``spec`` starts with a known SaaS prefix.

    Accepts both ``openai:gpt-4`` and ``openai://gpt-4`` forms — the
    SaaS-endpoint refusal is independent of URL grammar.
    """
    spec_lc = spec.strip().lower()
    for prefix in _SAAS_PREFIXES:
        if spec_lc.startswith(prefix + ":") or spec_lc.startswith(prefix + "://"):
            err_console.print(
                "[supamem.err]supamem eval: SaaS judge endpoint refused "
                "(D-07 invariant); use ollama:<model> for localhost only."
                "[/supamem.err]"
            )
            raise SystemExit(2)


def _resolve_ollama_host() -> str:
    """Resolve the Ollama base URL from ``OLLAMA_HOST`` env or default.

    Raises ``SystemExit(2)`` if the resolved host is not localhost or
    127.0.0.1 (D-JUDGE-03: Ollama tier MUST be localhost-only).
    """
    raw = os.environ.get("OLLAMA_HOST", "").strip() or _OLLAMA_DEFAULT_HOST
    # Ollama accepts both ``host:port`` and full ``http://host:port``.
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    if host not in _OLLAMA_LOCALHOSTS:
        err_console.print(
            f"[supamem.err]supamem eval: Ollama host {host!r} is not "
            "localhost (D-JUDGE-03 invariant); refusing to dispatch."
            "[/supamem.err]"
        )
        raise SystemExit(2)
    # Re-build a canonical URL with default port if missing.
    port = parsed.port or 11434
    return f"http://{host}:{port}"


def _ollama_score_answer_relevance(
    *,
    model: str,
    question: str,
    answer: str,
    contexts: list[str],
) -> JudgeResult:
    """POST a single scoring prompt to the localhost Ollama instance.

    Returns ``JudgeResult(value=float|None, reason=...)``. On any HTTP
    or parse failure, returns ``None`` with the failure reason — never
    raises out of the judge to keep batch evals robust.
    """
    base = _resolve_ollama_host()
    url = f"{base}/api/generate"
    prompt = (
        "On a 0-1 scale, how well does the answer address the question "
        "given the contexts? Respond with a single float.\n"
        f"Question: {question}\nAnswer: {answer}\nContexts: {contexts}\n"
    )
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(  # noqa: S310 — localhost-only, validated above
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            body = resp.read()
        parsed = json.loads(body.decode("utf-8"))
        text = (parsed.get("response") or "").strip()
        if not text:
            return JudgeResult(value=None, reason="ollama empty response")
        # Take the first float-shaped token in the reply.
        for tok in text.replace(",", " ").split():
            try:
                v = float(tok)
                return JudgeResult(value=max(0.0, min(1.0, v)), reason="ollama scored")
            except ValueError:
                continue
        return JudgeResult(value=None, reason="ollama no numeric score")
    except Exception as exc:  # noqa: BLE001
        return JudgeResult(value=None, reason=f"ollama error: {type(exc).__name__}")


def dispatch_judge(
    kind: JudgeKind = "heuristic",
    model: str | None = None,
) -> Judge:
    """Construct a judge instance after re-asserting the D-07 invariant.

    Calls ``assert_no_saas_llm_env`` exactly once. If that raises
    ``RuntimeError`` (SaaS env var set), re-raise as ``SystemExit(2)``
    with a breach line on err_console. SaaS-prefix model strings are
    rejected up front via ``_reject_saas``.

    Defaults: heuristic kind → ``model='n/a'``; ollama kind requires an
    explicit ``model`` argument.
    """
    try:
        assert_no_saas_llm_env()
    except RuntimeError as exc:
        err_console.print(f"[supamem.err]{exc}[/supamem.err]")
        raise SystemExit(2) from exc

    if model is not None:
        _reject_saas(model)

    if kind == "heuristic":
        return Judge(kind="heuristic", model=model or "n/a")
    if kind == "ollama":
        if not model:
            err_console.print(
                "[supamem.err]supamem eval: ollama judge requires a model "
                "(e.g. dispatch_judge(kind='ollama', model='llama3.2:3b'))."
                "[/supamem.err]"
            )
            raise SystemExit(2)
        return Judge(kind="ollama", model=model)
    raise ValueError(f"unknown judge kind: {kind!r}")


def resolve_judge_from_env() -> Judge:
    """Construct a judge from the ``EVAL_JUDGE_MODEL`` env contract.

    Format: ``EVAL_JUDGE_MODEL=ollama:<model>`` for Tier 2; absent /
    empty defaults to Tier 1 heuristic. SaaS-prefix values raise
    ``SystemExit(2)`` (D-JUDGE-03). For Ollama, the resolved host is
    validated as localhost via ``_resolve_ollama_host``.
    """
    spec = os.environ.get("EVAL_JUDGE_MODEL", "").strip()
    if not spec:
        return dispatch_judge(kind="heuristic")

    _reject_saas(spec)

    if spec.startswith("ollama:") or spec.startswith("ollama://"):
        # Validate host eagerly so non-localhost OLLAMA_HOST trips
        # SystemExit(2) at resolve time, not first-score time.
        _resolve_ollama_host()
        # Strip the scheme/prefix to get the model name.
        if spec.startswith("ollama://"):
            model = spec[len("ollama://"):]
        else:
            model = spec[len("ollama:"):]
        if not model:
            err_console.print(
                "[supamem.err]supamem eval: EVAL_JUDGE_MODEL='ollama:' is "
                "missing the model name.[/supamem.err]"
            )
            raise SystemExit(2)
        return dispatch_judge(kind="ollama", model=model)

    # Unknown scheme — surface clearly rather than silently falling
    # back to heuristic.
    err_console.print(
        f"[supamem.err]supamem eval: unknown EVAL_JUDGE_MODEL prefix "
        f"in {spec!r}; expected 'ollama:<model>'.[/supamem.err]"
    )
    raise SystemExit(2)


__all__ = [
    "Judge",
    "JudgeKind",
    "JudgeResult",
    "dispatch_judge",
    "resolve_judge_from_env",
]
