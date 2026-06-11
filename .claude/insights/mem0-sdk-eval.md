---
topic: mem0-sdk-eval
globs: ["src/supamem/eval/coderag/peers/**", ".planning/phases/**mem0**"]
updated: 2026-05-08
entries: 2
---
- Set `OPENAI_API_KEY=<any-non-empty>` (e.g. `sk-dummy-disabled`) before constructing `mem0.Memory(...)` in eval paths even when downstream calls use `infer=False` — the SDK eagerly instantiates an OpenAI LLM client at `Memory.__init__`, so missing env raises before any `infer=False` codepath runs; the dummy value never leaves the local process.
- Scope the OPENAI_API_KEY workaround INLINE on the mem0 eval invocation (`OPENAI_API_KEY=sk-dummy-disabled uv run --no-sync supamem eval ... --peer mem0 ...`); do NOT export it shell-globally, because `auto_goldens` enforces a D-07 invariant ("auto-goldens MUST stay offline — refused to run with SaaS LLM env vars set") and any `OPENAI_API_KEY` value (including the dummy) trips it across `tests/test_eval_runner_scoped.py` + `tests/test_eval_judge.py` (12 false-failure tests collapse the moment the env var is unset).
