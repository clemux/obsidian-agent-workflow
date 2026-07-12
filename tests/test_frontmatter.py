import pytest

from oaw.errors import OawError
from oaw.frontmatter import (
    append_frontmatter_list_value,
    parse_frontmatter,
    parse_yaml_string_list_item,
    set_frontmatter_scalar,
)


def test_parse_frontmatter_keeps_hand_rolled_scalar_and_list_behavior():
    frontmatter = """# comment
id: 'OAW-TSK-example'
aliases: [OAW-TSK-example, "Example"]
tags:
  - projects
  - 'workflow'
indented: ignored
  child: value
"""

    assert parse_frontmatter(frontmatter) == {
        "id": "OAW-TSK-example",
        "aliases": ["OAW-TSK-example", "Example"],
        "tags": ["projects", "workflow"],
        "indented": "ignored",
    }


def test_set_frontmatter_scalar_replaces_or_inserts_without_changing_body():
    original = "---\nstatus: todo\nid: example\n---\nBody\n"

    replaced = set_frontmatter_scalar(original, "status", "active")
    inserted = set_frontmatter_scalar(replaced, "priority", "2")

    assert replaced == "---\nstatus: active\nid: example\n---\nBody\n"
    assert inserted == "---\nstatus: active\nid: example\npriority: 2\n---\nBody\n"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Body\n", "task note has no YAML frontmatter"),
        ("---\nstatus: todo\n", "task note frontmatter is not closed"),
    ],
)
def test_set_frontmatter_scalar_rejects_missing_or_unclosed_frontmatter(text, message):
    with pytest.raises(OawError, match=message):
        set_frontmatter_scalar(text, "status", "active")


def test_append_frontmatter_list_value_adds_deduplicated_quoted_value():
    original = "---\nsession-ids:\n  # retained\n  - existing\n---\nBody\n"

    updated = append_frontmatter_list_value(original, "session-ids", "new id")

    assert updated == ('---\nsession-ids:\n  # retained\n  - existing\n  - "new id"\n---\nBody\n')
    assert append_frontmatter_list_value(updated, "session-ids", "new id") == updated


def test_append_frontmatter_list_value_creates_missing_block_list():
    original = "---\nid: example\n---\nBody\n"

    assert append_frontmatter_list_value(original, "aliases", "éxample") == (
        '---\nid: example\naliases:\n  - "éxample"\n---\nBody\n'
    )


@pytest.mark.parametrize("item", ["true", "42", "2026-07-12", "[nested]"])
def test_parse_yaml_string_list_item_rejects_ambiguous_non_strings(item):
    with pytest.raises(OawError, match="must contain only unambiguous string list items"):
        parse_yaml_string_list_item(item, "session-ids")


def test_append_frontmatter_list_value_rejects_inline_list():
    with pytest.raises(OawError, match="must use a YAML block list"):
        append_frontmatter_list_value(
            "---\nsession-ids: [existing]\n---\n",
            "session-ids",
            "new",
        )
