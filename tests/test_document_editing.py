"""Tests for oaw.document.editing: SourceEdit, apply_edits, and the high-level
frontmatter/section editing operations.
"""

from __future__ import annotations

import pytest

from oaw.document.editing import (
    EditResult,
    SourceEdit,
    append_block_to_section,
    append_frontmatter_list_item,
    apply_edits,
    normalize_block_newlines,
    remove_frontmatter_list_item,
    set_frontmatter_scalar,
)
from oaw.document.frontmatter_yaml import FrontmatterModel
from oaw.document.model import NoteDocument, parse_note_source
from oaw.document.types import SourceSpan
from oaw.errors import OawError


def _frontmatter(document: NoteDocument) -> FrontmatterModel:
    assert document.frontmatter is not None
    return document.frontmatter


# --------------------------------------------------------------------------- #
# normalize_block_newlines
# --------------------------------------------------------------------------- #


def test_normalize_block_newlines_lf_to_crlf():
    assert normalize_block_newlines("a\nb\n", "\r\n") == "a\r\nb\r\n"


def test_normalize_block_newlines_crlf_to_lf():
    assert normalize_block_newlines("a\r\nb\r\n", "\n") == "a\nb\n"


def test_normalize_block_newlines_mixed_input_normalizes_consistently():
    assert normalize_block_newlines("a\r\nb\nc\r", "\r\n") == "a\r\nb\r\nc\r\n"


def test_normalize_block_newlines_noop_when_already_matching():
    assert normalize_block_newlines("a\nb\n", "\n") == "a\nb\n"


# --------------------------------------------------------------------------- #
# apply_edits: refusals and mechanics
# --------------------------------------------------------------------------- #


def test_apply_edits_empty_list_refuses():
    document = parse_note_source("hello world\n")
    with pytest.raises(OawError):
        apply_edits(document, [])


def test_apply_edits_out_of_range_span_refuses():
    document = parse_note_source("hello\n")
    edit = SourceEdit(span=SourceSpan(0, 100), text="x")
    with pytest.raises(OawError):
        apply_edits(document, [edit])


def test_apply_edits_overlapping_edits_refuse():
    document = parse_note_source("hello world\n")
    edits = [
        SourceEdit(span=SourceSpan(0, 5), text="HI"),
        SourceEdit(span=SourceSpan(3, 8), text="YO"),
    ]
    with pytest.raises(OawError):
        apply_edits(document, edits)


def test_apply_edits_duplicate_zero_width_edits_at_same_point_refuse():
    document = parse_note_source("hello world\n")
    edits = [
        SourceEdit(span=SourceSpan(5, 5), text="A"),
        SourceEdit(span=SourceSpan(5, 5), text="B"),
    ]
    with pytest.raises(OawError):
        apply_edits(document, edits)


def test_apply_edits_splitting_crlf_pair_refuses():
    document = parse_note_source("a\r\nb\r\n")
    # offset 2 sits between the \r and \n of the first line ending.
    edit = SourceEdit(span=SourceSpan(2, 2), text="x")
    with pytest.raises(OawError):
        apply_edits(document, [edit])


def test_apply_edits_protected_region_refuses_without_allow_protected():
    document = parse_note_source("before\n```\ncode\n```\nafter\n")
    fence_region = next(r for r in document.protected_regions if r.kind == "fence")
    edit = SourceEdit(
        span=SourceSpan(fence_region.span.start + 1, fence_region.span.start + 2), text="x"
    )
    with pytest.raises(OawError):
        apply_edits(document, [edit])


def test_apply_edits_protected_region_allowed_with_allow_protected():
    document = parse_note_source("before\n```\ncode\n```\nafter\n")
    fence_region = next(r for r in document.protected_regions if r.kind == "fence")
    point = fence_region.span.start
    edit = SourceEdit(span=SourceSpan(point, point), text="X")
    result = apply_edits(document, [edit], allow_protected=True)
    assert result.source.startswith("before\nX```")


