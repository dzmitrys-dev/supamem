"""Per-project config discovery for supamem (D-38 precedence ladder).

Resolution order (highest to lowest):

1. ``$SUPAMEM_CONFIG`` env var pointing to an explicit TOML file
2. ``<cwd>/.supamem/config.toml`` (the canonical project config).
   MCP stdio defaults ``cwd`` to ``Path.cwd()``; set ``SUPAMEM_PROJECT_ROOT`` to your
   workspace root when the host launches the server outside the repo (Cursor/IDE).
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
    # MCP response caps — Phase 5 D-09 / D-10. Three flat fields populated
    # from the two-level [supamem.mcp.caps] TOML table via _NESTED_TABLES.
    mcp_caps_max_top_k: int = 25
    mcp_caps_max_query_chars: int = 250
    mcp_caps_max_preview_chars: int = 200
    # Transcript ingestion — Phase 6 D-30 / D-32. Flat fields populated from
    # the [supamem.transcript] TOML table via _NESTED_TABLES. The default_root
    # is stored byte-stable (unexpanded ~) and resolved by the CLI consumer.
    transcript_default_root: str = "~/.claude/projects/"
    transcript_since_days: int = 180
    transcript_tool_payload_max_chars: int = 2000
    transcript_chunk_soft_max_tokens: int = 600
    transcript_include_paths_glob: list[str] = field(default_factory=list)
    transcript_exclude_paths_glob: list[str] = field(default_factory=list)
    # Phase 7 D-14 / D-15 — coding-path classifier rooms. Defaults ship in
    # D-01a priority order (most-specific-first); first-match-wins by dict
    # insertion order (PEP 468). User TOML at [supamem.classifier.rooms]
    # REPLACES this map (matches transcript_include_paths_glob precedent).
    classifier_rooms: dict[str, list[str]] = field(
        default_factory=lambda: {
            "tests": ["tests", "test", "__tests__", "spec", "specs"],
            "types": ["types", "@types", "typings"],
            "migrations": ["migrations", "alembic", "schema"],
            "config": ["config", "configs", ".github", "ci"],
            "scripts": ["scripts", "bin", "tools"],
            "docs": ["docs", "documentation"],
            "frontend": [
                "frontend",
                "web",
                "client",
                "ui",
                "components",
                "pages",
            ],
            "backend": ["src", "backend", "api", "server", "lib"],
        }
    )
    # Phase 8 D-CONFIG-02 — code-aware reranker plugin selection. Flat
    # fields populated from the [supamem.reranker] TOML table via
    # _NESTED_TABLES; default flips to mxbai_v2 per D-FETCH-02.
    # ``reranker_name = "off"`` restores pre-Phase-8 byte-identical retrieval.
    reranker_name: str = "mxbai_v2"
    reranker_model_id: str = "mixedbread-ai/mxbai-rerank-base-v2"
    reranker_top_n: int = 50
    reranker_prefetch_per_arm: int = 50
    reranker_batch_size: int = 16
    # ── Phase 9 D-CONFIG-01 / D-GC-DEFAULT-01 ─────────────────────────────
    # Per-source recency decay (transcript-only opt-in) + auto-GC retention.
    # Code/ADR/doc rankings invariant under decay flag flips (TEMP-03 lock).
    # Defaults populated from the [supamem.recency.per_source.transcript]
    # and [supamem.temporal] TOML tables via _NESTED_TABLES; boot-time
    # validation gates in load_config() reject out-of-range values
    # (D-CONFIG-02): alpha ∈ [0, 1], half_life_days > 0, retention_days >= 0.
    recency_per_source_transcript_enabled: bool = False
    recency_per_source_transcript_half_life_days: float = 14.0
    recency_per_source_transcript_alpha: float = 0.7
    temporal_retention_days: int = 90  # 0 = kept-forever escape hatch
    # ── Phase 11 (FILT-01) ────────────────────────────────────────────────
    # Backend-level per-hit preview cap for the ``filtered_dense`` retrieval
    # backend. Mapped from ``[supamem.retrieval.filtered_dense] preview_chars``
    # via ``_NESTED_TABLES``. ``0`` disables truncation (preview becomes the
    # full text); positive values cap each hit's preview at N chars with the
    # ellipsis-on-truncate semantics from ``mcp_server.py:227`` (D-PREV-02).
    # Independent of the MCP transport cap (``mcp_caps_max_preview_chars``);
    # both caps act on the SAME RAW INPUT (``h.text``) — never composed (D-PREV-03).
    retrieval_filtered_dense_preview_chars: int = 240
    # ── Phase 17 D-HYDE-04 / D-DOCTOR-04 ──────────────────────────────────
    # Selected retrieval backend name — resolved by ``supamem.retrieval``
    # entry-point dispatch (see ``supamem.retrieval.load_retrieval``).
    # TOML key ``retrieval = "<name>"`` under ``[supamem]`` /
    # ``[tool.supamem]`` is mapped onto this flat field via
    # ``_apply_section`` (collision with the nested ``retrieval.*`` table
    # group is resolved by an explicit alias in ``load_config``). Default
    # ``"tuned_hybrid"`` preserves Phase 16 byte-identical retrieval for
    # opt-out users (T-17-04 carry-lock). Read by ``doctor`` to gate the
    # Ollama warm-pool panel (D-HYDE-04 + D-DOCTOR-04).
    retrieval_name: str = "tuned_hybrid"


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
    mcp_caps_max_top_k: Source = "default"
    mcp_caps_max_query_chars: Source = "default"
    mcp_caps_max_preview_chars: Source = "default"
    transcript_default_root: Source = "default"
    transcript_since_days: Source = "default"
    transcript_tool_payload_max_chars: Source = "default"
    transcript_chunk_soft_max_tokens: Source = "default"
    transcript_include_paths_glob: Source = "default"
    transcript_exclude_paths_glob: Source = "default"
    classifier_rooms: Source = "default"
    reranker_name: Source = "default"
    reranker_model_id: Source = "default"
    reranker_top_n: Source = "default"
    reranker_prefetch_per_arm: Source = "default"
    reranker_batch_size: Source = "default"
    # Phase 9 D-CONFIG-01 / D-GC-DEFAULT-01.
    recency_per_source_transcript_enabled: Source = "default"
    recency_per_source_transcript_half_life_days: Source = "default"
    recency_per_source_transcript_alpha: Source = "default"
    temporal_retention_days: Source = "default"
    # Phase 11 FILT-01.
    retrieval_filtered_dense_preview_chars: Source = "default"
    # Phase 17 D-HYDE-04 / D-DOCTOR-04 — top-level ``retrieval = "..."``
    # selector under ``[supamem]`` / ``[tool.supamem]`` (TOML key ``retrieval``
    # → flat field ``retrieval_name`` via ``_apply_section`` alias).
    retrieval_name: Source = "default"


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
    # Two-level dotted path: [supamem.mcp.caps] → flat mcp_caps_* fields.
    # _apply_nested drills through "mcp" then "caps"; _apply_section skips
    # the FIRST segment ("mcp") in its skip-set to avoid setattr accidents.
    (
        "mcp.caps",
        {
            "max_top_k": "mcp_caps_max_top_k",
            "max_query_chars": "mcp_caps_max_query_chars",
            "max_preview_chars": "mcp_caps_max_preview_chars",
        },
    ),
    # Phase 6 D-30 — [supamem.transcript] table → flat transcript_* fields.
    (
        "transcript",
        {
            "default_root": "transcript_default_root",
            "since_days": "transcript_since_days",
            "tool_payload_max_chars": "transcript_tool_payload_max_chars",
            "chunk_soft_max_tokens": "transcript_chunk_soft_max_tokens",
            "include_paths_glob": "transcript_include_paths_glob",
            "exclude_paths_glob": "transcript_exclude_paths_glob",
        },
    ),
    # Phase 7 D-15 — [supamem.classifier.rooms] table → flat
    # classifier_rooms field. Leaf is dict[str, list[str]]; _apply_nested
    # setattr is type-agnostic (verified RESEARCH R-06).
    ("classifier", {"rooms": "classifier_rooms"}),
    # Phase 8 D-CONFIG-01 / D-CONFIG-03 — flat [supamem.reranker] table
    # → 5 reranker_* fields. ``name = "off"`` is the disable sentinel.
    (
        "reranker",
        {
            "name": "reranker_name",
            "model_id": "reranker_model_id",
            "top_n": "reranker_top_n",
            "prefetch_per_arm": "reranker_prefetch_per_arm",
            "batch_size": "reranker_batch_size",
        },
    ),
    # ── Phase 9 D-CONFIG-01 — [supamem.recency.per_source.transcript] ────
    # Three-level dotted key; _apply_nested .split(".") traversal handles
    # arbitrary depth (verified via existing two-level [supamem.mcp.caps]).
    (
        "recency.per_source.transcript",
        {
            "enabled": "recency_per_source_transcript_enabled",
            "half_life_days": "recency_per_source_transcript_half_life_days",
            "alpha": "recency_per_source_transcript_alpha",
        },
    ),
    # ── Phase 9 D-GC-DEFAULT-01 — [supamem.temporal] ─────────────────────
    (
        "temporal",
        {
            "retention_days": "temporal_retention_days",
        },
    ),
    # ── Phase 11 FILT-01 — [supamem.retrieval.filtered_dense] ────────────
    # Two-level dotted key; _apply_nested .split(".") traversal handles
    # arbitrary depth (verified via existing two-level [supamem.mcp.caps]).
    (
        "retrieval.filtered_dense",
        {
            "preview_chars": "retrieval_filtered_dense_preview_chars",
        },
    ),
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
    """Apply flat field overrides from a [supamem] / [tool.supamem] section.

    Notes on the two filters below:

    * ``isinstance(getattr(cfg, key), dict)`` — already excludes dict-typed
      fields (e.g. ``classifier_rooms``). A user who writes a flat
      ``classifier_rooms = { ... }`` under ``[supamem]`` (instead of the
      canonical nested ``[supamem.classifier.rooms]`` table) has their
      value INTENTIONALLY ignored here; the only supported shape is the
      nested table consumed by ``_apply_nested``. See
      ``test_flat_classifier_rooms_under_supamem_is_ignored``.

    * ``key in skip_first_segments`` — defense-in-depth for a future
      scalar field whose name happens to collide with a nested table's
      first segment (e.g. someone adds a flat ``mcp`` attribute on
      ``ResolvedConfig`` while ``[supamem.mcp.caps]`` already exists).
      Today none of the entries in ``_NESTED_TABLES`` ("hook", "eval",
      "cache", "mcp", "transcript", "classifier") match a non-dict
      attribute on ``ResolvedConfig``, so this branch is unreachable,
      but it stays as a guard against silent setattr accidents on
      future schema additions.
    """
    skip_first_segments = {sub.split(".", 1)[0] for sub, _ in _NESTED_TABLES}
    # Phase 17 D-HYDE-04 — explicit aliases for TOML keys whose first
    # segment collides with a nested table prefix (``retrieval`` collides
    # with the ``retrieval.filtered_dense`` nested group). Scalar value at
    # the colliding top-level key is routed to a renamed flat field; dict
    # values pass through to ``_apply_nested``.
    _SCALAR_ALIASES: dict[str, str] = {"retrieval": "retrieval_name"}
    for key, value in section.items():
        if key in _SCALAR_ALIASES and not isinstance(value, dict):
            dst = _SCALAR_ALIASES[key]
            setattr(cfg, dst, value)
            setattr(chain, dst, source)
            continue
        if hasattr(cfg, key) and not isinstance(getattr(cfg, key), dict):
            if key in skip_first_segments:
                continue
            setattr(cfg, key, value)
            setattr(chain, key, source)


def _apply_nested(
    cfg: ResolvedConfig,
    chain: ConfigChain,
    section: dict[str, Any],
    source: Source,
) -> None:
    """Apply nested [supamem.hook], [supamem.eval], [supamem.cache], [supamem.mcp.caps] tables.

    A ``sub_key`` containing one or more dots (e.g. ``"mcp.caps"``) is drilled
    level-by-level through ``section`` via ``dict.get(part, {})``. Single-level
    keys keep the existing flat behavior. A non-dict at any intermediate level
    falls through harmlessly (defaults remain in effect).
    """
    for sub_key, field_map in _NESTED_TABLES:
        sub: Any = section
        for part in sub_key.split("."):
            if not isinstance(sub, dict):
                sub = {}
                break
            sub = sub.get(part, {})
        if not isinstance(sub, dict):
            continue
        for src_key, dst_field in field_map.items():
            if src_key in sub:
                setattr(cfg, dst_field, sub[src_key])
                setattr(chain, dst_field, source)


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk parents from ``start`` looking for a supamem-aware project root.

    A directory qualifies if it contains either:

    * ``.supamem/config.toml`` (canonical project config), OR
    * ``pyproject.toml`` whose parsed contents include a ``[tool.supamem]`` table.

    Used by ``supamem mcp-server`` when ``SUPAMEM_PROJECT_ROOT`` is unset, so MCP
    hosts that spawn the subprocess from a non-workspace cwd (Cursor, some IDE
    integrations) can still locate the workspace's ``.supamem/config.toml``.

    Stops at the filesystem root or at ``$HOME`` (whichever comes first) to avoid
    scanning above the user's home directory. Returns ``None`` if no marker is
    found. Tradeoff: a parent project further up the tree could be picked up
    accidentally — that is why ``SUPAMEM_PROJECT_ROOT`` remains the preferred,
    explicit mechanism.
    """
    start = (start or Path.cwd()).resolve()
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        home = None

    current = start
    while True:
        if (current / ".supamem" / "config.toml").is_file():
            return current
        pyproject = current / "pyproject.toml"
        if pyproject.is_file():
            try:
                data = _load_toml(pyproject)
            except RuntimeError:
                data = {}
            if isinstance(data.get("tool"), dict) and isinstance(
                data["tool"].get("supamem"), dict
            ):
                return current

        parent = current.parent
        if parent == current:
            return None
        if home is not None and current == home:
            return None
        current = parent


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

    # ── Phase 8 D-CONFIG-03 — reranker_name validation gate ───────────────
    # Unregistered names fail closed at construction time (T-CONFIG-01
    # mitigation): err_console + SystemExit(2). The "off" sentinel is
    # always accepted regardless of registered plugins.
    if cfg.reranker_name != "off":
        from importlib.metadata import entry_points  # noqa: PLC0415

        from supamem.console import err_console  # noqa: PLC0415

        known = {ep.name for ep in entry_points(group="supamem.reranker")}
        if cfg.reranker_name not in known:
            err_console.print(
                f"[supamem.err]config: reranker_name={cfg.reranker_name!r} "
                f"is not a registered supamem.reranker entry-point "
                f"(known: {sorted(known) or '[]'}). "
                f"Set [supamem.reranker] name = 'off' to disable."
            )
            raise SystemExit(2)

    # ── Phase 9 D-CONFIG-02 — Pydantic-style fail-closed validation ──────
    # Boot-time fail-closed: out-of-range numeric config never reaches the
    # decay math or GC sweep. Mitigates T-09-02-01 (tampering via TOML).
    from supamem.console import err_console  # noqa: PLC0415

    if not (0.0 <= cfg.recency_per_source_transcript_alpha <= 1.0):
        err_console.print(
            f"[supamem.err]config: recency.per_source.transcript.alpha="
            f"{cfg.recency_per_source_transcript_alpha} must be in [0.0, 1.0]"
        )
        raise SystemExit(2)
    if cfg.recency_per_source_transcript_half_life_days <= 0:
        err_console.print(
            f"[supamem.err]config: recency.per_source.transcript.half_life_days="
            f"{cfg.recency_per_source_transcript_half_life_days} must be > 0"
        )
        raise SystemExit(2)
    if cfg.temporal_retention_days < 0:
        err_console.print(
            f"[supamem.err]config: temporal.retention_days="
            f"{cfg.temporal_retention_days} must be >= 0 (0 = kept-forever)"
        )
        raise SystemExit(2)
    # Phase 11 FILT-01 D-PREV-01 — preview_chars >= 0; 0 disables truncation.
    if cfg.retrieval_filtered_dense_preview_chars < 0:
        err_console.print(
            f"[supamem.err]config: retrieval.filtered_dense.preview_chars="
            f"{cfg.retrieval_filtered_dense_preview_chars} must be >= 0 "
            "(0 disables truncation, positive values cap each hit's preview)."
        )
        raise SystemExit(2)

    return cfg, chain
