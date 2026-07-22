"""Tests for oaw.document.markdown.parse_markdown and obsidian_plugin."""

from __future__ import annotations

from markdown_it import MarkdownIt

from oaw.document.markdown import MarkdownStructure, parse_markdown
from oaw.document.obsidian_plugin import obsidian_plugin
from oaw.document.obsidian_syntax import find_tags, find_wikilinks
from oaw.document.types import SourceIndex, SourceSpan

FRONTMATTER = "---\nid: OAW-TSK-example\ntype: task\n---\n"


def _parse(body: str, *, frontmatter: str = "") -> tuple[str, MarkdownStructure]:
    """Parse ``body`` as the note body, optionally preceded by ``frontmatter``."""
    source = frontmatter + body
    index = SourceIndex.build(source)
    body_span = SourceSpan(len(frontmatter), len(source))
    return source, parse_markdown(source, body_span, index)


def _slice(source: str, span: SourceSpan) -> str:
    return source[span.start : span.end]


def _codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


# --- headings -----------------------------------------------------------------


def test_atx_heading_span_and_content_span_without_closing_hashes() -> None:
    source, structure = _parse("# Title\n\nbody\n")
    assert len(structure.headings) == 1
    heading = structure.headings[0]
    assert heading.level == 1
    assert heading.marker == "atx"
    assert heading.text == "Title"
    assert _slice(source, heading.content_span) == "Title"
    assert _slice(source, heading.span) == "# Title\n"


def test_atx_heading_with_closing_hashes_and_extra_spacing() -> None:
    source, structure = _parse("##    Heading with closing   ##\n")
    heading = structure.headings[0]
    assert heading.level == 2
    assert heading.text == "Heading with closing"
    assert _slice(source, heading.content_span) == "Heading with closing"


def test_atx_heading_levels_one_through_six() -> None:
    body = "\n".join(f"{'#' * level} H{level}" for level in range(1, 7)) + "\n"
    _source, structure = _parse(body)
    assert [h.level for h in structure.headings] == [1, 2, 3, 4, 5, 6]
    assert [h.text for h in structure.headings] == [f"H{i}" for i in range(1, 7)]


def test_setext_heading_level_one_and_two() -> None:
    source, structure = _parse("Heading One\n===\n\nHeading Two\n---\n")
    h1, h2 = structure.headings
    assert h1.level == 1
    assert h1.marker == "setext"
    assert h1.text == "Heading One"
    assert _slice(source, h1.span) == "Heading One\n===\n"
    assert h2.level == 2
    assert h2.marker == "setext"
    assert h2.text == "Heading Two"


def test_atx_heading_absolute_offset_with_nonzero_frontmatter_offset() -> None:
    source, structure = _parse("# Title\n", frontmatter=FRONTMATTER)
    heading = structure.headings[0]
    assert heading.span.start == len(FRONTMATTER)
    assert _slice(source, heading.span) == "# Title\n"
    assert _slice(source, heading.content_span) == "Title"


def test_heading_spans_on_crlf_source() -> None:
    source, structure = _parse("# Title\r\n\r\nSetext\r\nHeading\r\n===\r\n")
    atx, setext = structure.headings
    assert _slice(source, atx.span) == "# Title\r\n"
    assert _slice(source, atx.content_span) == "Title"
    assert _slice(source, setext.span) == "Setext\r\nHeading\r\n===\r\n"
    assert _slice(source, setext.content_span) == "Setext\r\nHeading"


# --- fences ---------------------------------------------------------------


def test_closed_backtick_fence_region() -> None:
    source, structure = _parse("```python\ncode here\n```\n")
    fences = [r for r in structure.regions if r.kind == "fence"]
    assert len(fences) == 1
    fence = fences[0]
    assert fence.closed is True
    assert fence.info == "python"
    assert _slice(source, fence.span) == "```python\ncode here\n```\n"
    assert structure.diagnostics == ()


def test_closed_tilde_fence_with_longer_closing_run() -> None:
    source, structure = _parse("~~~\ntilde content\n~~~~\n")
    fence = next(r for r in structure.regions if r.kind == "fence")
    assert fence.closed is True
    assert _slice(source, fence.span) == "~~~\ntilde content\n~~~~\n"


def test_unclosed_fence_captures_to_end_of_region_with_diagnostic() -> None:
    source, structure = _parse("intro\n\n```python\nnever closed\n")
    fence = next(r for r in structure.regions if r.kind == "fence")
    assert fence.closed is False
    assert _slice(source, fence.span) == "```python\nnever closed\n"
    assert "markdown.unclosed-fence" in _codes(structure.diagnostics)
    diag = next(d for d in structure.diagnostics if d.code == "markdown.unclosed-fence")
    assert diag.span == fence.span


def test_unclosed_fence_with_no_trailing_newline_at_eof() -> None:
    _source, structure = _parse("```\nunterminated at eof, no newline")
    fence = next(r for r in structure.regions if r.kind == "fence")
    assert fence.closed is False


# --- indented code --------------------------------------------------------


def test_indented_code_region() -> None:
    source, structure = _parse("para\n\n    indented code line\n")
    region = next(r for r in structure.regions if r.kind == "indented-code")
    assert _slice(source, region.span) == "    indented code line\n"
    assert region.closed is True


# --- html blocks ------------------------------------------------------------


def test_unclosed_html_comment_block_region_is_open_with_diagnostic() -> None:
    _source, structure = _parse("<!--\nnever closed\n")
    html_regions = [r for r in structure.regions if r.kind == "html-block"]
    assert len(html_regions) == 1
    assert html_regions[0].closed is False
    assert "markdown.unclosed-html-block" in _codes(structure.diagnostics)