def test_apply_edits_error_diagnostic_span_refuses():
    # A YAML error in frontmatter spans the whole inner block with ERROR severity.
    document = parse_note_source("---\nkey: [unclosed\n---\nbody\n")
    error_diag = next(d for d in document.diagnostics if d.code == "frontmatter.yaml-error")
    assert error_diag.span is not None
    point = error_diag.span.start
    edit = SourceEdit(span=SourceSpan(point, point + 1), text="x")
    with pytest.raises(OawError):
        apply_edits(document, [edit])


def test_apply_edits_applies_multiple_nonoverlapping_edits_end_backward():
    document = parse_note_source("aaa bbb ccc\n")
    edits = [
        SourceEdit(span=SourceSpan(0, 3), text="XXX"),
        SourceEdit(span=SourceSpan(8, 11), text="ZZZ"),
    ]
    result = apply_edits(document, edits)
    assert result.source == "XXX bbb ZZZ\n"
    assert isinstance(result, EditResult)
    assert result.edits == tuple(sorted(edits, key=lambda e: e.span.start))


def test_apply_edits_reparses_result_document():
    document = parse_note_source("# Title\n\nbody\n")
    edit = SourceEdit(span=SourceSpan(2, 7), text="Other")
    result = apply_edits(document, [edit])
    assert result.document.markdown.headings[0].text == "Other"


def test_apply_edits_verify_callback_can_refuse():
    document = parse_note_source("hello world\n")
    edit = SourceEdit(span=SourceSpan(0, 5), text="HI")

    def verify(_doc):
        raise OawError("nope")

    with pytest.raises(OawError, match="nope"):
        apply_edits(document, [edit], verify=verify)


def test_apply_edits_bytes_outside_edit_untouched():
    source = "line one\nline two\nline three\n"
    document = parse_note_source(source)
    edit = SourceEdit(span=SourceSpan(9, 17), text="LINE TWO")
    result = apply_edits(document, [edit])
    assert result.source == "line one\nLINE TWO\nline three\n"
    assert result.source[:9] == source[:9]
    assert result.source[9 + len("LINE TWO") :] == source[17:]


# --------------------------------------------------------------------------- #
# append_block_to_section
# --------------------------------------------------------------------------- #

LF_NOTE = (
    "---\n"
    "id: OAW-TSK-example\n"
    "---\n"
    "\n"
    "# Title\n"
    "\n"
    "## Agent sessions\n"
    "\n"
    "- existing entry\n"
    "\n"
    "## Notes\n"
    "\n"
    "some note text\n"
)

CRLF_NOTE = LF_NOTE.replace("\n", "\r\n")


def test_append_block_to_section_middle_section_inserts_before_next_heading():
    document = parse_note_source(LF_NOTE)
    result = append_block_to_section(document, "## Agent sessions", "- new entry")
    assert "- existing entry\n\n- new entry\n\n## Notes" in result.source
    assert result.source.count("- new entry") == 1


def test_append_block_to_section_last_section_appends_at_document_end():
    document = parse_note_source(LF_NOTE)
    result = append_block_to_section(document, "## Notes", "- trailing entry")
    assert result.source.endswith("some note text\n\n- trailing entry\n")


def test_append_block_to_section_missing_heading_appends_new_section():
    document = parse_note_source(LF_NOTE)
    result = append_block_to_section(document, "## Followups", "- first entry")
    assert result.source.endswith("\n## Followups\n\n- first entry\n")
    # Nothing before the appended section changed.
    assert result.source.startswith(LF_NOTE.rstrip("\n"))


def test_append_block_to_section_crlf_preserves_crlf_everywhere():
    document = parse_note_source(CRLF_NOTE)
    result = append_block_to_section(document, "## Agent sessions", "- new entry")
    assert "\r\n- new entry\r\n\r\n## Notes" in result.source
    assert "\n" not in result.source.replace("\r\n", "")


def test_append_block_to_section_crlf_missing_heading_uses_crlf():
    document = parse_note_source(CRLF_NOTE)
    result = append_block_to_section(document, "## Followups", "- first entry")
    assert result.source.endswith("\r\n## Followups\r\n\r\n- first entry\r\n")
    assert "\n" not in result.source.replace("\r\n", "")


