---
status: accepted
date: 2026-11-17
deciders: [dzmitrys-dev]
consulted: []
informed: []
related: ["v0.4.0a6", "issue #5", "ADR-0004"]
---

# 0005. Shared model daemon — one copy of the retrieval models per machine

## Context

Every process that ran a retrieval loaded about 2.4 GB of models itself: mxbai-rerank-base-v2,
which the mxbai library upcasts from bf16 to fp32 on CPU, plus the fastembed dense and BM25 models.

- **MCP servers.** Claude Code starts one stdio server per window. Each pinned 2.47 GB for its
  whole life (measured with `footprint`). a5's idle unload frees that after 30 idle minutes,
  but N active windows still hold N copies.
- **Edit hook.** `supamem hook claude-code` runs as a fresh process for every Edit/Write and
  loaded all the models every time. Journal latency: p50 24.6 s, p90 73.6 s. On 2026-11-17 a
  single call took 397 s with a peak footprint of 8.66 GB (main query plus shadow query).
- **Result.** With several windows editing at once, the 24 GB Mac reached 27.4 of 28 GB swap.
  The owner asked to "fix it so these leaks never repeat".

## Decision

1. **One shared daemon.**
   - `supamem daemon serve`: a `socketserver.ThreadingUnixStreamServer` on a user-only socket,
     `<cache>/run/daemon.sock`, mode 0600 in a 0700 directory. If that path exceeds `sun_path`,
     the socket goes to `/tmp/supamem-<uid>/<sha8>.sock`.
   - Protocol: newline-delimited JSON, one request per connection. Ops: `ping`, `query`,
     `shutdown`. Standard library only.
   - Spawned on demand by the first client, as a detached `Popen` with `start_new_session` and
     stdout redirected away. The hook's stdout and an MCP server's JSON-RPC stdout are never
     inherited.
   - Exits by itself after `mcp_model_idle_minutes` with nothing in flight.
   - Exactly one per cache root: a process-lifetime pid `FileLock` (fcntl, released by the
     kernel even on `kill -9`).
2. **Clients send their resolved config** (`dataclasses.asdict`). The daemon never resolves
   config itself, because that depends on the client's env, cwd and worktree.
   - The daemon caches one backend per full-config hash, because a backend reads about ten
     config fields per query.
   - Heavy models come from a process-wide pool (`retrieval/model_pool.py`) keyed by model id.
     One process holds one copy of each model however many collections it serves.
3. **Overload means skip, never load.**
   - One query runs at a time; at most 8 wait. Beyond that the daemon replies `busy`.
   - A request that has waited past its client's deadline (`deadline_s`) is dropped as `expired`.
   - `DaemonBusy` and `DaemonError` never trigger an in-process load. Only `DaemonUnavailable`
     (unreachable and could not be spawned) does.
   - The hook serializes that in-process fallback across the machine (`hook-inproc.lock`,
     3 s wait, then skip).
4. **The hook stays light.**
   - `tuned_hybrid` is imported lazily, so the hook process no longer pulls in
     fastembed, onnxruntime or qdrant.
   - The shadow query is skipped once 20 s have passed, so it cannot push the main injection
     past the hook timeout.
5. **The MCP server proxies to the daemon too** (server ≈ 119 MB). It falls back to the a5
   in-process path when the daemon is unreachable.
6. **Upgrades:** each request carries the client version. A newer client makes the daemon
   retire after draining, and the next client spawns the new code.
7. **Kill switch:** `[supamem.daemon] enabled` (default true) and env `SUPAMEM_DAEMON=off|on`.
   The daemon is never spawned in unit tests.

## Consequences

Measured on the owner's machine, at about 95% swap:

| Process | Before | After |
|---|---|---|
| Hook process | 2.5–8.7 GB | ~32 MB |
| MCP server | 2.47 GB | 119 MB |
| Three concurrent windows | 3 × ~3 GB | one daemon at ~2.85 GB |

- **Latency.**
  - Warm queries take 20–45 s on that host, and the reranker forward pass dominates: rerank
    off takes 0.3 s.
  - A cold daemon (first query after an idle exit) can take more than 60 s under heavy swap, so
    the first edit after idle may skip its injection.
- **The daemon is a single point of failure for injection.** Failures are visible in
  `daemon.log`, in the journal (empty rows), in `supamem daemon status` and in the `doctor`
  panel. `kill -USR1 <pid>` dumps every thread's stack.
- **Queries are serialized**, so a burst across windows queues. Past a client's deadline,
  requests expire instead of piling up dead work.
- **The Qdrant API key travels over the 0600 socket.** Request bodies are never logged.

## Alternatives Considered

- **Reuse `run_http` (MCP streamable-http).** Rejected:
  - it is bound to one config, so it serves one collection only;
  - the tool caps `top_k` at 25 (the hook needs 30) and drops the `rerank_score` and `payload`
    fields the hook's filters need;
  - an MCP handshake per hook call;
  - a TCP port is reachable by every local user.
- **A launchd/systemd service.** Rejected: it adds per-OS install state, keeps the models in
  memory even when unused, and needs uninstall handling. Lazy spawn plus idle exit gives zero
  footprint at rest.
- **Idle unload only (a5).** Rejected: it doesn't bound the number of copies while windows are
  active, and does nothing for the per-call hook spikes.
- **bf16 or int8 reranker, or MPS.** Deferred.
  - bf16/int8 change the rerank scores, and the hook's `min_score` 2.5 was calibrated on fp32
    CPU scores.
  - `mxbai_rerank.auto_device()` ignores MPS, so the model runs fp32 on CPU even where Apple
    GPUs are available. Moving it to MPS would cut latency, but it would compete with GPU
    work that other projects (ferrohex) run under their own GPU lock — an owner decision.
  - `ping` now reports the reranker dtype so the next step is decided on data.
