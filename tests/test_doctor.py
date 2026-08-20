"""Tests for ``supamem.doctor.run_doctor`` (Plan 80.6-11)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_doctor_redacts_api_key_by_default(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The literal QDRANT_API_KEY value must NOT appear in stdout."""
    secret = "sk-prod-secret-12345"
    monkeypatch.setenv("QDRANT_API_KEY", secret)

    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    rc = mod.run_doctor()
    out = capsys.readouterr().out + capsys.readouterr().err
    assert secret not in out
    # Exit non-zero because Qdrant is unreachable in the test env.
    assert rc == 1


def test_doctor_exits_1_on_qdrant_unreachable(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    rc = mod.run_doctor()
    assert rc == 1


def test_doctor_exits_1_on_version_drift(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed-block fence with an old version triggers drift + exit 1."""
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    # Skip the qdrant client path (collection check) cleanly.
    monkeypatch.setattr(
        mod, "_collection_health", lambda client, name: {"present": True, "sparse": True}
    )

    class _FakeClient:
        def get_collection(self, *_a, **_kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}
            return _Info()

    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda *a, **kw: _FakeClient(),
        raising=False,
    )

    # Plant an old-version managed block in CLAUDE.md.
    claude_md = home / "CLAUDE.md"
    claude_md.write_text(
        "# BEGIN SUPAMEM v0.0.1 MANAGED BLOCK — DO NOT EDIT\n"
        "@~/.supamem/share/rules/dual-memory.md\n"
        "# END SUPAMEM v0.0.1 MANAGED BLOCK\n",
        encoding="utf-8",
    )

    rc = mod.run_doctor()
    assert rc == 1


def test_doctor_prints_each_config_field_with_source(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    mod.run_doctor()
    out = capsys.readouterr().out
    assert "[source: default]" in out
    assert "qdrant_url" in out
    assert "collection" in out


def test_doctor_shows_transcript_config(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Plan 06-04 Task 01: ``supamem doctor`` surfaces all 6 transcript keys (D-31)."""
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    mod.run_doctor()
    out = capsys.readouterr().out
    assert "Transcript config" in out
    for key in (
        "default_root",
        "since_days",
        "tool_payload_max_chars",
        "chunk_soft_max_tokens",
        "include_paths_glob",
        "exclude_paths_glob",
    ):
        assert key in out, f"expected {key!r} in doctor output"
    assert "[source: default]" in out


# ───── Plan 07-02 — Classifier rooms + Room histogram (D-07, D-16) ───────


def test_doctor_shows_classifier_rooms_and_hash(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``supamem doctor`` surfaces [classifier.rooms] config + classifier_hash (D-16)."""
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    mod.run_doctor()
    out = capsys.readouterr().out
    assert "Classifier rooms" in out
    # Default rooms must each appear with their keyword list
    for room in ("tests", "backend", "frontend", "docs"):
        assert room in out, f"expected default room {room!r} in doctor output"
    # classifier_hash line: '(none)' when manifest is absent in test env
    assert "classifier_hash" in out


def test_doctor_shows_room_histogram_with_null_bucket(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Histogram MUST include a 'null' bucket (D-07) and tolerate Qdrant errors."""
    import supamem.doctor as mod

    # Probe says reachable so the histogram path runs; client construction
    # below raises so the count() try/except path falls back to 0.
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        mod, "_collection_health", lambda client, name: {"present": True, "sparse": True}
    )

    class _BoomClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def get_collection(self, *a, **kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}

            return _Info()

        def count(self, *a, **kw):
            raise RuntimeError("qdrant boom")

    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda *a, **kw: _BoomClient(), raising=False
    )

    mod.run_doctor()
    out = capsys.readouterr().out
    assert "Room histogram" in out
    # null bucket label must always appear (D-07)
    assert "null" in out
    # Counts default to 0 when Qdrant raises (T-07-02-04 mitigation)
    # At least the null line should show ': 0'
    assert ": 0" in out


def test_doctor_no_drift_no_qdrant_means_exit_1(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if no clients are installed, Qdrant unreachable still triggers exit 1."""
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    rc = mod.run_doctor()
    assert rc == 1


# ───── Plan 08-03 — Reranker panel + partial-download warning (D-DOCTOR-01/02) ─


def _seed_healthy_manifest(cache_dir: Path, model_id: str) -> Path:
    """Create a snapshot dir with two small files + a matching manifest."""
    import json as _json
    slug = model_id.replace("/", "--")
    snap = cache_dir / "models" / f"models--{slug}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    f1 = snap / "config.json"
    f1.write_text('{"k":"v"}', encoding="utf-8")
    f2 = snap / "model.safetensors"
    f2.write_bytes(b"\x00" * 64)
    manifest = {
        "files": {
            "config.json": f1.stat().st_size,
            "model.safetensors": f2.stat().st_size,
        },
        "total_bytes": f1.stat().st_size + f2.stat().st_size,
        "schema": 1,
    }
    (snap / "_expected_manifest.json").write_text(_json.dumps(manifest, indent=2))
    return snap


def test_doctor_reranker_panel_healthy(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-DOCTOR-01: panel surfaces name, model_id, cache_path, size, device."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    _seed_healthy_manifest(cache, "mixedbread-ai/mxbai-rerank-base-v2")

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    rc = mod.run_doctor()
    out = capsys.readouterr().out

    assert "Reranker" in out, "Reranker panel header missing"
    assert "mxbai_v2" in out, "active reranker name missing"
    assert "mixedbread-ai/mxbai-rerank-base-v2" in out, "model_id missing"
    assert "cache_path" in out
    assert "device" in out
    # No partial-download → reranker_drift must NOT bump rc on its own.
    # rc==1 is from qdrant_unreachable in this test, which is fine.
    assert rc == 1  # qdrant unreachable, not reranker drift


def test_doctor_reranker_panel_partial_download(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-DOCTOR-02: deleting half the model files surfaces warn + names ``supamem repair`` + rc=1.

    Test pins ``qdrant_up=True`` and a present collection so any rc=1 is
    UNAMBIGUOUSLY attributable to the new ``reranker_drift`` accumulator
    (B2 fix: drift contributes to the existing rc expression on line ~295).
    """
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    snap = _seed_healthy_manifest(cache, "mixedbread-ai/mxbai-rerank-base-v2")
    # Delete half the files (drop model.safetensors → ~99% of bytes gone).
    (snap / "model.safetensors").unlink()

    # Pin Qdrant up + collection present so drift attribution is unambiguous.
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        mod, "_collection_health",
        lambda client, name: {"present": True, "sparse": True},
    )

    class _OkClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def get_collection(self, *a, **kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}
            return _Info()

        def count(self, *a, **kw):
            class _C:
                count = 0
            return _C()

    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda *a, **kw: _OkClient(), raising=False
    )

    rc = mod.run_doctor()
    out = capsys.readouterr().out

    assert "Reranker" in out
    assert "supamem repair" in out, "warn must name `supamem repair` for actionability"
    assert rc == 1, (
        "partial-download MUST contribute to rc accumulator (B2 fix)"
    )