def test_append_block_to_section_empty_block_refuses():
    document = parse_note_source(LF_NOTE)
    with pytest.raises(OawError):
        append_block_to_section(document, "## Notes", "   ")


def test_append_block_to_section_refuses_next_to_unclosed_fence():
    source = "# Notes\n\nintro\n\n```\nunterminated\n"
    document = parse_note_source(source)
    with pytest.raises(OawError, match="unclosed protected region") as excinfo:
        append_block_to_section(document, "## Agent sessions", "- entry")
    assert "fence" in str(excinfo.value)


def test_append_block_to_section_refuses_when_target_section_ends_inside_unclosed_comment():
    source = "## Agent sessions\n\n%%comment never closes\n"
    document = parse_note_source(source)
    with pytest.raises(OawError, match="unclosed protected region") as excinfo:
        append_block_to_section(document, "## Agent sessions", "- entry")
    message = str(excinfo.value)
    assert "obsidian-comment" in message
    assert "cannot append:" in message


def test_append_block_to_section_normalizes_block_newlines_to_document_convention():
    document = parse_note_source(CRLF_NOTE)
    result = append_block_to_section(document, "## Notes", "line1\nline2")
    assert "line1\r\nline2" in result.source


# --------------------------------------------------------------------------- #
# set_frontmatter_scalar
# --------------------------------------------------------------------------- #


def test_set_frontmatter_scalar_rewrites_existing_scalar():
    document = parse_note_source("---\nstatus: doing\n---\nbody\n")
    result = set_frontmatter_scalar(document, "status", "done")
    assert result.source == "---\nstatus: done\n---\nbody\n"
    assert _frontmatter(result.document).value("status") == "done"


def test_set_frontmatter_scalar_inserts_missing_key():
    document = parse_note_source("---\nid: OAW-TSK-x\n---\nbody\n")
    result = set_frontmatter_scalar(document, "status", "doing")
    assert result.source == "---\nid: OAW-TSK-x\nstatus: doing\n---\nbody\n"


def test_set_frontmatter_scalar_ambiguous_value_gets_json_quoted():
    document = parse_note_source("---\nstatus: doing\n---\nbody\n")
    result = set_frontmatter_scalar(document, "status", "true")
    assert 'status: "true"' in result.source
    assert _frontmatter(result.document).value("status") == "true"


def test_set_frontmatter_scalar_raw_writes_bare_value_and_round_trips():
    document = parse_note_source("---\npriority: 1\n---\nbody\n")
    result = set_frontmatter_scalar(document, "priority", "2", raw=True)
    assert result.source == "---\npriority: 2\n---\nbody\n"
    assert _frontmatter(result.document).value("priority") == "2"


def test_set_frontmatter_scalar_raw_refuses_value_yaml_would_truncate():
    document = parse_note_source("---\npriority: 1\n---\nbody\n")
    with pytest.raises(OawError):
        set_frontmatter_scalar(document, "priority", "2 # x", raw=True)


def test_set_frontmatter_scalar_raw_refuses_multiline_value():
    document = parse_note_source("---\npriority: 1\n---\nbody\n")
    with pytest.raises(OawError):
        set_frontmatter_scalar(document, "priority", "2\nmalicious: true", raw=True)


def test_set_frontmatter_scalar_no_frontmatter_refuses():
    document = parse_note_source("no frontmatter here\n")
    with pytest.raises(OawError):
        set_frontmatter_scalar(document, "status", "doing")


def test_set_frontmatter_scalar_duplicate_key_refuses():
    document = parse_note_source("---\nstatus: a\nstatus: b\n---\nbody\n")
    with pytest.raises(OawError):
        set_frontmatter_scalar(document, "status", "done")


def test_set_frontmatter_scalar_wrong_kind_refuses():
    document = parse_note_source("---\ntags:\n  - a\n  - b\n---\nbody\n")
    with pytest.raises(OawError):
        set_frontmatter_scalar(document, "tags", "done")


