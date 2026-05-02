"""RED unit tests for supamem.install.agent_patcher (Plan 08.1-01).

This file is intentionally RED. The module ``supamem.install.agent_patcher`` does
not yet exist; Plan 08.1-02 creates it. Each detect_*/patch_*/block_sha256_* test
stub imports the missing symbol INSIDE the test body so the file still COLLECTS
(letting the Wave 0 ruamel smoke run) while the stubs themselves FAIL with either
``ModuleNotFoundError`` or the explicit ``pytest.fail("RED: implement in Plan 02")``
sentinel — both surface as FAIL/ERROR in the pytest report, never as pass/skip.

The Wave 0 smoke test ``test_ruamel_csv_round_trip_preserves_csv_style``
intentionally does NOT depend on the patcher import — it validates Assumption A4
from 08.1-RESEARCH.md (ruamel.yaml's CSV style preserves on append) using
ruamel.yaml directly. This MUST pass on first run; if it fails, Plan 02's parser
choice needs revisiting BEFORE a single line of patcher code is written.

REACH-NN -> test_NAME mapping (per 08.1-RESEARCH.md "Phase Requirements -> Test Map"):
  REACH-03 / REACH-08  -> test_detect_state_*_covered
  REACH-01             -> test_detect_state_csv_patchable, test_detect_state_block_list_patchable
  REACH-04             -> test_patch_csv_appends_at_end_preserves_spacing,
                          test_patch_csv_no_spaces_appends_no_spaces,
                          test_patch_block_list_appends_with_matching_indent,
                          test_patch_preserves_comments_in_frontmatter,
                          test_ruamel_csv_round_trip_preserves_csv_style (Wave 0 smoke)
  REACH-04 / REACH-07  -> test_detect_state_flow_style_skipped,
                          test_detect_state_malformed_yaml_skipped
  REACH-03             -> test_patch_idempotent_when_already_covered,
                          test_detect_state_full_inheritance_no_tools_line,
                          test_detect_state_empty_tools_line
  REACH-05 / REACH-06  -> test_block_sha256_excludes_prose_body,
                          test_block_sha256_normalizes_crlf
"""
from __future__ import annotations

from io import StringIO

import pytest


def _import_patcher() -> object:
    """Import the not-yet-existing patcher module.

    Lifted out of module scope so collection succeeds and the Wave 0 ruamel
    smoke test can run independently of the patcher module's existence. Each
    RED stub calls this and lets the ImportError surface as a test FAIL.
    """
    from supamem.install import agent_patcher  # type: ignore[import-not-found]

    return agent_patcher


# ---------------------------------------------------------------------------
# detect_tools_state — coverage / inheritance / patchable / skipped states
# ---------------------------------------------------------------------------


def test_detect_state_full_inheritance_no_tools_line() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_empty_tools_line() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_csv_with_mcp_wildcard_covered() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_csv_with_supamem_wildcard_covered() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_csv_with_supamem_specific_tool_covered() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_csv_patchable() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_block_list_patchable() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_flow_style_skipped() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_detect_state_malformed_yaml_skipped() -> None:
    pytest.fail("RED: implement in Plan 02")


# ---------------------------------------------------------------------------
# patch_yaml — CSV vs block-list append, comment / idempotency preservation
# ---------------------------------------------------------------------------


def test_patch_csv_appends_at_end_preserves_spacing() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_patch_csv_no_spaces_appends_no_spaces() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_patch_block_list_appends_with_matching_indent() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_patch_preserves_comments_in_frontmatter() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_patch_idempotent_when_already_covered() -> None:
    pytest.fail("RED: implement in Plan 02")


# ---------------------------------------------------------------------------
# block_sha256 — frontmatter-only hash, CRLF normalization
# ---------------------------------------------------------------------------


def test_block_sha256_excludes_prose_body() -> None:
    pytest.fail("RED: implement in Plan 02")


def test_block_sha256_normalizes_crlf() -> None:
    pytest.fail("RED: implement in Plan 02")


# ---------------------------------------------------------------------------
# Wave 0 smoke — Assumption A4 from 08.1-RESEARCH.md
#
# This test validates that ruamel.yaml's round-trip mode preserves CSV style
# across a load -> mutate -> dump cycle. If this FAILS, Plan 02 cannot use
# ruamel.yaml's CommentedSeq/scalar approach; we'd have to fall back to
# regex-based mutation (which D-YAML-01 explicitly rules out).
#
# Implementation note: ruamel parses ``tools: A, B, C`` as a plain string scalar
# (not a sequence). The append happens via tokenize-mutate-rejoin, so the dump
# emits the same scalar style. The assertion below exercises BOTH that the new
# token appears AND that the output is not coerced into flow style ([A, B, C]).
# ---------------------------------------------------------------------------


def test_ruamel_csv_round_trip_preserves_csv_style() -> None:
    from ruamel.yaml import YAML

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    src = "tools: Read, Bash, mcp__context7__*\n"
    data = yaml.load(src)
    # ruamel parses CSV string scalar into a plain string; tokenize-append-rejoin
    # so the dump emits CSV form again.
    tokens = [t.strip() for t in data["tools"].split(",")]
    tokens.append("mcp__supamem__*")
    data["tools"] = ", ".join(tokens)
    out = StringIO()
    yaml.dump(data, out)
    rendered = out.getvalue()
    assert "mcp__supamem__*" in rendered, (
        f"appended token missing from round-tripped output: {rendered!r}"
    )
    assert "[" not in rendered, (
        f"ruamel coerced CSV to flow style — D-YAML-03 violated: {rendered!r}"
    )
