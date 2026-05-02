"""``supamem doctor`` — Qdrant health probe + config chain + install drift.

Sections rendered to stdout:

1. **Health** — Qdrant reachability, collection presence, sparse-vector support.
2. **Config chain** — every ``ResolvedConfig`` field with the rung that set it.
3. **Installed clients** — per-host managed-block fence version vs the
   currently installed ``supamem.__version__`` (drift advisory).
4. Exit code: ``0`` healthy / ``1`` any drift, unreachable, or missing
   collection / ``2`` malformed config.

The ``qdrant_api_key`` field is redacted by default (T-80.6-11-01). All Qdrant
calls live inside a try/except so an unhandled exception never leaks the URL
or key into a stack trace (T-80.6-11-03).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from supamem import __version__
from supamem.config import ConfigChain, ResolvedConfig, load_config
from supamem.console import banner, console, err, info, ok, warn
from supamem.init import probe_qdrant

log = logging.getLogger("supamem.doctor")

def _client_targets() -> tuple[tuple[str, Path], ...]:
    """Lazy lookup so test monkeypatches of ``Path.home`` take effect."""
    return (
        ("claude-code", Path.home() / "CLAUDE.md"),
        ("opencode", Path.home() / "AGENTS.md"),
    )

_VERSION_RE = re.compile(r"BEGIN SUPAMEM v([\d\.]+) MANAGED BLOCK")


def _redact(value: str, *, enabled: bool) -> str:
    if not enabled:
        return value
    if not value:
        return "(empty)"
    return f"***({len(value)} chars)"


def format_chain(
    cfg: ResolvedConfig,
    chain: ConfigChain,
    *,
    redact_secrets: bool = True,
) -> str:
    lines: list[str] = []
    cfg_dict = asdict(cfg)
    chain_dict = asdict(chain)
    for key in sorted(cfg_dict):
        value = cfg_dict[key]
        source = chain_dict.get(key, "default")
        rendered = _redact(str(value), enabled=True) if key == "qdrant_api_key" and redact_secrets else value
        lines.append(f"  {key:<24} = {rendered!r:<24} [source: {source}]")
    return "\n".join(lines)


def version_drift_report() -> list[dict[str, Any]]:
    """For each known client target, return drift info vs ``__version__``."""
    out: list[dict[str, Any]] = []
    for client, path in _client_targets():
        if not path.exists():
            out.append({"client": client, "path": str(path), "present": False, "drift": False})
            continue
        body = path.read_text(encoding="utf-8")
        match = _VERSION_RE.search(body)
        if not match:
            out.append({"client": client, "path": str(path), "present": True, "block_version": None, "drift": False})
            continue
        block_version = match.group(1)
        out.append(
            {
                "client": client,
                "path": str(path),
                "present": True,
                "block_version": block_version,
                "current": __version__,
                "drift": block_version != __version__,
            }
        )
    return out


def _collection_health(client: Any, name: str) -> dict[str, Any]:
    """Return a dict describing collection presence + sparse support."""
    try:
        info_obj = client.get_collection(name)
    except Exception as exc:  # noqa: BLE001
        return {"present": False, "error": type(exc).__name__}
    sparse = bool(getattr(info_obj.config.params, "sparse_vectors", None))
    return {"present": True, "sparse": sparse}


def run_doctor(*, redact_secrets: bool = True) -> int:
    cfg, chain = load_config()
    banner("supamem doctor", f"v{__version__}")

    # ── Section 1: Health ────────────────────────────────────────────────
    console.print("[supamem.brand]Health[/supamem.brand]")
    qdrant_up = probe_qdrant(cfg.qdrant_url)
    if qdrant_up:
        ok(f"Qdrant reachable at {cfg.qdrant_url}")
    else:
        err(f"Qdrant unreachable at {cfg.qdrant_url}")

    coll_status: dict[str, Any] = {"present": False}
    if qdrant_up:
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(
                url=cfg.qdrant_url,
                api_key=cfg.qdrant_api_key or None,
                check_compatibility=False,
                timeout=10,
            )
            coll_status = _collection_health(client, cfg.collection)
        except Exception as exc:  # noqa: BLE001
            warn(f"could not query collection {cfg.collection!r}: {type(exc).__name__}")

        if coll_status.get("present"):
            sparse = "sparse+dense" if coll_status.get("sparse") else "dense-only"
            ok(f"collection {cfg.collection!r} ({sparse})")
        else:
            err(f"collection {cfg.collection!r} missing")

    # ── Section 2: Config chain ──────────────────────────────────────────
    console.print()
    console.print("[supamem.brand]Config chain[/supamem.brand]")
    # Use console with soft_wrap=True + no_wrap=True so the full
    # "key = value [source: ...]" line stays on one row even under
    # pytest's narrow default width — capsys captures stdout regardless.
    console.print(
        format_chain(cfg, chain, redact_secrets=redact_secrets),
        soft_wrap=True,
        no_wrap=True,
        highlight=False,
        markup=False,
    )

    # ── Section 2b: MCP caps (D-12) ──────────────────────────────────────
    console.print()
    console.print("[supamem.brand]MCP caps[/supamem.brand]")
    ok(
        f"max_top_k         = {cfg.mcp_caps_max_top_k}  "
        f"[source: {chain.mcp_caps_max_top_k}]"
    )
    ok(
        f"max_query_chars   = {cfg.mcp_caps_max_query_chars}  "
        f"[source: {chain.mcp_caps_max_query_chars}]"
    )
    ok(
        f"max_preview_chars = {cfg.mcp_caps_max_preview_chars}  "
        f"[source: {chain.mcp_caps_max_preview_chars}]"
    )

    # ── Section 2c: Transcript config (Phase 6 D-31) ─────────────────────
    console.print()
    console.print("[supamem.brand]Transcript config[/supamem.brand]")
    ok(
        f"default_root            = {cfg.transcript_default_root}  "
        f"[source: {chain.transcript_default_root}]"
    )
    ok(
        f"since_days              = {cfg.transcript_since_days}  "
        f"[source: {chain.transcript_since_days}]"
    )
    ok(
        f"tool_payload_max_chars  = {cfg.transcript_tool_payload_max_chars}  "
        f"[source: {chain.transcript_tool_payload_max_chars}]"
    )
    ok(
        f"chunk_soft_max_tokens   = {cfg.transcript_chunk_soft_max_tokens}  "
        f"[source: {chain.transcript_chunk_soft_max_tokens}]"
    )
    ok(
        f"include_paths_glob      = {cfg.transcript_include_paths_glob}  "
        f"[source: {chain.transcript_include_paths_glob}]"
    )
    ok(
        f"exclude_paths_glob      = {cfg.transcript_exclude_paths_glob}  "
        f"[source: {chain.transcript_exclude_paths_glob}]"
    )

    # ── Section 2d: Classifier rooms (Phase 7 D-16) ──────────────────────
    console.print()
    console.print("[supamem.brand]Classifier rooms[/supamem.brand]")
    for room, kws in cfg.classifier_rooms.items():
        ok(f"  {room:<12} = {kws}  [source: {chain.classifier_rooms}]")
    # Load the on-disk manifest so we can surface the persisted classifier
    # hash (post-sweep state). Missing/malformed manifest → '(none)'.
    from supamem.indexer import _manifest_path
    from supamem.indexer.manifest import Manifest

    try:
        _mf = Manifest.load(_manifest_path(cfg))
        _ch = _mf.classifier_hash
    except Exception:  # noqa: BLE001 — non-essential probe (CLAUDE.md sanctions)
        _ch = None
    ok(f"  classifier_hash = {_ch or '(none)'}")

    # ── Section 2e: Room histogram (Phase 7 D-07) ────────────────────────
    console.print()
    console.print("[supamem.brand]Room histogram[/supamem.brand]")
    try:
        from qdrant_client.http import models as qmodels
    except Exception:  # noqa: BLE001 — qdrant-client may be missing
        qmodels = None  # type: ignore[assignment]

    # ``client`` is bound only inside the qdrant_up branch above, so guard
    # it here. The `null` bucket is ALWAYS shown (D-07) — even with no
    # Qdrant connection it surfaces as `: 0`, matching T-07-02-04.
    _client_for_histogram = locals().get("client") if qdrant_up else None
    for room in [*cfg.classifier_rooms.keys(), None]:
        label = "null" if room is None else room
        n = 0
        if _client_for_histogram is not None and qmodels is not None:
            try:
                if room is None:
                    cf = qmodels.Filter(
                        must=[qmodels.IsNullCondition(
                            is_null=qmodels.PayloadField(key="room")
                        )]
                    )
                else:
                    cf = qmodels.Filter(
                        must=[qmodels.FieldCondition(
                            key="room", match=qmodels.MatchValue(value=room)
                        )]
                    )
                n = _client_for_histogram.count(
                    collection_name=cfg.collection, count_filter=cf
                ).count
            except Exception:  # noqa: BLE001 — non-essential probe (T-07-02-04)
                n = 0
        ok(f"  {label:<12} : {n}")

    # ── Section 2f: Reranker (Phase 8 D-DOCTOR-01 / D-DOCTOR-02 / D-CPU-02) ───
    console.print()
    console.print("[supamem.brand]Reranker[/supamem.brand]")
    reranker_drift = False  # local accumulator OR-ed into rc on line ~295
    rname = getattr(cfg, "reranker_name", "off")
    rname_src = getattr(chain, "reranker_name", "default")
    ok(f"name           = {rname}  [source: {rname_src}]")

    if rname != "off":
        rmodel = getattr(cfg, "reranker_model_id", "(unset)")
        ok(f"model_id       = {rmodel}")
        try:
            from supamem.rerankers import _model_cache_dir  # noqa: PLC0415

            cache_root = _model_cache_dir()
            ok(f"cache_path     = {cache_root}")

            slug = rmodel.replace("/", "--")
            snap_candidates = list(
                cache_root.glob(f"models--{slug}/snapshots/*")
            ) or list(cache_root.glob(f"{slug}/*"))
            if not snap_candidates:
                warn("snapshot not found — run `supamem repair`")
                reranker_drift = True
            else:
                snap = snap_candidates[0]
                manifest_path = snap / "_expected_manifest.json"
                try:
                    import json as _json  # noqa: PLC0415

                    m = _json.loads(manifest_path.read_text())
                    expected_total = int(m.get("total_bytes", 0))
                    expected_files = m.get("files", {})
                    actual_files = {
                        str(p.relative_to(snap)): p.stat().st_size
                        for p in snap.rglob("*")
                        if p.is_file() and p.name != "_expected_manifest.json"
                    }
                    actual_total = sum(actual_files.values())
                    pct = (actual_total / expected_total) if expected_total else 0.0
                    missing = set(expected_files) - set(actual_files)
                    if missing or pct < 0.9:
                        warn(
                            f"size           = {actual_total} / {expected_total} bytes "
                            f"({pct:.0%}); missing {len(missing)} files — run `supamem repair`"
                        )
                        reranker_drift = True
                    else:
                        ok(
                            f"size           = {actual_total} bytes "
                            f"({len(actual_files)} files)"
                        )
                except Exception:  # noqa: BLE001 — non-essential probe (T-INTEGRITY-01)
                    warn("manifest unreadable — run `supamem repair`")
                    reranker_drift = True
        except Exception:  # noqa: BLE001
            warn("reranker cache probe failed — run `supamem repair`")
            reranker_drift = True

        # Latency telemetry — DEQUE path (D-CPU-02 / W3 verifiable percentiles).
        try:
            from supamem.stats.counter import (  # noqa: PLC0415
                get_latency_samples,
            )

            samples = get_latency_samples("rerank", "rerank_latency_ms")
            if samples:
                import statistics as _st  # noqa: PLC0415

                p50 = _st.median(samples)
                p95 = (
                    _st.quantiles(samples, n=20)[18]
                    if len(samples) >= 20
                    else max(samples)
                )
                ok(
                    f"rerank_p50_ms  = {p50:.1f}  rerank_p95_ms  = {p95:.1f}  "
                    f"(n={len(samples)})"
                )
            else:
                ok("rerank latency = (no samples yet)")
        except Exception:  # noqa: BLE001
            pass

        # Detected device (D-CPU-02 backend detection).
        device = "cpu"
        try:
            import torch  # noqa: PLC0415 — local import only on doctor probe

            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
        except ImportError:
            device = "cpu (torch not installed)"
        ok(f"device         = {device}")
        # D-CPU-03 escape-hatch hint:
        info(
            "(set retrieval.reranker = 'off' to disable; restores pre-Phase-8 latency)"
        )

    # ── Section 3: Installed clients drift ───────────────────────────────
    console.print()
    console.print("[supamem.brand]Installed clients[/supamem.brand]")
    drift_rows = version_drift_report()
    any_drift = False
    for row in drift_rows:
        if not row["present"]:
            info(f"{row['client']:<12} not installed ({row['path']})")
            continue
        if row.get("block_version") is None:
            info(f"{row['client']:<12} present, no managed block detected")
            continue
        if row.get("drift"):
            any_drift = True
            warn(
                f"{row['client']:<12} drift: managed-block v{row['block_version']} "
                f"vs current v{row['current']}"
            )
        else:
            ok(f"{row['client']:<12} v{row['block_version']} (current)")

    # ── Section 4: Update check ──────────────────────────────────────────
    console.print()
    console.print("[supamem.brand]Update check[/supamem.brand]")
    from supamem.update_check import doctor_report

    uc = doctor_report(__version__)
    cached = uc.get("cached_latest_version")
    last_ts = uc.get("last_check_ts")
    last_human = (
        time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(last_ts))
        if last_ts
        else "never"
    )
    if uc.get("update_available"):
        warn(
            f"update available: {uc['current_version']} → {cached} "
            f"(last check: {last_human})"
        )
    elif cached:
        ok(f"on latest cached version v{cached} (last check: {last_human})")
    else:
        info(f"no probe yet — runs in background on next invocation (cache: {uc['cache_path']})")
    if uc.get("suppressed_by_env"):
        info(f"suppressed by env: {', '.join(uc['suppressed_by_env'])}")

    # ── Section 5: Exit code ─────────────────────────────────────────────
    rc = 0
    if (
        not qdrant_up
        or any_drift
        or reranker_drift
        or (qdrant_up and not coll_status.get("present"))
    ):
        rc = 1
    console.print()
    if rc == 0:
        ok("doctor: all green")
    else:
        warn(f"doctor: issues detected (exit {rc})")
    return rc


__all__ = ["format_chain", "run_doctor", "version_drift_report"]