def test_set_frontmatter_scalar_empty_value_inserts_after_colon():
    document = parse_note_source("---\nstatus:\nother: 1\n---\nbody\n")
    result = set_frontmatter_scalar(document, "status", "doing")
    assert "status: doing" in result.source
    assert _frontmatter(result.document).value("status") == "doing"


def test_set_frontmatter_scalar_crlf_and_bom_note():
    source = "﻿---\r\nstatus: doing\r\n---\r\nbody\r\n"
    document = parse_note_source(source)
    result = set_frontmatter_scalar(document, "status", "done")
    assert result.source == "﻿---\r\nstatus: done\r\n---\r\nbody\r\n"


# --------------------------------------------------------------------------- #
# append_frontmatter_list_item
# --------------------------------------------------------------------------- #


def test_append_frontmatter_list_item_appends_to_existing_block_list():
    document = parse_note_source("---\ntags:\n  - alpha\n  - beta\n---\nbody\n")
    result = append_frontmatter_list_item(document, "tags", "gamma")
    assert result.source == '---\ntags:\n  - alpha\n  - beta\n  - "gamma"\n---\nbody\n'


def test_append_frontmatter_list_item_dedup_is_noop_with_original_document():
    document = parse_note_source("---\ntags:\n  - alpha\n  - beta\n---\nbody\n")
    result = append_frontmatter_list_item(document, "tags", "alpha")
    assert result.edits == ()
    assert result.document is document
    assert result.source == document.source


def test_append_frontmatter_list_item_missing_key_creates_block_list():
    document = parse_note_source("---\nid: OAW-TSK-x\n---\nbody\n")
    result = append_frontmatter_list_item(document, "tags", "alpha")
    assert result.source == '---\nid: OAW-TSK-x\ntags:\n  - "alpha"\n---\nbody\n'


def test_append_frontmatter_list_item_flow_list_refuses():
    document = parse_note_source("---\naliases: [alpha, beta]\n---\nbody\n")
    with pytest.raises(OawError):
        append_frontmatter_list_item(document, "aliases", "gamma")


def test_append_frontmatter_list_item_nested_mapping_refuses():
    document = parse_note_source("---\nmeta:\n  owner: someone\n---\nbody\n")
    with pytest.raises(OawError):
        append_frontmatter_list_item(document, "meta", "gamma")


def test_append_frontmatter_list_item_scalar_field_refuses():
    document = parse_note_source("---\nstatus: doing\n---\nbody\n")
    with pytest.raises(OawError):
        append_frontmatter_list_item(document, "status", "gamma")


def test_append_frontmatter_list_item_crlf_preserves_crlf():
    source = "---\r\ntags:\r\n  - alpha\r\n---\r\nbody\r\n"
    document = parse_note_source(source)
    result = append_frontmatter_list_item(document, "tags", "beta")
    assert result.source == '---\r\ntags:\r\n  - alpha\r\n  - "beta"\r\n---\r\nbody\r\n'


# --------------------------------------------------------------------------- #
# remove_frontmatter_list_item
# --------------------------------------------------------------------------- #


def test_remove_frontmatter_list_item_removes_one_of_many():
    document = parse_note_source("---\ntags:\n  - alpha\n  - beta\n---\nbody\n")
    result = remove_frontmatter_list_item(document, "tags", "alpha")
    assert result.source == "---\ntags:\n  - beta\n---\nbody\n"


def test_remove_frontmatter_list_item_removes_last_item_removes_whole_entry():
    document = parse_note_source("---\ntags:\n  - alpha\n---\nbody\n")
    result = remove_frontmatter_list_item(document, "tags", "alpha")
    assert result.source == "---\n---\nbody\n"
    assert _frontmatter(result.document).field("tags") is None


def test_remove_frontmatter_list_item_missing_key_refuses():
    document = parse_note_source("---\nid: OAW-TSK-x\n---\nbody\n")
    with pytest.raises(OawError, match="relationship is not present"):
        remove_frontmatter_list_item(document, "tags", "alpha")


