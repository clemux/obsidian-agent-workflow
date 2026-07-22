"""Tests for oaw.document.frontmatter_yaml.compose_frontmatter and friends."""

from __future__ import annotations

from oaw.document.envelope import BOM, scan_envelope
from oaw.document.frontmatter_yaml import (
    OWNED_FIELD_TYPES,
    FieldKind,
    compose_frontmatter,
    validate_owned_fields,
)
from oaw.document.types import Severity


def _compose(source: str):
    """Compose frontmatter the way the model layer will: via the real envelope."""
    envelope = scan_envelope(source)
    assert envelope.frontmatter_inner_span is not None, "fixture must have frontmatter"
    return compose_frontmatter(source, envelope.frontmatter_inner_span)


def _codes(model) -> list[str]:
    return [d.code for d in model.diagnostics]


# --------------------------------------------------------------------------- #
# Absolute span correctness: marks -> whole-note offsets
# --------------------------------------------------------------------------- #


def test_lf_spans_are_absolute_into_whole_source() -> None:
    source = "---\nkey: value\n---\nbody\n"
    model = _compose(source)
    field = model.field("key")
    assert field is not None
    assert source[field.key_span.start : field.key_span.end] == "key"
    assert field.value_span is not None
    assert source[field.value_span.start : field.value_span.end] == "value"
    assert field.scalar == "value"


def test_crlf_spans_are_absolute_into_whole_source() -> None:
    source = "---\r\nkey: value\r\nfoo:\r\n  - a\r\n  - b\r\n---\r\nbody\r\n"
    model = _compose(source)
    key_field = model.field("key")
    assert key_field is not None
    assert key_field.value_span is not None
    assert source[key_field.value_span.start : key_field.value_span.end] == "value"

    foo_field = model.field("foo")
    assert foo_field is not None
    assert foo_field.kind is FieldKind.STRING_LIST
    assert foo_field.items == ("a", "b")
    assert foo_field.item_spans is not None
    assert [source[s.start : s.end] for s in foo_field.item_spans] == ["a", "b"]


def test_bom_and_crlf_offsets_are_correct() -> None:
    source = BOM + "---\r\nid: OAW-TSK-x\r\n---\r\nbody\r\n"
    model = _compose(source)
    field = model.field("id")
    assert field is not None
    assert field.value_span is not None
    assert source[field.value_span.start : field.value_span.end] == "OAW-TSK-x"
    # Offsets must land past the BOM, not treat it as part of the YAML text.
    assert field.key_span.start == source.index("id:")


def test_entry_span_covers_full_key_line_including_newline() -> None:
    source = "---\nkey: value\nother: 1\n---\nbody\n"
    model = _compose(source)
    field = model.field("key")
    assert field is not None
    assert source[field.entry_span.start : field.entry_span.end] == "key: value\n"


def test_entry_span_for_block_list_covers_all_item_lines() -> None:
    source = "---\nfoo:\n  - a\n  - b\nbar: 1\n---\nbody\n"
    model = _compose(source)
    foo = model.field("foo")
    assert foo is not None
    assert source[foo.entry_span.start : foo.entry_span.end] == "foo:\n  - a\n  - b\n"
    bar = model.field("bar")
    assert bar is not None
    assert source[bar.entry_span.start : bar.entry_span.end] == "bar: 1\n"


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #


def test_duplicate_key_recorded_and_field_returns_none() -> None:
    source = "---\nstatus: draft\nstatus: done\n---\nbody\n"
    model = _compose(source)
    assert model.field("status") is None
    assert model.duplicated_keys() == ("status",)
    assert "frontmatter.duplicate-key" in _codes(model)
    dup_fields = [f for f in model.fields if f.key == "status"]
    assert len(dup_fields) == 2
    diag = next(d for d in model.diagnostics if d.code == "frontmatter.duplicate-key")
    assert diag.severity is Severity.ERROR


def test_no_duplicates_reports_empty_tuple() -> None:
    source = "---\nkey: value\n---\nbody\n"
    model = _compose(source)
    assert model.duplicated_keys() == ()


# --------------------------------------------------------------------------- #
# Anchors, aliases, explicit tags, merge keys -> unsupported-node
# --------------------------------------------------------------------------- #


def test_anchor_and_alias_both_flagged_and_fields_become_other() -> None:
    source = "---\na: &anc value\nb: *anc\n---\nbody\n"
    model = _compose(source)
    field_a = next(f for f in model.fields if f.key == "a")
    field_b = next(f for f in model.fields if f.key == "b")
    assert field_a.kind is FieldKind.OTHER
    assert field_b.kind is FieldKind.OTHER
    unsupported = [d for d in model.diagnostics if d.code == "frontmatter.unsupported-node"]
    assert len(unsupported) == 2
    assert all(d.severity is Severity.ERROR for d in unsupported)


def test_explicit_tag_flags_unsupported_node() -> None:
    source = "---\na: !custom foo\n---\nbody\n"
    model = _compose(source)
    field = model.field("a")
    assert field is not None
    assert field.kind is FieldKind.OTHER
    assert "frontmatter.unsupported-node" in _codes(model)


