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
