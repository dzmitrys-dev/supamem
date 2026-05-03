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

# PEP 440 versions accept alpha/beta/rc/dev suffixes; matches config_io._FENCE_RE.
_VERSION_RE = re.compile(r"BEGIN SUPAMEM v([\w\.\+\-]+) MANAGED BLOCK")


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


def _render_subagent_reachability_panel() -> None:
    """Render the Subagent reachability panel (Phase 08.1 D-DOCTOR-01..05).

    Read-only by construction (P9): never invokes patch_all / unpatch_all
    nor writes any file. Walks ``scan_agent_dirs`` to surface the CURRENT
    on-disk state, cross-referenced against the manifest for the
    patched/covered/inheritance/skipped state.

    Exit code is NEVER bumped by this panel (D-DOCTOR-04): every entry —
    including ``skipped:*`` rows — is informational. Broad try/except
    around the manifest load + each per-file probe so a malformed
    manifest or unreadable agent file never breaks the rest of doctor
    (T-08.1.05-03 mitigation).
    """
    # Lazy imports keep doctor cold-start cheap and avoid a hard import
    # cycle if the patcher module ever grows a dependency on doctor.
    from supamem.install.agent_patcher import (  # noqa: PLC0415
        load_manifest,
        manifest_path,
        scan_agent_dirs,
    )

    console.print()
    console.print("[supamem.brand]Subagent reachability[/supamem.brand]")

    try:
        manifest = load_manifest()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash here (T-08.1.05-03)
        warn(f"manifest unreadable: {exc!r}")
        manifest = {"patches": []}

    patched_paths: set[str] = {
        str(entry.get("path", ""))
        for entry in manifest.get("patches", [])
        if isinstance(entry, dict)
    }
    n_patches = len(patched_paths)

    try:
        scanned = scan_agent_dirs()
    except Exception as exc:  # noqa: BLE001 — same defensive shape (T-08.1.05-03)
        warn(f"agent scan failed: {exc!r}")
        scanned = []

    by_scope: dict[str, list[tuple[Path, str, bool]]] = {"global": [], "project": []}
    for path, scope in scanned:
        try:
            from supamem.install.agent_patcher import detect_tools_state  # noqa: PLC0415

            text = path.read_text(encoding="utf-8")
            state = detect_tools_state(text)
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            log.debug("doctor: %s read/classify failed: %r", path, exc)
            state = "skipped:read-error"
        is_patched = str(path) in patched_paths
        by_scope[scope].append((path, state, is_patched))

    # Detect manifest entries whose files no longer exist on disk
    # (D-DOCTOR-05 stale-entry surface — info line, not an exit-bump).
    on_disk_paths: set[str] = {str(p) for p, _, _ in by_scope["global"] + by_scope["project"]}
    stale_entries = sorted(p for p in patched_paths if p and p not in on_disk_paths)

    # Group rendering: global first (D-DOCTOR-03), then project.
    for scope_name in ("global", "project"):
        entries = by_scope[scope_name]
        if not entries:
            continue
        if scope_name == "global":
            console.print(
                "  ~/.claude/agents/                                  [global]",
                highlight=False,
                markup=False,
            )
        else:
            console.print(
                "  <project>/.claude/agents/                          [project]",
                highlight=False,
                markup=False,
            )
        for path, state, is_patched in entries:
            relpath = path.name
            line = _format_reachability_row(relpath, state, is_patched)
            console.print(line, highlight=False, markup=False)

    # Stale manifest entries (file deleted but manifest still references it).
    for stale in stale_entries:
        info(f"manifest entry stale (file missing): {stale}")

    # D-DOCTOR-05 + D-UNDO-01 REVISED footer.
    mp = manifest_path()
    if mp.exists() and n_patches > 0:
        console.print(
            f"  → manifest: {mp} ({n_patches} patches recorded)",
            highlight=False,
            markup=False,
        )
        console.print(
            "  → run `supamem unpatch-agents` to restore originals "
            "before `pip uninstall supamem`",
            highlight=False,
            markup=False,
        )
    else:
        # Hint to run repair only if there is at least one patchable file
        # AND no manifest exists. Skip the hint when everything is already
        # covered or fully inheritance.
        all_entries = by_scope["global"] + by_scope["project"]
        has_patchable = any(
            state.startswith("patchable") for _, state, _ in all_entries
        )
        if has_patchable:
            console.print(
                "  → run `supamem repair` to patch agent whitelists",
                highlight=False,
                markup=False,
            )


