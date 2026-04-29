"""Per-project config discovery for supamem (D-38 precedence ladder).

Resolution order (highest to lowest):

1. ``$SUPAMEM_CONFIG`` env var pointing to an explicit TOML file
2. ``<cwd>/.supamem/config.toml`` (the canonical project config)
3. ``<cwd>/pyproject.toml`` ``[tool.supamem]`` section
4. Auto-detect: presence of ``<cwd>/.claude/insights/`` seeds ``sources``
5. Defaults baked into ``ResolvedConfig``

Per-key resolution: each field is resolved independently. A user can put just
``QDRANT_URL`` in env and inherit everything else from a TOML file.

Legacy single-key env vars (compatibility with earlier embed-dev-memories.py):
- ``QDRANT_URL``, ``QDRANT_API_KEY``, ``COLLECTION_NAME``, ``EMBEDDING_MODEL``

Implementation note: lower-precedence rungs are applied FIRST, then higher
rungs unconditionally overwrite. The ``ConfigChain`` record always reflects the
last writer per field, which matches user-visible precedence.

NOTE on secrets: ``ResolvedConfig.qdrant_api_key`` may contain a user secret.
``supamem doctor`` (plan 11) MUST redact this field by default — see STRIDE
T-80.6-03-01.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Source = Literal["env", "supamem_toml", "pyproject", "auto_detect", "default"]


@dataclass
class ResolvedConfig:
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    collection: str = "dev_memory_tuned_hybrid"
    embedder: str = "minilm"
    chunker: str = "markdown_header"
    sources: list[str] = field(default_factory=list)
    chunk_size: int = 200
    fusion: str = "rrf"
    drop_tokens: list[str] = field(default_factory=list)
    goldens_path: str = ""
    cache_dir: str = ""
    allow_legacy_collection: bool = False
    # Regress baselines — Phase 80.1 D-19 defaults; project-tunable for
    # corpora outside the supamem-internal calibration set (added v0.1.2).
    regress_baseline_recall_at_5: float = 0.60
    regress_baseline_total_tokens: int = 4000
    regress_baseline_p95_latency_ms: int = 500


@dataclass
class ConfigChain:
    qdrant_url: Source = "default"
    qdrant_api_key: Source = "default"
    collection: Source = "default"
    embedder: Source = "default"
    chunker: Source = "default"
    sources: Source = "default"
    chunk_size: Source = "default"
    fusion: Source = "default"
    drop_tokens: Source = "default"
    goldens_path: Source = "default"
    cache_dir: Source = "default"
    regress_baseline_recall_at_5: Source = "default"
    regress_baseline_total_tokens: Source = "default"
    regress_baseline_p95_latency_ms: Source = "default"


_LEGACY_ENV: dict[str, str] = {
    "QDRANT_URL": "qdrant_url",
    "QDRANT_API_KEY": "qdrant_api_key",
    "COLLECTION_NAME": "collection",
    "EMBEDDING_MODEL": "embedder",
}

_NESTED_TABLES: list[tuple[str, dict[str, str]]] = [
    ("hook", {"drop_tokens": "drop_tokens"}),
    (
        "eval",
        {
            "goldens_path": "goldens_path",
            "baseline_recall_at_5": "regress_baseline_recall_at_5",
            "baseline_total_tokens": "regress_baseline_total_tokens",
            "baseline_p95_latency_ms": "regress_baseline_p95_latency_ms",
        },
    ),
    ("cache", {"cache_dir": "cache_dir"}),
]


def _load_toml(p: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"supamem: failed to parse {p}: {exc}") from exc


def _apply_section(
    cfg: ResolvedConfig,
    chain: ConfigChain,
    section: dict[str, Any],
    source: Source,
) -> None:
    """Apply flat field overrides from a [supamem] / [tool.supamem] section."""
    for key, value in section.items():
        if hasattr(cfg, key) and not isinstance(getattr(cfg, key), dict):
            # skip nested tables (hook/eval/cache) — handled by _apply_nested
            if key in {sub for sub, _ in _NESTED_TABLES}:
                continue
            setattr(cfg, key, value)
            setattr(chain, key, source)


def _apply_nested(
    cfg: ResolvedConfig,
    chain: ConfigChain,
    section: dict[str, Any],
    source: Source,
) -> None:
    """Apply nested [supamem.hook], [supamem.eval], [supamem.cache] tables."""
    for sub_key, field_map in _NESTED_TABLES:
        sub = section.get(sub_key, {})
        if not isinstance(sub, dict):
            continue
        for src_key, dst_field in field_map.items():
            if src_key in sub:
                setattr(cfg, dst_field, sub[src_key])
                setattr(chain, dst_field, source)


def load_config(cwd: Path | None = None) -> tuple[ResolvedConfig, ConfigChain]:
    """Resolve the supamem config for ``cwd`` (defaults to ``Path.cwd()``)."""
    cwd = cwd or Path.cwd()
    cfg = ResolvedConfig()
    chain = ConfigChain()

    # ── 4. Auto-detect (lowest applied rung) ──────────────────────────────
    if (cwd / ".claude" / "insights").exists():
        cfg.sources = [".claude/insights/", ".claude/rules/"]
        chain.sources = "auto_detect"

    # ── 3. pyproject.toml [tool.supamem] ──────────────────────────────────
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        data = _load_toml(pyproject)
        section = data.get("tool", {}).get("supamem", {}) or {}
        _apply_section(cfg, chain, section, "pyproject")
        _apply_nested(cfg, chain, section, "pyproject")

    # ── 2. .supamem/config.toml ───────────────────────────────────────────
    supamem_toml = cwd / ".supamem" / "config.toml"
    if supamem_toml.is_file():
        data = _load_toml(supamem_toml)
        section = data.get("supamem", {}) or {}
        _apply_section(cfg, chain, section, "supamem_toml")
        _apply_nested(cfg, chain, section, "supamem_toml")

    # ── 1a. SUPAMEM_CONFIG explicit path ──────────────────────────────────
    explicit = os.environ.get("SUPAMEM_CONFIG", "").strip()
    if explicit:
        ep = Path(explicit)
        if not ep.is_file():
            raise FileNotFoundError(
                f"supamem: SUPAMEM_CONFIG points to missing file: {ep}"
            )
        data = _load_toml(ep)
        section = data.get("supamem", {}) or {}
        _apply_section(cfg, chain, section, "env")
        _apply_nested(cfg, chain, section, "env")

    # ── 1b. Legacy single-key env vars (highest precedence) ───────────────
    for env_name, field_name in _LEGACY_ENV.items():
        val = os.environ.get(env_name)
        if val is not None and val != "":
            setattr(cfg, field_name, val)
            setattr(chain, field_name, "env")

    return cfg, chain