def test_closed_html_comment_block_region_stays_closed() -> None:
    _source, structure = _parse("<!-- x -->\n")
    html_regions = [r for r in structure.regions if r.kind == "html-block"]
    assert len(html_regions) == 1
    assert html_regions[0].closed is True


# --- BOM without frontmatter ------------------------------------------------


def test_bom_without_frontmatter_heading_span_offset_and_slice_equality() -> None:
    bom = "﻿"
    source, structure = _parse("## Notes\n\nbody\n", frontmatter=bom)
    heading = structure.headings[0]
    assert heading.span.start == 1
    assert _slice(source, heading.span) == "## Notes\n"
    assert heading.text == _slice(source, heading.content_span)
    assert heading.text == "Notes"


# --- tables (GFM) -----------------------------------------------------------


def test_table_region_and_cell_inline_code() -> None:
    source, structure = _parse("| a | b |\n|---|---|\n| 1 | `code` |\n")
    table = next(r for r in structure.regions if r.kind == "table")
    assert _slice(source, table.span) == "| a | b |\n|---|---|\n| 1 | `code` |\n"
    assert len(structure.inline_code_spans) == 1
    assert _slice(source, structure.inline_code_spans[0]) == "`code`"


# --- blockquotes and lists --------------------------------------------------


def test_blockquote_nesting_with_fence_inside() -> None:
    body = "> outer\n> ```\n> fenced in quote\n> ```\n"
    source, structure = _parse(body)
    blockquote = next(r for r in structure.regions if r.kind == "blockquote")
    fence = next(r for r in structure.regions if r.kind == "fence")
    assert blockquote.span.contains(fence.span)
    assert _slice(source, blockquote.span) == body


def test_list_item_structure_with_nested_list() -> None:
    body = "- item 1\n- item 2\n  - nested\n"
    _source, structure = _parse(body)
    lists = [r for r in structure.regions if r.kind == "list"]
    items = [r for r in structure.regions if r.kind == "list-item"]
    assert len(lists) == 2  # outer bullet list + nested bullet list
    assert len(items) == 3  # item 1, item 2, nested
    # The outer list region contains every list-item region.
    outer_list = lists[0]
    assert all(outer_list.span.contains(item.span) for item in items)


# --- inline code spans -------------------------------------------------------


def test_inline_code_span_in_paragraph() -> None:
    source, structure = _parse("Some para with `code` and more text.\n")
    assert len(structure.inline_code_spans) == 1
    assert _slice(source, structure.inline_code_spans[0]) == "`code`"
    assert structure.diagnostics == ()


def test_multiple_inline_code_spans_with_duplicate_content() -> None:
    source, structure = _parse("first `a` then `a` again and `` b`c `` end.\n")
    spans = structure.inline_code_spans
    assert len(spans) == 3
    texts = [_slice(source, s) for s in spans]
    assert texts == ["`a`", "`a`", "`` b`c ``"]
    # The two "`a`" spans must be distinct, later occurrences must not
    # collapse onto the first.
    assert spans[0] != spans[1]
    assert spans[0].start < spans[1].start


def test_inline_code_span_in_heading() -> None:
    source, structure = _parse("# Heading with `code` inside\n")
    assert len(structure.inline_code_spans) == 1
    assert _slice(source, structure.inline_code_spans[0]) == "`code`"


def test_inline_code_span_absolute_offset_with_frontmatter() -> None:
    source, structure = _parse("Para with `x` here.\n", frontmatter=FRONTMATTER)
    span = structure.inline_code_spans[0]
    assert span.start >= len(FRONTMATTER)
    assert _slice(source, span) == "`x`"


def test_crlf_multiline_inline_code_span_is_exact_not_inexact() -> None:
    source, structure = _parse("para `code\r\nmore` and more text.\r\n")
    assert _codes(structure.diagnostics) == []
    assert len(structure.inline_code_spans) == 1
    assert _slice(source, structure.inline_code_spans[0]) == "`code\r\nmore`"


# --- tokens are passed through opaquely -------------------------------------


def test_tokens_tuple_is_nonempty_and_opaque() -> None:
    _source, structure = _parse("# Title\n\npara\n")
    assert len(structure.tokens) > 0
    assert structure.tokens[0].type == "heading_open"


# --- obsidian_plugin parity --------------------------------------------------


def test_obsidian_plugin_token_parity_with_pure_recognizers() -> None:
    source = "See [[Target|alias]] and ![[embed.png]] and #tag1 done.\n"
    md = MarkdownIt("commonmark").use(obsidian_plugin)
    tokens = md.parse(source)

    inline = next(t for t in tokens if t.type == "inline")
    plugin_matches = {
        (child.meta["kind"], child.meta["target"], child.meta.get("alias"))
        for child in (inline.children or [])
        if child.type in ("obsidian_wikilink", "obsidian_embed", "obsidian_tag")
    }

    region = SourceSpan(0, len(source))
    recognizer_matches = {
        (span.kind.value, span.target, span.alias) for span in find_wikilinks(source, region)
    } | {(span.kind.value, span.target, span.alias) for span in find_tags(source, region)}

    assert plugin_matches == recognizer_matches
    assert plugin_matches == {
        ("wikilink", "Target", "alias"),
        ("embed", "embed.png", None),
        ("tag", "tag1", None),
    }


def test_obsidian_plugin_does_not_affect_parse_markdown_parser() -> None:
    # obsidian_plugin must be a fully separate MarkdownIt instance from the
    # one parse_markdown uses internally; wikilink brackets should stay
    # inert (plain text tokens) under parse_markdown.
    _source, structure = _parse("See [[Target]] here.\n")
    inline = next(t for t in structure.tokens if t.type == "inline")
    assert all(
        child.type not in ("obsidian_wikilink", "obsidian_embed")
        for child in (inline.children or [])
    )
