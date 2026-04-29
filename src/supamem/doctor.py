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
    # Plain print so capsys / non-TTY environments capture the full line —
    # rich's soft_wrap still respects the 80-col default in pytest.
    print(format_chain(cfg, chain, redact_secrets=redact_secrets))

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

    # ── Section 4: Exit code ─────────────────────────────────────────────
    rc = 0
    if not qdrant_up or any_drift or (qdrant_up and not coll_status.get("present")):
        rc = 1
    console.print()
    if rc == 0:
        ok("doctor: all green")
    else:
        warn(f"doctor: issues detected (exit {rc})")
    return rc


__all__ = ["format_chain", "run_doctor", "version_drift_report"]