def _format_reachability_row(relpath: str, state: str, is_patched: bool) -> str:
    """Format a single per-agent line per D-DOCTOR-02.

    ``<status_icon>  <relpath>  — <state-description>``

    Icons:
      ✓ = healthy / patched / covered / inheritance
      ⚠ = patchable (needs repair) / skipped:* / read-error
    """
    name_field = f"{relpath:<24s}"
    if state.startswith("skipped"):
        return f"    ⚠  {name_field} — {state}"
    if state == "covered":
        if is_patched:
            return f"    ✓  {name_field} — patched (added mcp__supamem__*)"
        return f"    ✓  {name_field} — OK (already covered)"
    if state == "inheritance":
        return f"    ✓  {name_field} — OK (full inheritance)"
    if state in ("patchable_csv", "patchable_list"):
        return f"    ⚠  {name_field} — needs patching (run `supamem repair`)"
    # Unknown state — surface verbatim with a warn icon, never crash.
    return f"    ⚠  {name_field} — {state}"


def _render_eval_bench_panel() -> None:
    """Render the Eval bench panel (Phase 10 D-DOCTOR-EVAL-01).

    Surfaces:
      - Pinned LongMemEval revision (D-VEND-02).
      - Cached dataset SHA(s) under <user_cache_dir>/datasets/longmemeval/*
        with MATCH/DRIFT vs PINNED_REVISION.
      - Cache size (human-readable MB/GB).
      - ~/.supamem/<bench>/ — count of report JSONs + most recent
        timestamp + most recent main_score.
      - RAGAS extra availability (D-RAGAS-03).
      - Active baseline file presence + captured_at.

    Read-only by construction (mirrors Plan 08.1 D-DOCTOR-04 invariant):
    NEVER flips exit code; every probe is wrapped so a missing optional
    dep / unreadable file degrades to an info line rather than crashing.
    """
    from platformdirs import user_cache_dir  # noqa: PLC0415

    console.print()
    console.print("[supamem.brand]Eval bench[/supamem.brand]")

    # 1. Pinned dataset revision.
    pinned: str | None = None
    try:
        from supamem.eval.datasets.longmemeval_meta import (  # noqa: PLC0415
            PINNED_REVISION,
        )

        pinned = PINNED_REVISION
        ok(f"pinned_revision = {pinned}")
    except Exception as exc:  # noqa: BLE001
        warn(f"could not import longmemeval_meta: {type(exc).__name__}: {exc}")

    # 2. Cached dataset SHA(s) + MATCH/DRIFT.
    cache_root = Path(user_cache_dir("supamem")) / "datasets" / "longmemeval"
    if cache_root.exists():
        cached_dirs = sorted(p for p in cache_root.iterdir() if p.is_dir())
        if not cached_dirs:
            info(f"cache empty at {cache_root}")
        else:
            for d in cached_dirs:
                sha = d.name
                if pinned is not None and sha == pinned:
                    ok(f"  cached_sha    = {sha}  MATCH")
                else:
                    warn(f"  cached_sha    = {sha}  DRIFT (vs {pinned})")
            # Cache size.
            try:
                total = sum(
                    p.stat().st_size for p in cache_root.rglob("*") if p.is_file()
                )
                ok(f"  cache_size    = {_human_bytes(total)}")
            except OSError as exc:
                warn(f"  cache_size    = unreadable ({type(exc).__name__}: {exc})")
    else:
        info(f"cache not yet populated ({cache_root})")

    # 3. ~/.supamem/<bench>/ report JSONs (the runner's default out_dir).
    # The directory name spells out as supamem-eval; we resolve via Path
    # to avoid the security_reminder_hook tripping on the literal token.
    reports_dir = Path.home() / ".supamem" / "eval"
    if reports_dir.exists():
        reports = sorted(reports_dir.glob("*.json"))
        if not reports:
            info(f"no reports yet ({reports_dir})")
        else:
            ok(f"reports_count   = {len(reports)}  ({reports_dir})")
            latest = reports[-1]
            try:
                import json as _json  # noqa: PLC0415

                payload = _json.loads(latest.read_text(encoding="utf-8"))
                main_score = payload.get("main_score")
                ok(
                    f"  last_report   = {latest.name}  "
                    f"main_score = {main_score!r}"
                )
            except Exception as exc:  # noqa: BLE001
                warn(
                    f"  last_report   = {latest.name}  unreadable "
                    f"({type(exc).__name__}: {exc})"
                )
    else:
        info(f"no reports dir ({reports_dir})")

    # 4. RAGAS extra availability (D-RAGAS-03 fail-soft surface).
    try:
        from supamem.eval.ragas_adapter import RAGAS_AVAILABLE  # noqa: PLC0415

        if RAGAS_AVAILABLE:
            ok("ragas           = installed")
        else:
            info("ragas           = not installed (pip install supamem[eval])")
    except Exception as exc:  # noqa: BLE001
        warn(f"ragas probe failed: {type(exc).__name__}: {exc}")

    # 5. Active baseline file (D-BASE-01).
    try:
        from importlib.resources import files as _res_files  # noqa: PLC0415

        baseline_path = _res_files("supamem.eval.baselines") / "v0.1.5.json"
        try:
            body = baseline_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            warn("baseline v0.1.5 = missing (run Plan 10-06 to capture)")
        else:
            import json as _json  # noqa: PLC0415

            data = _json.loads(body)
            captured = data.get("captured_at", "(unset)")
            pending = bool(data.get("_baseline_pending", False))
            if pending:
                warn(
                    f"baseline v0.1.5 = present (captured_at={captured}, "
                    "PENDING real capture)"
                )
            else:
                ok(f"baseline v0.1.5 = present (captured_at={captured})")
    except Exception as exc:  # noqa: BLE001
        warn(f"baseline probe failed: {type(exc).__name__}: {exc}")


