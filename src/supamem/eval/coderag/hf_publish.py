"""HuggingFace dataset publish helper for the coderag suite.

Phase 15 Plan B Task B3.

Layout: BEIR-shaped Parquet files (``corpus.parquet``, ``queries.parquet``,
``qrels.parquet``) + a dataset-card README with the YAML frontmatter HF
requires (``license``, ``task_categories``, ``language``, ``pretty_name``,
``size_categories``, ``source_datasets``).

Hard rules:

* ``HF_TOKEN`` is read from the environment ONLY when ``push=True``. The
  token is passed to ``Dataset.push_to_hub`` via the ``token=`` kwarg —
  never f-string-interpolated into a logged message (T-15-02).
* ``pyarrow`` and ``datasets`` are LAZY imports inside the writer / push
  functions. The module must import cleanly even on a dev install that
  has neither — required for the CI test environment which omits the
  ``eval`` extras.
* ``--push`` is a manual-only operation (CODERAG-09 manual verification
  per VALIDATION.md); CI never sets it.
"""
from __future__ import annotations

import os
from pathlib import Path

from supamem.console import err_console

DATASET_NAME = "dzmitrys-dev/coderag-supamem-fastapi"
LICENSE = "MIT"


# ----------------------------- BEIR layout writers --------------------------


def _qrels_rows(queries: list[dict]) -> list[dict]:
    """Flatten queries → BEIR qrels rows ``{query-id, corpus-id, score}``."""
    rows: list[dict] = []
    for q in queries:
        for gold in q["gold"]:
            rows.append({"query-id": q["id"], "corpus-id": gold, "score": 1})
    return rows


def _write_parquet(rows: list[dict], where: Path) -> None:
    """Write a list-of-dicts as a Parquet file via lazy pyarrow import.

    Lazy import so the module imports cleanly without pyarrow installed.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(where))


def write_beir_layout(out_dir: Path, corpus: list[dict], queries: list[dict]) -> None:
    """Write ``corpus.parquet`` + ``queries.parquet`` + ``qrels.parquet`` +
    dataset-card ``README.md`` to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(corpus, out_dir / "corpus.parquet")
    _write_parquet(queries, out_dir / "queries.parquet")
    _write_parquet(_qrels_rows(queries), out_dir / "qrels.parquet")
    (out_dir / "README.md").write_text(_dataset_card(), encoding="utf-8")


# ----------------------------- dataset card ---------------------------------


def _dataset_card() -> str:
    """Return the dataset-card README with HF YAML frontmatter."""
    return (
        "---\n"
        f"license: {LICENSE}\n"
        "task_categories:\n  - text-retrieval\n"
        "language:\n  - en\n  - code\n"
        "pretty_name: coderag — supamem + fastapi\n"
        "size_categories:\n  - 1K<n<10K\n"
        "source_datasets:\n  - original\n"
        "---\n\n"
        "# coderag — supamem + fastapi\n\n"
        "Two-repo agentic-coding evaluation haystack pinned to specific commit "
        "SHAs for reproducibility. Two reported axes:\n\n"
        "- `code_fact` — PR-derived queries with file-modification gold.\n"
        "- `decision_rationale` — ADR-derived queries (supamem only — fastapi "
        "  has no `docs/adr/`).\n\n"
        "Three-column reporting per axis (`supamem_only` / `fastapi_only` / "
        "`combined`) per supamem ADR-0002.\n\n"
        "## License\n\nMIT.\n\n"
        "## Caveats\n\n"
        "supamem self-corpus is in every frontier-model training set; "
        "the per-repo manifest records `training_leakage_suspected: true` "
        "for self-references. Three-column reporting lets readers audit "
        "self-reference circularity at a glance.\n"
    )


# ----------------------------- publish entrypoint ---------------------------


def publish(
    out_dir: Path,
    corpus: list[dict],
    queries: list[dict],
    *,
    push: bool = False,
) -> None:
    """Write BEIR-shaped Parquet + dataset card; optionally push to HF.

    ``push=True`` reads ``HF_TOKEN`` from env and passes it as a kwarg to
    ``Dataset.push_to_hub``. The token is NEVER logged.
    """
    write_beir_layout(out_dir, corpus, queries)
    if not push:
        return
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN not set; cannot publish to HuggingFace Hub with push=True"
        )
    # Lazy import — datasets is in the optional `eval` extras only.
    from datasets import Dataset

    for name in ("corpus", "queries", "qrels"):
        ds = Dataset.from_parquet(str(out_dir / f"{name}.parquet"))
        # NOTE: token is passed via kwarg ONLY. Do NOT include it in any
        # log message or f-string (T-15-02).
        ds.push_to_hub(
            DATASET_NAME,
            config_name=name,
            token=token,
            commit_message=f"upload {name}",
        )
    err_console.print(
        f"[supamem.info]coderag: published {DATASET_NAME} to HuggingFace Hub"
        f"[/supamem.info]"
    )


__all__ = [
    "DATASET_NAME",
    "LICENSE",
    "publish",
    "write_beir_layout",
]