def test_remove_frontmatter_list_item_missing_value_refuses():
    document = parse_note_source("---\ntags:\n  - alpha\n---\nbody\n")
    with pytest.raises(OawError, match="relationship is not present"):
        remove_frontmatter_list_item(document, "tags", "gamma")


def test_remove_frontmatter_list_item_duplicate_key_refuses():
    document = parse_note_source("---\ntags:\n  - a\ntags:\n  - b\n---\nbody\n")
    with pytest.raises(OawError):
        remove_frontmatter_list_item(document, "tags", "a")


def test_remove_frontmatter_list_item_flow_list_refuses():
    document = parse_note_source("---\naliases: [alpha, beta]\n---\nbody\n")
    with pytest.raises(OawError):
        remove_frontmatter_list_item(document, "aliases", "alpha")


def test_remove_frontmatter_list_item_crlf_preserves_crlf():
    source = "---\r\ntags:\r\n  - alpha\r\n  - beta\r\n---\r\nbody\r\n"
    document = parse_note_source(source)
    result = remove_frontmatter_list_item(document, "tags", "alpha")
    assert result.source == "---\r\ntags:\r\n  - beta\r\n---\r\nbody\r\n"


def test_remove_frontmatter_list_item_removes_middle_item_preserves_other_bytes():
    source = "---\ntags:\n  - alpha\n  - beta\n  - gamma\n---\nbody\n"
    document = parse_note_source(source)
    result = remove_frontmatter_list_item(document, "tags", "beta")
    expected = "---\ntags:\n  - alpha\n  - gamma\n---\nbody\n"
    assert result.source == expected


# --------------------------------------------------------------------------- #
# Broken-YAML frontmatter guard (_refuse_broken_frontmatter)
# --------------------------------------------------------------------------- #

BROKEN_FRONTMATTER_NOTE = "---\nbad: [unclosed\n---\nbody\n"


def test_set_frontmatter_scalar_refuses_on_unparseable_frontmatter():
    document = parse_note_source(BROKEN_FRONTMATTER_NOTE)
    with pytest.raises(OawError, match="not parseable YAML"):
        set_frontmatter_scalar(document, "status", "done")


def test_append_frontmatter_list_item_refuses_on_unparseable_frontmatter():
    document = parse_note_source(BROKEN_FRONTMATTER_NOTE)
    with pytest.raises(OawError, match="not parseable YAML"):
        append_frontmatter_list_item(document, "tags", "alpha")


def test_remove_frontmatter_list_item_refuses_on_unparseable_frontmatter():
    document = parse_note_source(BROKEN_FRONTMATTER_NOTE)
    with pytest.raises(OawError, match="not parseable YAML"):
        remove_frontmatter_list_item(document, "tags", "alpha")


# --------------------------------------------------------------------------- #
# set_frontmatter_scalar on a block-scalar field
# --------------------------------------------------------------------------- #


def test_set_frontmatter_scalar_block_scalar_field_refuses():
    document = parse_note_source("---\nnotes: |\n  line one\n  line two\n---\nbody\n")
    with pytest.raises(OawError, match="must be a scalar field"):
        set_frontmatter_scalar(document, "notes", "done")


# --------------------------------------------------------------------------- #
# reparse-verify safety gate on append_block_to_section
# --------------------------------------------------------------------------- #


def test_append_block_to_section_stray_comment_marker_refuses_unclosed_construct():
    document = parse_note_source(LF_NOTE)
    with pytest.raises(OawError, match="unclosed construct"):
        append_block_to_section(document, "## Notes", "%%")


def test_append_block_to_section_missing_section_refuses_after_unterminated_html_comment():
    source = "# Notes\n\nintro\n\n<!--\nnever closed\n"
    document = parse_note_source(source)
    with pytest.raises(OawError):
        append_block_to_section(document, "## Agent sessions", "- entry")


def test_append_block_to_section_bom_without_frontmatter_appends_inside_section():
    text = "﻿## Notes\n\nbody\n"
    document = parse_note_source(text)
    result = append_block_to_section(document, "## Notes", "- entry")
    assert result.source.count("## Notes") == 1
    assert "- entry" in result.source
