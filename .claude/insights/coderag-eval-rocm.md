---
topic: coderag-eval-rocm
globs: ["src/supamem/eval/coderag/**", "src/supamem/rerankers/**", "src/supamem/retrieval/tuned_hybrid_hyde.py"]
updated: 2026-05-11
entries: 3
---
- The combined `tuned_hybrid_hyde` retrieval + `mxbai-rerank-base-v2` rerank stack triggers an AMD ROCm 6.4 `HW Exception by GPU node-1 ... reason :GPU Hang` on RX 6800 XT after only a handful of scoring batches when both axes of load run simultaneously (Ollama HyDE rewrites + mxbai forward passes). 17-E (AST-only + default rerank) and 17-F (HyDE-only + default rerank, no re-ingest) ran cleanly because they don't stack both pressures. Mitigation when ADR §9 needs the combined data point: split the workload (run scoring in smaller batches, drain Ollama between batches), or accept the combined configuration as an opt-in-only documented limitation.
- After a ROCm GPU Hang, `ollama` unloads `llama3.2:3b` from VRAM and `supamem doctor` warm-pool panel will report `NOT loaded — first HyDE call will pay 10–30s`. Before retrying any HyDE eval after a hang, re-run `ollama run llama3.2:3b ""` and confirm the doctor panel flips back to `loaded` — otherwise the first measured query absorbs cold-start latency and pollutes p95.
- `src/supamem/eval/coderag/runner.py` silently swallows mid-run GPU errors: when reranker forward passes fail on a hung GPU, the eval exits cleanly with code 0 and writes NO output JSON, so a green exit code does NOT mean the run succeeded. Always assert the output JSON exists before declaring a Wave-3 measurement plan complete. Bug-shaped: runner should either re-raise on reranker exceptions or write a partial envelope flagged `incomplete: true` so the orchestrator can detect the gap.
