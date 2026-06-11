---
status: accepted
date: 2026-06-11
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["ADR-0002", "Phase 18", "SimpleMem"]
---

# 0003. SimpleMem Evaluation — Selective Borrow Under Local-Only

## Context

[SimpleMem](https://arxiv.org/abs/2601.02553) (MIT, `aiming-lab/SimpleMem`) proposes a
three-stage memory pipeline: (1) semantic structured compression of dialogue into
atomic facts, (2) online semantic synthesis merging fragments, and (3) intent-aware
retrieval with adaptive depth (`k_dyn = ⌊k_base·(1+δ·C_q)⌋`). Headline benchmarks on
**LoCoMo** (conversational long-term memory) report strong F1 and large token-reduction
claims. supamem already demoted LongMemEval as a ship gate in favor of the **CodeRAG**
agentic-coding eval (ADR-0002; Phases 13–17).

supamem's contract is **local-first / offline / zero hard provider dependencies**.
Phase 17 HyDE (`tuned_hybrid_hyde`) allowed **opt-in Ollama** for query expansion (D-07).
Phase 18 tightens this for **all SimpleMem-inspired borrows**: no LLM at write or read
time on any path (D-A2). Borrowed concepts must be deterministic, heuristic, or
rule-based only.

This ADR records per-concept BORROW / DEFER / REJECT verdicts before shipping borrow
code in Plans F–H. Default-changing behavior remains gated by CodeRAG no-regression
floors per ADR-0002 §7 (D-A4); opt-in plugins may ship with defaults OFF.

### LoCoMo transfer caveat

SimpleMem's published F1 and token-economy numbers are measured on **LoCoMo**
(conversational QA over multi-session chat). supamem indexes code chunks, ADRs, and
planning artifacts for agentic coding ("where is X defined?", "why did we choose Y?").
**These headline numbers do not transfer to coding retrieval** without re-measurement on
the CodeRAG suite. No README benchmark updates ship from this evaluation (Phase 13
publish gate remains the sole claims surface).

## Decision

### Concept verdicts (locked)

| Concept | Verdict | Rationale |
|---------|---------|-----------|
| Adaptive retrieval depth (`k_dyn` heuristic) | **BORROW** | D-A3a: local complexity signal → `k` modulation; opt-in, default OFF; no LLM for `C_q`. |
| Heuristic dedup (content-hash + cosine merge) | **BORROW** | D-A3b: extend existing `tuned_hybrid` mechanical dedup; REJECT the LLM synthesis half of SimpleMem stage 2. |
| EvolveMem closed loop (observe → diagnose → apply → re-measure) | **BORROW** | D-A3d: rule-based diagnosis only; explicit invoke; CodeRAG-gated apply; no LLM AutoResearch. |
| Symbolic / metadata layer (`room`, `path_prefix`, `valid_to`) | **BORROW** | D-A3e: harden existing facet layer and write-path guards; not a new subsystem. |
| Atomic-fact compression (Semantic Structured Compression) | **REJECT** | D-A3c: value is LLM rewriting dialogue into facts; local-only forbids; overlaps Phase 12 prose-compressor CUT. |
| LLM semantic synthesis (Online Semantic Synthesis) | **REJECT** | D-A2 / D-A3b: merging fragments via LLM-written abstractions conflicts with local-only; mechanical dedup only. |

### Local-only stance (stricter than Phase 17 HyDE)

Phase 18 borrows **must not** import or call OpenAI, Anthropic, Ollama, LiteLLM, or
any other LLM provider on write or read paths — including opt-in borrow modules. This is
**stricter than Phase 17**, where HyDE permitted opt-in Ollama query expansion. HyDE
remains unchanged; new Phase 18 modules do not inherit that exception.

### Packaging

- Retrieval-affecting borrows register via `supamem.retrieval` entry-points (D-48).
- All borrow flags default **OFF** (D-A4). Flipping defaults requires CodeRAG
  no-regression ± ε per ADR-0002 §7.

## Consequences

### Positive

- Durable public record of what was evaluated, borrowed, and rejected before code ships.
- CI tests lock ADR shape and enforce no-LLM imports on Phase 18 borrow modules.
- Opt-in borrows can ship without blocking on CodeRAG default-flip gates.

### Negative / trade-offs

- SimpleMem's largest reported gains (LLM compression + synthesis) are unavailable
  under local-only; headline LoCoMo numbers are not actionable for supamem claims.
- EvolveMem rule-based loop is reimplemented from scratch; SimpleMem's LLM AutoResearch
  is not ported.
- Adaptive depth and dedup require users to opt in; defaults stay conservative.

### Neutral

- Phase 17 HyDE continues as opt-in Ollama; unaffected by this ADR.
- CodeRAG remains the sole ship gate for default-changing retrieval behavior.

## Alternatives Considered

### Port the `simplemem` PyPI package

**Rejected.** The package routes through OpenAI-compatible providers for compression,
synthesis, and retrieval diagnosis. Conflicts with D-A2 local-only and zero hard
provider-dep posture.

### LLM-driven EvolveMem diagnosis (SimpleMem AutoResearch)

**Rejected.** SimpleMem's EvolveMem uses LLM search/optimization over failure logs.
Phase 18 ships hand-written failure-pattern → config-delta rules scored by CodeRAG
metrics instead (D-A3d).

### Heuristic-only atomic-fact compression (no LLM)

**Deferred / not selected.** A deterministic fact-extraction pass without LLM rewrite
was considered but not shipped this phase. Revisit only if D-A2 is relaxed or a
non-LLM compression need emerges.

### Default-on adaptive depth or dedup

**Rejected for Phase 18.** Per Phase 17 D-LAT-01 precedent and D-A4, borrows ship
opt-in first; default flip requires CodeRAG floors.
