"""Phase 15 Plan B Task B3 — HuggingFace publish helper tests."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def mock_pyarrow(monkeypatch):
    """Stub pyarrow.parquet so write_beir_layout can be exercised without
    pyarrow installed in the dev set. Records every write_table call as a
    JSON-serializable summary file alongside the requested parquet path so
    tests can introspect what would have been written.
    """
    pa = types.ModuleType("pyarrow")
    pq = types.ModuleType("pyarrow.parquet")

    class _FakeTable:
        def __init__(self, rows):
            self._rows = rows

        @classmethod
        def from_pylist(cls, rows):
            return cls(list(rows))

        @property
        def column_names(self):
            return list(self._rows[0].keys()) if self._rows else []

    def _write_table(table, where, **_kw):
        # Store a JSON sidecar that tests can read.
        sidecar = Path(str(where) + ".json")
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({
            "rows": table._rows,
            "columns": table.column_names,
        }))
        # Also touch the parquet path so existence checks pass.
        Path(where).write_bytes(b"FAKE-PARQUET")

    pa.Table = _FakeTable
    pq.write_table = _write_table
    monkeypatch.setitem(sys.modules, "pyarrow", pa)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", pq)
    return pq


# ----------------------------- import-time hygiene --------------------------


def test_hf_publish_imports_without_pyarrow():
    """Module import MUST NOT require pyarrow / datasets at top level."""
    import supamem.eval.coderag.hf_publish  # noqa: F401


# ----------------------------- write paths ----------------------------------


def _sample_corpus():
    return [
        {"id": "src/foo.py", "text": "foo", "repo": "supamem"},
        {"id": "src/bar.py", "text": "bar", "repo": "supamem"},
    ]


def _sample_queries():
    return [
        {"id": "pr_supamem_abc", "axis": "code_fact", "repo": "supamem",
         "text": "title", "gold": ["src/foo.py"]},
        {"id": "adr_supamem_0001", "axis": "decision_rationale", "repo": "supamem",
         "text": "why", "gold": ["docs/adr/0001-x.md", "src/bar.py"]},
    ]


def test_hf_publish_writes_three_parquets(tmp_path, mock_pyarrow):
    from supamem.eval.coderag.hf_publish import publish

    publish(tmp_path, _sample_corpus(), _sample_queries(), push=False)
    assert (tmp_path / "corpus.parquet").exists()
    assert (tmp_path / "queries.parquet").exists()
    assert (tmp_path / "qrels.parquet").exists()


def test_hf_publish_writes_dataset_card_readme(tmp_path, mock_pyarrow):
    from supamem.eval.coderag.hf_publish import publish

    publish(tmp_path, _sample_corpus(), _sample_queries(), push=False)
    readme = (tmp_path / "README.md").read_text()
    assert "MIT" in readme
    assert "coderag" in readme.lower()
    # Dataset-card YAML mandatory fields per HF spec:
    assert "license:" in readme
    assert "task_categories:" in readme
    assert "text-retrieval" in readme


def test_hf_publish_qrels_shape_beir_compatible(tmp_path, mock_pyarrow):
    from supamem.eval.coderag.hf_publish import publish

    publish(tmp_path, _sample_corpus(), _sample_queries(), push=False)
    sidecar = json.loads((tmp_path / "qrels.parquet.json").read_text())
    assert sidecar["columns"] == ["query-id", "corpus-id", "score"]
    # 1 + 2 = 3 (q,gold) pairs across the two queries.
    assert len(sidecar["rows"]) == 3
    for r in sidecar["rows"]:
        assert r["score"] == 1


# ----------------------------- HF_TOKEN handling ----------------------------


def test_hf_publish_no_token_read_when_push_false(tmp_path, monkeypatch, mock_pyarrow):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    from supamem.eval.coderag.hf_publish import publish

    # Should NOT raise even with HF_TOKEN unset because push=False.
    publish(tmp_path, _sample_corpus(), _sample_queries(), push=False)


def test_hf_publish_no_token_logged_to_stdout_on_push(
    tmp_path, monkeypatch, capsys, mock_pyarrow
):
    """T-15-02: HF_TOKEN must NOT appear in any stdout/stderr output."""
    monkeypatch.setenv("HF_TOKEN", "SECRET-TOKEN-ABC")
    fake_datasets = types.ModuleType("datasets")
    push_calls: list[dict] = []

    class _FakeDataset:
        @classmethod
        def from_parquet(cls, path):
            d = cls()
            d._path = path
            return d

        def push_to_hub(self, repo_id, **kwargs):
            push_calls.append({"repo_id": repo_id, "kwargs": dict(kwargs)})

    fake_datasets.Dataset = _FakeDataset
    monkeypatch.setitem(sys.modules, "datasets", fake_datasets)

    from supamem.eval.coderag.hf_publish import publish

    publish(tmp_path, _sample_corpus(), _sample_queries(), push=True)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "SECRET-TOKEN-ABC" not in combined
    # The token was passed to push_to_hub via the token= kwarg.
    assert push_calls
    for c in push_calls:
        assert c["kwargs"].get("token") == "SECRET-TOKEN-ABC"


def test_hf_publish_push_calls_push_to_hub_with_token_kwarg(
    tmp_path, monkeypatch, mock_pyarrow
):
    monkeypatch.setenv("HF_TOKEN", "tok-xyz")
    fake_datasets = types.ModuleType("datasets")
    captured: list[dict] = []

    class _FakeDataset:
        @classmethod
        def from_parquet(cls, path):
            return cls()

        def push_to_hub(self, repo_id, **kwargs):
            captured.append({"repo_id": repo_id, "kwargs": kwargs})

    fake_datasets.Dataset = _FakeDataset
    monkeypatch.setitem(sys.modules, "datasets", fake_datasets)

    from supamem.eval.coderag.hf_publish import publish

    publish(tmp_path, _sample_corpus(), _sample_queries(), push=True)
    assert captured
    for c in captured:
        assert c["kwargs"]["token"] == "tok-xyz"
        # config_name MUST distinguish the three artifacts.
        assert c["kwargs"].get("config_name") in {"corpus", "queries", "qrels"}
