from pathlib import Path

from oaw.notes import read_note, split_note


def test_split_note_returns_frontmatter_block_content_and_body():
    text = "---\nid: OAW-TSK-example\n---\n\n# Example\n"

    block, frontmatter, body = split_note(text)

    assert block == "---\nid: OAW-TSK-example\n---\n"
    assert frontmatter == "id: OAW-TSK-example\n"
    assert body == "\n# Example\n"


def test_split_note_leaves_plain_or_unclosed_notes_untouched():
    for text in ("# Plain\n", "---\nid: unclosed\n"):
        assert split_note(text) == ("", "", text)


def test_read_note_returns_text_sections(tmp_path: Path):
    path = tmp_path / "Example.md"
    text = "---\nid: example\naliases:\n  - one\n---\nBody\n"
    path.write_text(text, encoding="utf-8")

    assert read_note(path) == (text, "id: example\naliases:\n  - one\n", "Body\n")