def test_merge_key_flagged_even_without_alias() -> None:
    source = "---\n<<: {a: 1}\nb: 2\n---\nbody\n"
    model = _compose(source)
    merge_field = next(f for f in model.fields if f.key == "<<")
    assert merge_field.kind is FieldKind.OTHER
    assert "frontmatter.unsupported-node" in _codes(model)
    # Unrelated sibling field is unaffected.
    other = model.field("b")
    assert other is not None
    assert other.kind is FieldKind.SCALAR


# --------------------------------------------------------------------------- #
# Nested maps, flow lists, block lists with comments, quoted scalars
# --------------------------------------------------------------------------- #


def test_nested_mapping_value_is_other() -> None:
    source = "---\na:\n  nested: 1\n---\nbody\n"
    model = _compose(source)
    field = model.field("a")
    assert field is not None
    assert field.kind is FieldKind.OTHER
    assert field.scalar is None
    assert field.items is None


def test_flow_list_is_string_list_when_all_items_are_strings() -> None:
    source = "---\ntags: [alpha, beta]\n---\nbody\n"
    model = _compose(source)
    field = model.field("tags")
    assert field is not None
    assert field.kind is FieldKind.STRING_LIST
    assert field.items == ("alpha", "beta")


def test_flow_list_with_mixed_types_is_other() -> None:
    source = "---\nmixed: [1, two]\n---\nbody\n"
    model = _compose(source)
    field = model.field("mixed")
    assert field is not None
    assert field.kind is FieldKind.OTHER
    assert field.items is None


def test_block_list_with_interleaved_comment_is_string_list() -> None:
    source = "---\ntags:\n  - alpha\n  # a comment\n  - beta\n---\nbody\n"
    model = _compose(source)
    field = model.field("tags")
    assert field is not None
    assert field.kind is FieldKind.STRING_LIST
    assert field.items == ("alpha", "beta")


def test_quoted_scalar_with_hash_is_not_treated_as_comment() -> None:
    source = '---\nnote: "value # not a comment"\n---\nbody\n'
    model = _compose(source)
    field = model.field("note")
    assert field is not None
    assert field.kind is FieldKind.SCALAR
    assert field.scalar == "value # not a comment"


def test_quoted_list_items_qualify_as_string_list() -> None:
    source = "---\ntags: [\"a b\", 'c d']\n---\nbody\n"
    model = _compose(source)
    field = model.field("tags")
    assert field is not None
    assert field.kind is FieldKind.STRING_LIST
    assert field.items == ("a b", "c d")


# --------------------------------------------------------------------------- #
# Block scalars, empty value, non-string implicit scalars
# --------------------------------------------------------------------------- #


def test_block_scalar_is_scalar_kind_with_correct_span() -> None:
    source = "---\nbody_text: |\n  line one\n  line two\n---\nbody\n"
    model = _compose(source)
    field = model.field("body_text")
    assert field is not None
    assert field.kind is FieldKind.SCALAR
    assert field.scalar == "line one\nline two\n"


def test_empty_value_has_no_span_and_no_scalar() -> None:
    source = "---\nkey:\nother: 1\n---\nbody\n"
    model = _compose(source)
    field = model.field("key")
    assert field is not None
    assert field.kind is FieldKind.SCALAR
    assert field.value_span is None
    assert field.scalar is None


def test_non_string_implicit_scalar_keeps_scalar_kind_no_diagnostic() -> None:
    source = "---\ncount: 5\nflag: true\n---\nbody\n"
    model = _compose(source)
    count = model.field("count")
    flag = model.field("flag")
    assert count is not None and count.kind is FieldKind.SCALAR and count.scalar == "5"
    assert flag is not None and flag.kind is FieldKind.SCALAR and flag.scalar == "true"
    assert model.diagnostics == ()


def test_tabs_in_frontmatter_produce_yaml_error_not_exception() -> None:
    source = "---\n\ta: 1\n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert "frontmatter.yaml-error" in _codes(model)
    diag = next(d for d in model.diagnostics if d.code == "frontmatter.yaml-error")
    assert diag.severity is Severity.ERROR
    assert diag.span is not None


# --------------------------------------------------------------------------- #
# YAML error recovery, not-a-mapping, empty frontmatter
# --------------------------------------------------------------------------- #


def test_malformed_yaml_never_raises_and_records_diagnostic() -> None:
    source = "---\na: b: c\n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert "frontmatter.yaml-error" in _codes(model)


def test_scalar_root_is_not_a_mapping() -> None:
    source = "---\njust a plain scalar\n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert "frontmatter.not-a-mapping" in _codes(model)
    diag = next(d for d in model.diagnostics if d.code == "frontmatter.not-a-mapping")
    assert diag.severity is Severity.ERROR


def test_sequence_root_is_not_a_mapping() -> None:
    source = "---\n- one\n- two\n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert "frontmatter.not-a-mapping" in _codes(model)


def test_empty_frontmatter_has_no_fields_and_no_diagnostics() -> None:
    source = "---\n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert model.diagnostics == ()


