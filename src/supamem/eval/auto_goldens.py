"""Auto-goldens generator — D-07 invariant: no SaaS LLM calls.

Auto-generates ``required_substrings`` for golden records by extracting
identifiers / tokens from the answer text using a deterministic local
algorithm. NEVER calls OpenAI / Anthropic / any external API — D-07 is the
load-bearing invariant: goldens must be reproducible offline.
"""
from __future__ import annotations

import os
import re

# Env vars whose presence indicates a SaaS LLM SDK is configured. If any is
# set, the user is in a "could call cloud" state — we refuse to run auto-
# goldens to make the D-07 invariant obvious at the boundary.
_SAAS_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY",
)


def _identifier_tokens(text: str, *, min_len: int = 4) -> list[str]:
    """Extract camelCase / snake_case / dotted-name tokens that look code-shaped.

    Heuristic: any run of [A-Za-z_][A-Za-z0-9_]+ at least ``min_len`` chars,
    plus dotted names like ``module.func``. Returns deduped list, order
    preserved.
    """
    pat = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]+[A-Za-z0-9_]")
    seen: set[str] = set()
    out: list[str] = []
    for tok in pat.findall(text):
        if len(tok) < min_len:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def derive_required_substrings(
    answer_text: str,
    *,
    max_subs: int = 5,
    min_len: int = 4,
) -> list[str]:
    """Deterministic substring extraction. Pure function, no I/O."""
    tokens = _identifier_tokens(answer_text, min_len=min_len)
    return tokens[:max_subs]


def assert_no_saas_llm_env() -> None:
    """Raise RuntimeError if any SaaS LLM env var is set (D-07 enforcement)."""
    found = [name for name in _SAAS_ENV_VARS if os.environ.get(name, "").strip()]
    if found:
        raise RuntimeError(
            "supamem auto_goldens: D-07 invariant breach — refused to run "
            "with SaaS LLM env vars set ({}). Auto-goldens MUST stay offline."
            .format(", ".join(found))
        )


__all__ = [
    "assert_no_saas_llm_env",
    "derive_required_substrings",
]
