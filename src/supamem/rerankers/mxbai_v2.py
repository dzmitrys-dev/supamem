"""MxbaiV2Reranker — default supamem reranker plugin.

Thin wrapper around :class:`mxbai_rerank.MxbaiRerankV2` with a lazy
``_ensure()`` that mirrors :class:`supamem.retrieval.tuned_hybrid.TunedHybridBackend._ensure`
(cheap ``__init__``; model materializes on first ``rerank()`` call). Heavy
imports (``torch``, ``transformers``, ``mxbai_rerank``) live INSIDE
``_ensure`` so cold ``supamem --help`` does not pay the cost (RESEARCH
Pitfall 1; D-CONTRACT-02).
"""
from __future__ import annotations

import time
from typing import Any

from supamem.config import ResolvedConfig
from supamem.console import err_console
from supamem.retrieval.types import RetrievedChunk


class MxbaiV2Reranker:
    """Cross-encoder reranker over ``mixedbread-ai/mxbai-rerank-base-v2``."""

    name = "mxbai_v2"
    model_id = "mixedbread-ai/mxbai-rerank-base-v2"

    def __init__(self, *, config: ResolvedConfig) -> None:
        self.config = config
        self._model: Any | None = None

    def _ensure(self) -> Any:
        if self._model is None:
            t0 = time.perf_counter()
            # Heavy imports inside the function — cold CLI must not import
            # torch / transformers / mxbai_rerank (D-CONTRACT-02).
            from mxbai_rerank import MxbaiRerankV2  # noqa: PLC0415

            self._model = MxbaiRerankV2(self.config.reranker_model_id)

            # Compatibility shim: mxbai-rerank 0.1.6 calls
            # `tokenizer.prepare_for_model(ids, pair_ids, ...)` unconditionally,
            # but the slow Qwen2Tokenizer in transformers >=4.50 no longer
            # exposes this method (and PreTrainedTokenizerBase no longer
            # provides a fallback impl as of recent releases). Upstream
            # mxbai-rerank is unmaintained (no fix released as of 2026-05-04).
            # Implement the minimal call signature mxbai uses:
            # add_special_tokens=False, padding=False, truncation="only_second".
            try:
                tok = getattr(self._model, "tokenizer", None)
                if tok is not None and not hasattr(tok, "prepare_for_model"):
                    def _shim_prepare_for_model(
                        ids,
                        pair_ids=None,
                        *,
                        truncation=None,
                        max_length=None,
                        padding=False,  # noqa: ARG001
                        return_attention_mask=False,
                        return_token_type_ids=False,
                        add_special_tokens=False,  # noqa: ARG001
                        **_kwargs,
                    ):
                        a = list(ids)
                        b = list(pair_ids) if pair_ids is not None else []
                        if max_length is not None:
                            if truncation == "only_second":
                                budget = max_length - len(a)
                                if budget < 0:
                                    a = a[:max_length]
                                    b = []
                                elif len(b) > budget:
                                    b = b[:budget]
                            elif truncation in (
                                True,
                                "longest_first",
                                "only_first",
                            ):
                                while len(a) + len(b) > max_length:
                                    if truncation == "only_first" or len(a) > len(b):
                                        a.pop()
                                    else:
                                        b.pop()
                        combined = a + b
                        out: dict[str, Any] = {"input_ids": combined}
                        if return_attention_mask:
                            out["attention_mask"] = [1] * len(combined)
                        if return_token_type_ids:
                            out["token_type_ids"] = [0] * len(a) + [1] * len(b)
                        return out

                    tok.prepare_for_model = _shim_prepare_for_model
            except Exception as _shim_exc:  # noqa: BLE001
                err_console.print(
                    "[supamem.warn]prepare_for_model shim failed "
                    f"({type(_shim_exc).__name__}: {_shim_exc}); "
                    "rerank may still raise"
                )

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            try:
                from supamem.stats.counter import bump  # noqa: PLC0415

                bump(
                    kind="rerank",
                    source="load_latency_ms",
                    tokens=0,
                    latency_ms=elapsed_ms,
                )
            except Exception:  # noqa: BLE001 — non-essential probe
                pass
        return self._model

    def rerank(
        self, query: str, candidates: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        top_n = min(self.config.reranker_top_n, len(candidates))
        if self.config.reranker_top_n > len(candidates):
            err_console.print(
                f"[supamem.warn]reranker_top_n={self.config.reranker_top_n} "
                f"> unique candidates ({len(candidates)}); reranking all"
            )
        model = self._ensure()
        t0 = time.perf_counter()
        documents = [c.text for c in candidates]
        results = model.rank(
            query,
            documents,
            top_k=top_n,
            batch_size=self.config.reranker_batch_size,
            return_documents=False,
            sort=True,
            show_progress=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            from supamem.stats.counter import bump  # noqa: PLC0415

            bump(
                kind="rerank",
                source="rerank_latency_ms",
                tokens=len(candidates),
                latency_ms=elapsed_ms,
            )
        except Exception:  # noqa: BLE001 — non-essential probe
            pass

        out: list[RetrievedChunk] = []
        for r in results:
            src = candidates[int(r.index)]
            score = float(r.score)
            # frozen=True on RetrievedChunk forbids attribute assignment;
            # always go through model_copy(update=...) (RESEARCH Pitfall 4,
            # T-FROZEN-01 mitigation).
            out.append(
                src.model_copy(
                    update={"score": score, "rerank_score": score}
                )
            )
        return out
