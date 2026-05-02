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