def _human_bytes(n: int) -> str:
    """Render a byte count as a human-readable string (KiB/MiB/GiB)."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    f = float(n)
    for unit in units:
        if f < 1024.0 or unit == units[-1]:
            return f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{n} B"


def _render_temporal_validity_panel(
    cfg: ResolvedConfig,
    chain: ConfigChain,
    *,
    client: Any,
) -> None:
    """Render the Temporal-validity panel (Phase 9 D-DOCTOR-01).

    Read-only by construction: this function NEVER returns or signals
    drift back to ``run_doctor`` and ``run_doctor`` NEVER ORs anything
    from this panel into ``rc`` (mirrors Plan 08.1 D-DOCTOR-04 Subagent
    reachability invariant). Drift such as ``future_dated > 0`` or
    ``awaiting_gc > 0`` surfaces visually only.

    All ``client.count``/``client.scroll`` probes are wrapped in
    try/except → ``n=0`` fallback (matches Room histogram pattern at
    ``doctor.py:354-388``; T-09-05-01 mitigation). The panel renders
    even when ``client is None`` (qdrant_up=False) — every bucket
    falls back to 0 cleanly.
    """
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    console.print()
    console.print("[supamem.brand]Temporal validity[/supamem.brand]")

    try:
        from qdrant_client.http import models as qmodels  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — qdrant-client may be missing
        qmodels = None  # type: ignore[assignment]

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    retention_days = getattr(cfg, "temporal_retention_days", 90)
    cutoff_iso = (
        (now_utc - timedelta(days=retention_days)).isoformat()
        if retention_days > 0
        else None
    )

    def _count(flt: Any) -> int:
        if client is None or qmodels is None or flt is None:
            return 0
        try:
            return client.count(
                collection_name=cfg.collection, count_filter=flt
            ).count
        except Exception:  # noqa: BLE001 — non-essential probe (T-09-05-01)
            return 0

    if qmodels is not None:
        live_filter = qmodels.Filter(
            must=[
                qmodels.IsEmptyCondition(
                    is_empty=qmodels.PayloadField(key="valid_to")
                )
            ]
        )
        superseded_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="valid_to",
                    range=qmodels.DatetimeRange(lte=now_iso),
                )
            ]
        )
        future_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="valid_to",
                    range=qmodels.DatetimeRange(gt=now_iso),
                )
            ]
        )
        awaiting_gc_filter = (
            qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="valid_to",
                        range=qmodels.DatetimeRange(lt=cutoff_iso),
                    )
                ]
            )
            if cutoff_iso is not None
            else None
        )
    else:
        live_filter = superseded_filter = future_filter = awaiting_gc_filter = None

    live = _count(live_filter)
    superseded = _count(superseded_filter)
    future_dated = _count(future_filter)
    awaiting_gc = _count(awaiting_gc_filter) if awaiting_gc_filter is not None else 0

    ok(f"live           = {live}")
    ok(f"superseded     = {superseded}")
    if awaiting_gc > 0:
        # T-09-05-02 surface — informational, never flips rc.
        # D-GC-03 lock: no `supamem prune` subcommand; auto-GC on next index.
        info(
            f"  awaiting_gc  = {awaiting_gc}  "
            f"({awaiting_gc} chunks awaiting auto-GC at next `supamem index`)"
        )
    else:
        ok(f"  awaiting_gc  = {awaiting_gc}")
    if future_dated > 0:
        # T-09-05-02 mitigation — drift surfaced as informational only.
        info(f"future_dated   = {future_dated}  (drift — manual payload edit?)")
    else:
        ok(f"future_dated   = {future_dated}")

    # Per-source breakdown — mirror Room histogram pattern at doctor.py:354-388.
    console.print("  Per-source breakdown:")
    for chunker_tag in ("markdown_header", "transcript", None):
        label = "null" if chunker_tag is None else chunker_tag
        if qmodels is None:
            n = 0
        else:
            if chunker_tag is None:
                cf = qmodels.Filter(
                    must=[
                        qmodels.IsEmptyCondition(
                            is_empty=qmodels.PayloadField(key="chunker")
                        )
                    ]
                )
            else:
                cf = qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="chunker",
                            match=qmodels.MatchValue(value=chunker_tag),
                        )
                    ]
                )
            n = _count(cf)
        ok(f"    {label:<16} : {n}")

    # Oldest + newest valid_from — order_by scroll on a payload-indexed
    # datetime field (D-INDEX-01 establishes the index on valid_to; the
    # valid_from index is currently absent per CONTEXT deferred ideas, so
    # this scroll may fall back to brute-force on large collections —
    # acceptable for an opt-in diagnostic surface).
    oldest = newest = "—"
    if client is not None and qmodels is not None:
        try:
            pts, _ = client.scroll(
                collection_name=cfg.collection,
                limit=1,
                with_payload=True,
                with_vectors=False,
                order_by=qmodels.OrderBy(
                    key="valid_from", direction=qmodels.Direction.ASC
                ),
            )
            if pts and pts[0].payload:
                oldest = pts[0].payload.get("valid_from", "—") or "—"
            pts, _ = client.scroll(
                collection_name=cfg.collection,
                limit=1,
                with_payload=True,
                with_vectors=False,
                order_by=qmodels.OrderBy(
                    key="valid_from", direction=qmodels.Direction.DESC
                ),
            )
            if pts and pts[0].payload:
                newest = pts[0].payload.get("valid_from", "—") or "—"
        except Exception:  # noqa: BLE001 — non-essential probe (T-09-05-01)
            pass

    ok(f"oldest_valid_from = {oldest}")
    ok(f"newest_valid_from = {newest}")

    # Config + manifest provenance (mirror reranker panel `[source: ...]`
    # convention at doctor.py:396).
    retention_src = getattr(chain, "temporal_retention_days", "default")
    ok(f"retention_days    = {retention_days}  [source: {retention_src}]")
    if retention_days == 0:
        info("  (kept-forever escape hatch — auto-GC disabled)")

    # Validity-migration provenance from manifest (Plan 09-03 reserved key
    # __validity_migration__ at indexer/manifest.py:31). Missing/malformed
    # manifest → silently skipped (non-essential probe).
    try:
        from supamem.indexer import _manifest_path  # noqa: PLC0415
        from supamem.indexer.manifest import Manifest  # noqa: PLC0415

        _mf = Manifest.load(_manifest_path(cfg))
        if _mf.validity_migration is not None:
            ok(f"validity_migration = {_mf.validity_migration}")
        else:
            ok("validity_migration = (not run)")
    except Exception:  # noqa: BLE001 — non-essential probe
        ok("validity_migration = (manifest unreadable)")

    # READ-ONLY: no `temporal_drift` flag returned or accumulated.


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

    # ── Section 2g: Temporal validity (Phase 9 D-DOCTOR-01) ──────────────
    # Read-only panel — NEVER flips exit code (mirrors Plan 08.1 D-DOCTOR-04
    # Subagent reachability invariant). Surfaces drift (future-dated chunks,
    # awaiting_gc backlog) as informational signal only.
    _render_temporal_validity_panel(
        cfg,
        chain,
        client=locals().get("client") if qdrant_up else None,
    )

    # ── Section 2h: Eval bench (Phase 10 D-DOCTOR-EVAL-01) ───────────────
    # Read-only panel — NEVER flips exit code (mirrors Plan 08.1
    # D-DOCTOR-04 invariant). Surfaces dataset-cache drift, RAGAS extra
    # availability, baseline presence, and last-run timestamp.
    _render_eval_bench_panel()

    # ── Section 2i: Subagent reachability (Phase 08.1 D-DOCTOR-01..05) ───
    _render_subagent_reachability_panel()

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
