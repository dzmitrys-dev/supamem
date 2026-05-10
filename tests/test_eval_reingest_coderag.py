"""Plan 17-B2 Task 1 (RED) — --reingest-coderag flag dispatch tests.

Locks the wiring contract for D-WIRE-04..06:
- ``--reingest-coderag`` (default OFF) drops + rebuilds the
  ``supamem_eval_coderag`` collection BEFORE scoring when ON.
- Default OFF is byte-identical to Phase 16 baseline replay (no ingest
  call, no QdrantClient construction in the coderag branch).
- The flag NEVER touches the mem0 peer collection (D-WIRE-04 scope guard).
- Typer flag plumbs through to ``run_bench(reingest_coderag=...)``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _stub_smoke_records(monkeypatch) -> None:
    """Avoid touching the live coderag bench fixture in dispatch tests."""
    import supamem.eval.runner as runner_mod  # noqa: F401  (side-effect: import)


def _make_run_bench_kwargs(**over) -> dict:
    base = {
        "suite": "coderag",
        "regress": False,
        "goldens_path": None,
        "config": None,
        "judge": None,
        "full": True,
        "dataset_path": None,
        "out": None,
        "verbose": False,
        "baseline_version": "v0.1.5",
        "peer": None,
        "ingest_peer": None,
    }
    base.update(over)
    return base


def test_reingest_flag_drops_collection_and_calls_ingest(monkeypatch, tmp_path) -> None:
    """``run_bench(suite='coderag', full=True, reingest_coderag=True)``
    drops ``supamem_eval_coderag`` and invokes ``coderag.ingest.ingest()``
    with a chunker_fn resolved from the entry-point, BEFORE scoring."""
    from supamem.eval import runner as runner_mod

    # Spy on coderag.ingest.ingest
    ingest_calls: list[dict] = []

    def fake_ingest(cfg, records, *, client=None, chunker_fn=None):
        ingest_calls.append({
            "cfg": cfg, "records": list(records),
            "client": client, "chunker_fn": chunker_fn,
        })
        return 7

    from supamem.eval.coderag import ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest", fake_ingest)

    # Patch QdrantClient construction inside runner so no real network.
    fake_client = MagicMock()
    monkeypatch.setattr(
        "supamem.eval.runner.QdrantClient",
        lambda *a, **kw: fake_client,
        raising=False,
    )

    # Stub out the manifest/corpus walk + the auto_queries extractors so we
    # don't touch real on-disk caches.
    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.ensure_populated_manifest",
        lambda _p: {"repos": [{"slug": "demo/repo", "commit_sha": "abc"}]},
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.repo_cache_path",
        lambda slug, sha: tmp_path / slug / sha,
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_pr_queries",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_adr_queries",
        lambda *_a, **_kw: [],
    )

    # Stub the smoke fixture loader so suite_cls.run sees an empty record list.
    monkeypatch.setattr(
        "supamem.eval.suite_loader.load_suite",
        lambda _name: type(
            "FakeSuite", (), {"run": staticmethod(lambda *a, **kw: {"ok": True})}
        ),
    )

    # Capture call order.
    order: list[str] = []
    real_delete = fake_client.delete_collection
    fake_client.delete_collection = lambda name: order.append(f"delete:{name}") or real_delete(name)

    def _ordered_ingest(cfg, records, *, client=None, chunker_fn=None):
        order.append("ingest")
        return fake_ingest(cfg, records, client=client, chunker_fn=chunker_fn)
    monkeypatch.setattr(ingest_mod, "ingest", _ordered_ingest)

    # Patch _build_backend to avoid touching Qdrant.
    monkeypatch.setattr(
        runner_mod, "_build_backend",
        lambda cfg, *, suite=None: (order.append("backend") or MagicMock()),
    )

    rc = runner_mod.run_bench(**_make_run_bench_kwargs(reingest_coderag=True))
    assert rc == 0
    assert ingest_calls, "expected coderag.ingest.ingest() to be called"
    assert ingest_calls[0]["chunker_fn"] is not None

    # Drop happens BEFORE ingest, ingest happens BEFORE backend build/scoring.
    delete_idx = next(i for i, e in enumerate(order) if e.startswith("delete:supamem_eval_coderag"))
    ingest_idx = order.index("ingest")
    backend_idx = order.index("backend")
    assert delete_idx < ingest_idx < backend_idx, order


def test_default_off_does_not_call_ingest(monkeypatch) -> None:
    """Without ``--reingest-coderag``, the coderag branch makes NO call to
    ``coderag.ingest.ingest()`` — Phase 16 baseline replay is preserved."""
    from supamem.eval import runner as runner_mod
    from supamem.eval.coderag import ingest as ingest_mod

    ingest_calls: list = []
    monkeypatch.setattr(
        ingest_mod, "ingest",
        lambda *a, **kw: ingest_calls.append((a, kw)),
    )

    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.ensure_populated_manifest",
        lambda _p: {"repos": []},
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_pr_queries",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_adr_queries",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "supamem.eval.suite_loader.load_suite",
        lambda _name: type(
            "FakeSuite", (), {"run": staticmethod(lambda *a, **kw: {"ok": True})}
        ),
    )
    monkeypatch.setattr(
        runner_mod, "_build_backend",
        lambda cfg, *, suite=None: MagicMock(),
    )

    # If reingest_coderag is False, runner MUST NOT instantiate QdrantClient
    # in the reingest branch. We assert by patching it to raise on call.
    def _boom(*a, **kw):
        raise AssertionError("QdrantClient must NOT be constructed when reingest_coderag=False")

    with patch("supamem.eval.runner.QdrantClient", _boom, create=True):
        rc = runner_mod.run_bench(**_make_run_bench_kwargs(reingest_coderag=False))
    assert rc == 0
    assert not ingest_calls, "ingest must NOT be called when reingest_coderag=False"


def test_reingest_does_not_touch_mem0_peer_collection(monkeypatch) -> None:
    """``--reingest-coderag`` only affects the supamem bench collection;
    mem0 peer collection is NEVER passed to ``client.delete_collection``
    from this code path."""
    from supamem.eval import runner as runner_mod
    from supamem.eval.coderag import ingest as ingest_mod

    deleted_names: list[str] = []

    fake_client = MagicMock()
    fake_client.delete_collection.side_effect = (
        lambda name: deleted_names.append(name)
    )
    monkeypatch.setattr(
        "supamem.eval.runner.QdrantClient",
        lambda *a, **kw: fake_client,
        raising=False,
    )
    monkeypatch.setattr(ingest_mod, "ingest", lambda *a, **kw: 0)
    monkeypatch.setattr(
        "supamem.eval.coderag.corpus.ensure_populated_manifest",
        lambda _p: {"repos": []},
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_pr_queries",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "supamem.eval.coderag.auto_queries.extract_adr_queries",
        lambda *_a, **_kw: [],
    )
    monkeypatch.setattr(
        "supamem.eval.suite_loader.load_suite",
        lambda _name: type(
            "FakeSuite", (), {"run": staticmethod(lambda *a, **kw: {"ok": True})}
        ),
    )
    monkeypatch.setattr(
        runner_mod, "_build_backend",
        lambda cfg, *, suite=None: MagicMock(),
    )

    rc = runner_mod.run_bench(**_make_run_bench_kwargs(reingest_coderag=True))
    assert rc == 0
    # Only the supamem bench collection should ever be deleted; no mem0
    # adapter collection.
    for name in deleted_names:
        assert "mem0" not in name.lower(), deleted_names
        assert name == "supamem_eval_coderag", deleted_names


def test_cli_flag_plumbed_through(monkeypatch, tmp_path) -> None:
    """``supamem eval --reingest-coderag --suite coderag --full ...``
    invokes ``run_bench(reingest_coderag=True)``."""
    from typer.testing import CliRunner

    from supamem.cli import app

    captured: dict = {}

    def fake_run_bench(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("supamem.eval.runner.run_bench", fake_run_bench)

    runner = CliRunner()
    out_path = tmp_path / "envelope.json"
    result = runner.invoke(
        app,
        [
            "eval", "--suite", "coderag", "--full",
            "--reingest-coderag", "--out", str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("reingest_coderag") is True


def test_cli_flag_default_false(monkeypatch, tmp_path) -> None:
    """Without ``--reingest-coderag``, ``run_bench`` is called with
    ``reingest_coderag=False``."""
    from typer.testing import CliRunner

    from supamem.cli import app

    captured: dict = {}

    def fake_run_bench(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("supamem.eval.runner.run_bench", fake_run_bench)

    runner = CliRunner()
    out_path = tmp_path / "envelope.json"
    result = runner.invoke(
        app,
        ["eval", "--suite", "coderag", "--full", "--out", str(out_path)],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("reingest_coderag") is False