def test_doctor_reranker_p50_p95_verifiable(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """W3 fix: p50/p95 doctor field MUST be verifiable.

    Pre-load N=20 latency samples; assert ONE of:
    - **Deque path:** printed ``rerank_p50_ms`` matches ``statistics.median(samples)``
      to within 0.5 ms.
    - **Welford-mean path:** printed line carries the literal substring ``"approx"``
      so users know the value is mean-derived, not a true percentile.
    """
    import statistics
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    _seed_healthy_manifest(cache, "mixedbread-ai/mxbai-rerank-base-v2")

    samples = [10.0 * (i + 1) for i in range(20)]  # 10, 20, ..., 200
    expected_p50 = statistics.median(samples)  # 105.0

    # Inject samples through whichever telemetry surface the doctor panel reads.
    # Try the deque path first (preferred per Plan 08-03 Step 2b.i); fall back
    # to seeding the Welford aggregate file.
    from supamem.stats import counter as counter_mod
    deque_seeded = False
    if hasattr(counter_mod, "_LATENCY_DEQUES") and hasattr(
        counter_mod, "get_latency_samples"
    ):
        # Deque path — clear + seed.
        d = counter_mod._LATENCY_DEQUES[("rerank", "rerank_latency_ms")]
        d.clear()
        for s in samples:
            d.append(s)
        deque_seeded = True
    else:
        # Welford-mean path — write aggregate file directly.
        import json as _json
        agg_path = Path.home() / ".cache" / "supamem" / "aggregates.json"
        agg_path.parent.mkdir(parents=True, exist_ok=True)
        n = len(samples)
        s_sum = sum(samples)
        s_sumsq = sum(v * v for v in samples)
        agg_path.write_text(_json.dumps({
            "rerank:rerank_latency_ms": {
                "sum": s_sum, "sumsq": s_sumsq, "count": n,
                "min": min(samples), "max": max(samples),
            }
        }))

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    mod.run_doctor()
    out = capsys.readouterr().out

    assert "rerank" in out.lower(), "rerank latency line must appear in panel"

    if deque_seeded:
        # Deque path → printed p50 must match statistics.median to 0.5 ms.
        # Locate the rerank_p50_ms value in output.
        import re
        m = re.search(r"rerank_p50_ms\s*[=≈]?\s*([\d.]+)", out)
        assert m, f"could not parse rerank_p50_ms from output:\n{out}"
        printed = float(m.group(1))
        assert abs(printed - expected_p50) <= 0.5, (
            f"p50 drift: printed {printed} vs expected {expected_p50}"
        )
    else:
        # Welford path → must carry literal "approx" so users know it's not a true percentile.
        assert "approx" in out, (
            "Welford-mean path MUST label the line with literal 'approx' "
            "so users know the value is mean-derived (W3 verifiability)"
        )


# ───── Plan 08.1-05 — Subagent reachability panel (D-DOCTOR-01..05) ──────


CSV_PATCHABLE_AGENT = (
    "---\n"
    "name: csv-patchable\n"
    "description: restrictive whitelist, no supamem coverage\n"
    "tools: Read, Bash, Grep, mcp__context7__*\n"
    "---\n"
    "\n"
    "body\n"
)

CSV_COVERED_AGENT = (
    "---\n"
    "name: covered\n"
    "tools: Read, Bash, mcp__supamem__*\n"
    "---\n"
    "\n"
    "body\n"
)

INHERITANCE_AGENT = (
    "---\n"
    "name: helper-readonly\n"
    "description: full inheritance — no tools key\n"
    "---\n"
    "\n"
    "body\n"
)

MALFORMED_AGENT = (
    "---\n"
    "name: broken\n"
    "tools: [unclosed\n"
    "---\n"
    "\n"
    "body\n"
)


def _seed_agent(home: Path, name: str, body: str) -> Path:
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    p = agents / name
    p.write_text(body, encoding="utf-8")
    return p


def _seed_manifest(cache: Path, entries: list[dict]) -> Path:
    import json as _json

    cache.mkdir(parents=True, exist_ok=True)
    mp = cache / "agent_patches.json"
    mp.write_text(
        _json.dumps(
            {"schema_version": 1, "supamem_version": "0.0.0+test", "patches": entries},
            indent=2,
        ),
        encoding="utf-8",
    )
    return mp


def test_doctor_subagent_reachability_panel_present(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-DOCTOR-01..03: panel header + per-agent rows for patched / covered /
    inheritance / skipped fixtures, grouped under [global]."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    # Seed 4 fixtures: 1 patched (covered + manifest entry), 1 covered-only,
    # 1 inheritance, 1 malformed.
    patched_path = _seed_agent(home, "patched.md", CSV_COVERED_AGENT)
    _seed_agent(home, "covered.md", CSV_COVERED_AGENT)
    _seed_agent(home, "helper-readonly.md", INHERITANCE_AGENT)
    _seed_agent(home, "broken.md", MALFORMED_AGENT)

    # Manifest records `patched.md` so its row gets the "patched" wording.
    _seed_manifest(
        cache,
        [
            {
                "path": str(patched_path),
                "scope": "global",
                "patched_at": "2026-05-02T00:00:00Z",
                "supamem_version": "0.0.0+test",
                "original_frontmatter": "---\nname: patched\n---\n",
                "original_frontmatter_sha256": "deadbeef",
                "patched_frontmatter_sha256": "cafebabe",
                "tools_form": "csv",
            }
        ],
    )

    rc = mod.run_doctor()
    out = capsys.readouterr().out

    assert "Subagent reachability" in out, out
    assert "[global]" in out, out
    assert "patched (added mcp__supamem__*)" in out, out
    assert "OK (already covered)" in out, out
    assert "OK (full inheritance)" in out, out
    # Skipped row for the malformed agent
    assert "skipped:" in out, out
    # rc is 1 because qdrant unreachable, NOT because of skipped rows
    assert rc == 1


def test_doctor_subagent_reachability_no_manifest_shows_repair_hint(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-DOCTOR-05: a patchable file present + no manifest → repair hint."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    _seed_agent(home, "csv-patchable.md", CSV_PATCHABLE_AGENT)

    mod.run_doctor()
    out = capsys.readouterr().out

    assert "Subagent reachability" in out, out
    assert "needs patching" in out, out
    assert "supamem repair" in out, out
    # No unpatch reminder when manifest absent
    assert "supamem unpatch-agents" not in out, out


def test_doctor_subagent_reachability_does_not_change_exit_code(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-DOCTOR-04: a `skipped:` row MUST NOT flip GREEN → YELLOW.

    Compare the exit code from a tmp HOME with a malformed agent against
    the exit code from a tmp HOME with no agents at all. Both rest of the
    environment (qdrant unreachable, no clients) is identical, so any
    delta would isolate the new panel as the cause.
    """
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    # Baseline: empty home, no agents.
    rc_baseline = mod.run_doctor()

    # Now seed a malformed agent and re-run.
    _seed_agent(home, "broken.md", MALFORMED_AGENT)
    rc_with_skipped = mod.run_doctor()

    assert rc_with_skipped == rc_baseline, (
        f"skipped: row should not change exit code "
        f"(baseline={rc_baseline}, with_skipped={rc_with_skipped})"
    )


def test_doctor_renders_unpatch_reminder_when_manifest_present(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-UNDO-01 REVISED: when manifest exists, the panel reminds users to
    run ``supamem unpatch-agents`` before ``pip uninstall supamem``."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    p = _seed_agent(home, "covered.md", CSV_COVERED_AGENT)
    _seed_manifest(
        cache,
        [
            {
                "path": str(p),
                "scope": "global",
                "patched_at": "2026-05-02T00:00:00Z",
                "supamem_version": "0.0.0+test",
                "original_frontmatter": "---\nname: covered\n---\n",
                "original_frontmatter_sha256": "deadbeef",
                "patched_frontmatter_sha256": "cafebabe",
                "tools_form": "csv",
            }
        ],
    )

    mod.run_doctor()
    out = capsys.readouterr().out
    # Rich autodetects terminal width when capsys captures stdout, so the
    # reminder line can wrap across physical rows. Collapse whitespace so
    # substring assertions match the logical message regardless of width.
    flat = " ".join(out.split())

    assert "supamem unpatch-agents" in flat, out
    assert "pip uninstall supamem" in flat, out
    assert "manifest:" in flat, out


def test_doctor_handles_empty_global_dir(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No ``~/.claude/agents/`` directory at all → header renders, no rows,
    no traceback, no `[global]` line, no repair hint."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))
    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)

    rc = mod.run_doctor()
    out = capsys.readouterr().out
    err = capsys.readouterr().err

    assert "Subagent reachability" in out, out
    assert "[global]" not in out, out
    assert "[project]" not in out, out
    assert "Traceback" not in (out + err)
    # Repair hint only renders when there is a patchable file on disk.
    assert "run `supamem repair` to patch" not in out, out
    # rc is 1 because qdrant unreachable; doctor itself didn't crash.
    assert rc == 1


# ───── Plan 09-05 — Temporal-validity panel (D-DOCTOR-01) ────────────────


def test_doctor_temporal_panel_renders_when_qdrant_unreachable(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-DOCTOR-01: panel renders header + buckets + provenance even with no Qdrant.

    Qdrant unreachable → every count probe falls back to 0 (mirrors Room
    histogram T-07-02-04 fail-soft). Panel must still surface its labels
    and the retention_days [source: ...] provenance line.
    """
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    rc = mod.run_doctor()
    out = capsys.readouterr().out

    # Header.
    assert "Temporal validity" in out
    # Four count-buckets must all label, even when n=0 fallback fires.
    for label in ("live", "superseded", "awaiting_gc", "future_dated"):
        assert label in out, f"expected bucket {label!r} in output, got: {out!r}"
    # Per-source breakdown labels.
    assert "Per-source breakdown" in out
    for src in ("markdown_header", "transcript", "null"):
        assert src in out, f"expected per-source {src!r} in output, got: {out!r}"
    # Oldest / newest valid_from rows.
    assert "oldest_valid_from" in out
    assert "newest_valid_from" in out
    # Config provenance line (mirrors reranker [source: ...] convention).
    assert "retention_days" in out
    assert "[source:" in out
    # Validity-migration provenance row (manifest absent in tmp HOME → "(not run)"
    # or "(manifest unreadable)" — both are valid surfaces).
    assert "validity_migration" in out
    # rc==1 attributable to qdrant unreachable, NOT to the temporal panel.
    assert rc == 1


def test_doctor_temporal_panel_read_only_never_flips_rc(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-DOCTOR-01 + Plan 08.1 D-DOCTOR-04 mirror: panel never flips exit code.

    Compare two doctor runs in identical environments where the ONLY
    difference is what the temporal panel observes:

    - Baseline:    every count probe returns 0 (no drift surface).
    - With-drift:  every count probe returns 7 (worst-case drift —
                   future_dated > 0 AND awaiting_gc > 0).

    Both runs share the same qdrant up/collection-present/reranker
    state, so any rc delta isolates the temporal panel as the cause.
    Mirrors ``test_doctor_subagent_reachability_does_not_change_exit_code``
    (Plan 08.1 D-DOCTOR-04) baseline-vs-drift comparison shape.
    """
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        mod,
        "_collection_health",
        lambda client, name: {"present": True, "sparse": True},
    )

    counts_holder = {"value": 0}

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        def get_collection(self, *a, **kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}

            return _Info()

        def count(self, *a, **kw):
            class _C:
                count = counts_holder["value"]

            return _C()

        def scroll(self, *a, **kw):
            return ([], None)

    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda *a, **kw: _Client(),
        raising=False,
    )

    counts_holder["value"] = 0
    rc_baseline = mod.run_doctor()

    counts_holder["value"] = 7  # surfaces awaiting_gc + future_dated drift
    rc_with_drift = mod.run_doctor()

    assert rc_with_drift == rc_baseline, (
        "Temporal-validity panel MUST be read-only "
        "(D-DOCTOR-01 + Plan 08.1 D-DOCTOR-04 mirror): "
        f"baseline={rc_baseline}, with_drift={rc_with_drift}"
    )


def test_doctor_temporal_panel_handles_count_exception(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-09-05-01: client.count() raising falls back to n=0; panel still renders."""
    import supamem.doctor as mod

    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("SUPAMEM_CACHE_DIR", str(cache))

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        mod,
        "_collection_health",
        lambda client, name: {"present": True, "sparse": True},
    )

    class _BoomClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def get_collection(self, *a, **kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}

            return _Info()

        def count(self, *a, **kw):
            raise RuntimeError("qdrant boom — temporal panel must fail-soft")

        def scroll(self, *a, **kw):
            raise RuntimeError("qdrant boom — scroll must also fail-soft")

    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda *a, **kw: _BoomClient(),
        raising=False,
    )

    mod.run_doctor()
    out = capsys.readouterr().out
    err = capsys.readouterr().err

    assert "Temporal validity" in out
    assert "Traceback" not in (out + err)
    # No assertion on rc value — other panels (e.g. reranker model not
    # cached in tmp_path) legitimately bump rc; the contract here is
    # that the temporal panel ITSELF survives a count() exception
    # without crashing or polluting stderr with a traceback.


# ───── Phase 19.1 SM-2b — update-check render gating (stale never ✓) ──────


@pytest.fixture
def uc_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the update-check cache dir → tmp (mirrors test_update_check.py)."""
    import supamem.update_check as uc

    d = tmp_path / "uc"
    monkeypatch.setattr(uc, "_cache_dir", lambda: d)
    return d


@pytest.fixture
def uc_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("SUPAMEM_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"):
        monkeypatch.delenv(v, raising=False)


def _seed_uc_cache(cache_dir: Path, *, age_seconds: float, latest: str) -> None:
    import time as _time

    import supamem.update_check as uc

    uc._write_cache(
        uc.UpdateCacheEntry(
            last_check_ts=_time.time() - age_seconds,
            latest_version=latest,
            etag=None,
        )
    )


def test_doctor_stale_cache_renders_neutral_marker_never_ok(
    home: Path,
    uc_cache: Path,
    uc_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-2b Test 1: stale cache + update_available False → neutral info marker
    naming the cache age and last-seen version; the ✓ on-latest line must NOT
    appear (doctor cannot assert up-to-date from stale data)."""
    import supamem.doctor as mod
    import supamem.update_check as uc
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    # Belt-and-suspenders: no network from this test under any env state.
    monkeypatch.setattr(uc, "_is_suppressed", lambda: True)
    _seed_uc_cache(uc_cache, age_seconds=48 * 3600, latest=__version__)

    mod.run_doctor()
    out = capsys.readouterr().out
    flat = " ".join(out.split())

    assert "cache stale" in flat, flat
    assert "2 days old" in flat, flat
    assert "cannot confirm latest" in flat, flat
    assert f"last seen v{__version__}" in flat, flat
    assert "✓ on latest cached version" not in flat, flat


def test_doctor_fresh_cache_renders_ok_on_latest(
    home: Path,
    uc_cache: Path,
    uc_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-2b Test 2: fresh cache + update_available False → ✓ on-latest line."""
    import supamem.doctor as mod
    import supamem.update_check as uc

    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    monkeypatch.setattr(uc, "_is_suppressed", lambda: True)
    _seed_uc_cache(uc_cache, age_seconds=0.0, latest=__version__)

    mod.run_doctor()
    out = capsys.readouterr().out
    flat = " ".join(out.split())

    assert "✓ on latest cached version" in flat, flat
    assert "cache stale" not in flat, flat


def test_doctor_stale_cache_refreshes_before_render(
    home: Path,
    uc_cache: Path,
    uc_clean: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-2b Test 3: stale cache + mocked successful probe → doctor refreshes
    (bounded, suppression-honored) and renders the fresh comparison result."""
    import supamem.doctor as mod
    import supamem.update_check as uc
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    monkeypatch.setattr(uc, "_is_suppressed", lambda: False)
    monkeypatch.setattr(
        uc, "_probe_github", lambda cur, etag: ("9.9.9", None, False)
    )
    _seed_uc_cache(uc_cache, age_seconds=48 * 3600, latest=__version__)

    mod.run_doctor()
    out = capsys.readouterr().out
    flat = " ".join(out.split())

    assert "update available" in flat, flat
    assert "9.9.9" in flat, flat
    assert "cache stale" not in flat, flat


# ───── Phase 19.1 SM-4d — duplicate managed-block drift ────────────────────


def _pin_green_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin qdrant up + collection present so rc deltas isolate to drift."""
    import supamem.doctor as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    monkeypatch.setattr(
        mod, "_collection_health", lambda client, name: {"present": True, "sparse": True}
    )

    class _FakeClient:
        def get_collection(self, *_a, **_kw):
            class _Info:
                class config:
                    class params:
                        sparse_vectors = {"sparse": object()}

            return _Info()

    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda *a, **kw: _FakeClient(), raising=False
    )


def test_doctor_duplicate_managed_blocks_warn_and_drift(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-4d: two BEGIN markers → warn naming the count + advising repair; the
    target counts as drift (rc 1) even though the first block's version matches."""
    import supamem.doctor as mod
    from supamem import __version__

    _pin_green_qdrant(monkeypatch)
    block = (
        f"# BEGIN SUPAMEM v{__version__} MANAGED BLOCK — DO NOT EDIT\n"
        "@~/.supamem/share/rules/dual-memory.md\n"
        f"# END SUPAMEM v{__version__} MANAGED BLOCK\n"
    )
    (home / "CLAUDE.md").write_text(block + "\n" + block, encoding="utf-8")

    rc = mod.run_doctor()
    out = capsys.readouterr().out
    flat = " ".join(out.split())

    assert "2 managed blocks detected" in flat, flat
    assert "supamem repair" in flat, flat
    assert rc == 1, "duplicate blocks MUST count as drift (existing rc semantics)"


def test_doctor_single_current_block_no_duplicate_warn(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SM-4d regression: single current-version block → no duplicate warn; the
    ✓ current line renders exactly as today."""
    import supamem.doctor as mod
    from supamem import __version__

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    (home / "CLAUDE.md").write_text(
        f"# BEGIN SUPAMEM v{__version__} MANAGED BLOCK — DO NOT EDIT\n"
        "@~/.supamem/share/rules/dual-memory.md\n"
        f"# END SUPAMEM v{__version__} MANAGED BLOCK\n",
        encoding="utf-8",
    )

    mod.run_doctor()
    out = capsys.readouterr().out
    flat = " ".join(out.split())

    assert "managed blocks detected" not in flat, flat
    assert f"v{__version__} (current)" in flat, flat
