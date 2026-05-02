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
from pathlib import Path

_FIXTURES = Path(__file__).parent / "_fixtures" / "agents"


def _import_patcher() -> object:
    """Import the patcher module (Plan 02 makes this succeed).

    Kept as a helper so tests stay decoupled from the import path string.
    """
    from supamem.install import agent_patcher

    return agent_patcher


def _read_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# detect_tools_state — coverage / inheritance / patchable / skipped states
# ---------------------------------------------------------------------------


def test_detect_state_full_inheritance_no_tools_line() -> None:
    p = _import_patcher()
    # no-frontmatter.md has no leading --- block; raw kernel reports
    # "skipped:no-frontmatter" (the doctor wrapper in Plan 03 maps to
    # "OK full inheritance" for display per P9).
    assert p.detect_tools_state(_read_fixture("no-frontmatter.md")) == "skipped:no-frontmatter"


def test_detect_state_empty_tools_line() -> None:
    p = _import_patcher()
    assert p.detect_tools_state(_read_fixture("empty-tools.md")) == "inheritance"


def test_detect_state_csv_with_mcp_wildcard_covered() -> None:
    p = _import_patcher()
    assert p.detect_tools_state(_read_fixture("csv-covered.md")) == "covered"


def test_detect_state_csv_with_supamem_wildcard_covered() -> None:
    p = _import_patcher()
    assert (
        p.detect_tools_state(_read_fixture("csv-supamem-wildcard-covered.md"))
        == "covered"
    )


def test_detect_state_csv_with_supamem_specific_tool_covered() -> None:
    p = _import_patcher()
    assert (
        p.detect_tools_state(_read_fixture("csv-supamem-literal-covered.md"))
        == "covered"
    )


def test_detect_state_csv_patchable() -> None:
    p = _import_patcher()
    assert p.detect_tools_state(_read_fixture("csv-patchable.md")) == "patchable_csv"


def test_detect_state_block_list_patchable() -> None:
    p = _import_patcher()
    assert (
        p.detect_tools_state(_read_fixture("block-list-patchable.md"))
        == "patchable_list"
    )


def test_detect_state_flow_style_skipped() -> None:
    p = _import_patcher()
    assert p.detect_tools_state(_read_fixture("flow-style-skipped.md")) == "skipped:flow"


def test_detect_state_malformed_yaml_skipped() -> None:
    p = _import_patcher()
    assert (
        p.detect_tools_state(_read_fixture("malformed.md")) == "skipped:malformed"
    )


# ---------------------------------------------------------------------------
# patch_yaml — CSV vs block-list append, comment / idempotency preservation
# ---------------------------------------------------------------------------


def test_patch_csv_appends_at_end_preserves_spacing() -> None:
    p = _import_patcher()
    src = _read_fixture("csv-patchable.md")
    new_text, fragment = p.patch_yaml(src)
    assert fragment is not None
    assert fragment["tools_form"] == "csv"
    assert "mcp__supamem__*" in new_text
    # Original used `, ` separators — preserve that.
    assert ", mcp__supamem__*" in new_text
    # Frontmatter SHA changed; original recorded.
    assert fragment["original_frontmatter_sha256"] != fragment["patched_frontmatter_sha256"]
    assert fragment["original_frontmatter"].startswith("---\n")


def test_patch_csv_no_spaces_appends_no_spaces() -> None:
    p = _import_patcher()
    src = (
        "---\n"
        "name: tight\n"
        "description: no-space CSV\n"
        "tools: Read,Bash,Grep\n"
        "---\n\n"
        "body\n"
    )
    new_text, fragment = p.patch_yaml(src)
    assert fragment is not None
    assert fragment["tools_form"] == "csv"
    # No space added — matches the user's existing style.
    assert "Read,Bash,Grep,mcp__supamem__*" in new_text
    # Sanity: did NOT inject a leading space.
    assert ", mcp__supamem__*" not in new_text


def test_patch_block_list_appends_with_matching_indent() -> None:
    p = _import_patcher()
    src = _read_fixture("block-list-patchable.md")
    new_text, fragment = p.patch_yaml(src)
    assert fragment is not None
    assert fragment["tools_form"] == "block-list"
    # Block-list item with the standard 2-space indent (matches siblings).
    assert "\n  - mcp__supamem__*\n" in new_text
    # Original block items still present.
    assert "\n  - Read\n" in new_text
    assert "\n  - mcp__context7__*\n" in new_text


def test_patch_preserves_comments_in_frontmatter() -> None:
    p = _import_patcher()
    src = _read_fixture("with-comments.md")
    new_text, fragment = p.patch_yaml(src)
    assert fragment is not None
    # The standalone comment on its own line MUST round-trip verbatim.
    assert "# user-authored comment that ruamel.yaml MUST preserve" in new_text
    # The inline comment is preserved somewhere in the frontmatter; ruamel may
    # reposition it relative to the appended token, so we just verify it
    # survives anywhere in the patched output.
    assert "# inline trailing comment" in new_text
    # The supamem token was actually appended.
    assert "mcp__supamem__*" in new_text


def test_patch_idempotent_when_already_covered() -> None:
    p = _import_patcher()
    # Already-covered file: patch_yaml is a no-op.
    covered = _read_fixture("csv-supamem-wildcard-covered.md")
    out, fragment = p.patch_yaml(covered)
    assert out == covered
    assert fragment is None

    # End-to-end: patching a patchable file once produces patched output;
    # patching that output a SECOND time MUST be a no-op (D-COVER-03).
    raw = _read_fixture("csv-patchable.md")
    once_text, once_fragment = p.patch_yaml(raw)
    assert once_fragment is not None
    twice_text, twice_fragment = p.patch_yaml(once_text)
    assert twice_text == once_text
    assert twice_fragment is None


# ---------------------------------------------------------------------------
# block_sha256 — frontmatter-only hash, CRLF normalization
# ---------------------------------------------------------------------------


def test_block_sha256_excludes_prose_body() -> None:
    p = _import_patcher()
    base = _read_fixture("with-comments.md")
    # Mutate prose body only — frontmatter unchanged. SHA must be identical.
    mutated = base.rstrip() + "\n\nappended prose paragraph that the user wrote\n"
    assert p.block_sha256(base) == p.block_sha256(mutated)
    # Sanity: changing the frontmatter DOES change the hash.
    fm_changed = base.replace("Read, Bash", "Read, Bash, Grep")
    assert p.block_sha256(base) != p.block_sha256(fm_changed)


def test_block_sha256_normalizes_crlf() -> None:
    p = _import_patcher()
    lf = _read_fixture("csv-patchable.md")
    crlf = lf.replace("\n", "\r\n")
    assert p.block_sha256(lf) == p.block_sha256(crlf)


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