def test_whitespace_only_frontmatter_has_no_fields() -> None:
    source = "---\n   \n---\nbody\n"
    model = _compose(source)
    assert model.fields == ()
    assert model.diagnostics == ()


# --------------------------------------------------------------------------- #
# FrontmatterModel convenience accessors
# --------------------------------------------------------------------------- #


def test_value_returns_scalar_string() -> None:
    source = "---\nstatus: active\n---\nbody\n"
    model = _compose(source)
    assert model.value("status") == "active"


def test_value_returns_list_for_string_list() -> None:
    source = "---\ntags: [a, b]\n---\nbody\n"
    model = _compose(source)
    assert model.value("tags") == ["a", "b"]


def test_value_returns_none_for_absent_key() -> None:
    source = "---\nstatus: active\n---\nbody\n"
    model = _compose(source)
    assert model.value("missing") is None


def test_value_returns_none_for_other_kind() -> None:
    source = "---\nnested:\n  a: 1\n---\nbody\n"
    model = _compose(source)
    assert model.value("nested") is None


# --------------------------------------------------------------------------- #
# safe_to_rewrite
# --------------------------------------------------------------------------- #


def test_safe_to_rewrite_true_for_simple_scalar() -> None:
    source = "---\nstatus: active\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("status") is True


def test_safe_to_rewrite_false_for_duplicate_key() -> None:
    source = "---\nstatus: a\nstatus: b\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("status") is False


def test_safe_to_rewrite_false_for_other_kind() -> None:
    source = "---\nnested:\n  a: 1\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("nested") is False


def test_safe_to_rewrite_false_for_unsupported_node() -> None:
    source = "---\na: &anc value\nb: *anc\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("a") is False
    assert model.safe_to_rewrite("b") is False


def test_safe_to_rewrite_false_for_missing_key() -> None:
    source = "---\nstatus: active\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("missing") is False


def test_safe_to_rewrite_false_when_entries_share_a_line() -> None:
    source = "---\n{a: 1, b: 2}\n---\nbody\n"
    model = _compose(source)
    # Root itself is a flow mapping: both fields' entry spans land on the same line.
    assert model.safe_to_rewrite("a") is False
    assert model.safe_to_rewrite("b") is False


def test_safe_to_rewrite_true_for_string_list() -> None:
    source = "---\ntags:\n  - a\n  - b\n---\nbody\n"
    model = _compose(source)
    assert model.safe_to_rewrite("tags") is True


def test_block_scalar_field_has_scalar_style_and_is_not_safe_to_rewrite() -> None:
    source = "---\nnotes: |\n  line one\n  line two\n---\nbody\n"
    model = _compose(source)
    field = model.field("notes")
    assert field is not None
    assert field.kind is FieldKind.SCALAR
    assert field.scalar_style == "|"
    assert model.safe_to_rewrite("notes") is False


def test_list_containing_a_block_scalar_item_classifies_other() -> None:
    source = "---\nitems:\n  - >\n    folded text\n  - plain\n---\nbody\n"
    model = _compose(source)
    field = model.field("items")
    assert field is not None
    assert field.kind is FieldKind.OTHER


# --------------------------------------------------------------------------- #
# validate_owned_fields / OWNED_FIELD_TYPES
# --------------------------------------------------------------------------- #


def test_owned_field_types_covers_scalar_and_string_list_fields() -> None:
    assert OWNED_FIELD_TYPES["id"] is FieldKind.SCALAR
    assert OWNED_FIELD_TYPES["status"] is FieldKind.SCALAR
    assert OWNED_FIELD_TYPES["tags"] is FieldKind.STRING_LIST
    assert OWNED_FIELD_TYPES["blocked-by"] is FieldKind.STRING_LIST


def test_validate_owned_fields_flags_wrong_kind() -> None:
    source = "---\nid: OAW-TSK-x\ntags: not-a-list\n---\nbody\n"
    model = _compose(source)
    diagnostics = validate_owned_fields(model)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "frontmatter.owned-field-type"
    assert diagnostics[0].severity is Severity.ERROR
    assert "tags" in diagnostics[0].message


def test_validate_owned_fields_passes_when_kinds_match() -> None:
    source = "---\nid: OAW-TSK-x\nstatus: active\ntags: [a, b]\n---\nbody\n"
    model = _compose(source)
    assert validate_owned_fields(model) == ()


def test_validate_owned_fields_ignores_unowned_keys() -> None:
    source = "---\ncustom_field: whatever\n---\nbody\n"
    model = _compose(source)
    assert validate_owned_fields(model) == ()


def test_validate_owned_fields_allows_bare_empty_string_list_field() -> None:
    source = "---\nid: OAW-CAP-x\ndestinations:\n---\nbody\n"
    model = _compose(source)
    assert validate_owned_fields(model) == ()


def test_validate_owned_fields_still_flags_actual_scalar_for_string_list_owner() -> None:
    source = "---\nid: OAW-CAP-x\ndestinations: actual-scalar-text\n---\nbody\n"
    model = _compose(source)
    diagnostics = validate_owned_fields(model)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "frontmatter.owned-field-type"
    assert "destinations" in diagnostics[0].message
