"""Phase 15 Plan E Task E2 — llms.txt v0.3.0a5 doc-shape tests.

Per AGENTS.md "llms.txt is MANDATORY": drift between code and llms.txt
is a documentation bug. This module locks the four touchpoints that
v0.3.0a5 must reflect:

1. Releases one-liner mentions the new version.
2. supamem eval description includes `--suite coderag`.
3. payload.repo and payload.axis are documented as bench-only
   pass-through keys (mirrors Phase 14's payload.session_id disclosure).
4. ADR-0002 is linked.
"""
from __future__ import annotations

from pathlib import Path

import pytest


LLMS_TXT_PATH = Path(__file__).resolve().parent.parent / "llms.txt"


@pytest.fixture(scope="module")
def llms_txt() -> str:
    return LLMS_TXT_PATH.read_text(encoding="utf-8")


def test_llms_txt_releases_line_bumped(llms_txt: str) -> None:
    assert "0.3.0a5" in llms_txt or "v0.3.0a5" in llms_txt or "0.3.0\n" in llms_txt, (
        "llms.txt must mention the new version (0.3.0a5 or 0.3.0)"
    )


def test_llms_txt_mentions_coderag_suite(llms_txt: str) -> None:
    assert "coderag" in llms_txt
    assert "supamem eval --suite coderag" in llms_txt


def test_llms_txt_mentions_payload_repo(llms_txt: str) -> None:
    assert "payload.repo" in llms_txt or "`repo`" in llms_txt, (
        "llms.txt must disclose `payload.repo` as a bench-only pass-through key"
    )


def test_llms_txt_mentions_payload_axis(llms_txt: str) -> None:
    assert "payload.axis" in llms_txt or "`axis`" in llms_txt, (
        "llms.txt must disclose `payload.axis` as a bench-only pass-through key"
    )


def test_llms_txt_links_adr_0002(llms_txt: str) -> None:
    assert "ADR-0002" in llms_txt or "0002-coderag-eval-philosophy" in llms_txt
