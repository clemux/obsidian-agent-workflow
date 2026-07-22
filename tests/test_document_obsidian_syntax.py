"""Tests for the pure Obsidian-syntax recognizers in oaw.document.obsidian_syntax."""

from __future__ import annotations

from oaw.document.obsidian_syntax import (
    ObsidianSpanKind,
    find_block_ids,
    find_comments,
    find_markdown_links,
    find_math,
    find_tags,
    find_wikilinks,
)
from oaw.document.types import SourceSpan


def whole(text: str) -> SourceSpan:
    return SourceSpan(0, len(text))


# --------------------------------------------------------------------------- #
# Wikilinks and embeds
# --------------------------------------------------------------------------- #


def test_wikilink_basic():
    text = "See [[Target]] please"
    spans = find_wikilinks(text, whole(text))
    assert len(spans) == 1
    span = spans[0]
    assert span.kind == ObsidianSpanKind.WIKILINK
    assert span.target == "Target"
    assert span.alias is None
    assert text[span.span.start : span.span.end] == "[[Target]]"


def test_wikilink_with_alias():
    text = "[[Target|Alias Text]]"
    (span,) = find_wikilinks(text, whole(text))
    assert span.target == "Target"
    assert span.alias == "Alias Text"


def test_wikilink_heading_and_block_ref_in_target():
    text = "[[Target#Heading]] and [[Target#^abc123]]"
    spans = find_wikilinks(text, whole(text))
    assert [s.target for s in spans] == ["Target#Heading", "Target#^abc123"]


def test_wikilink_unicode_target():
    text = "[[日本語のノート]]"
    (span,) = find_wikilinks(text, whole(text))
    assert span.target == "日本語のノート"


def test_wikilink_escaped_is_not_recognized():
    text = r"\[[Target]]"
    spans = find_wikilinks(text, whole(text))
    assert spans == ()


def test_embed_basic():
    text = "![[Attachment.png]]"
    (span,) = find_wikilinks(text, whole(text))
    assert span.kind == ObsidianSpanKind.EMBED
    assert span.target == "Attachment.png"
    assert span.span == SourceSpan(0, len(text))


def test_embed_escaped_does_not_leak_inner_wikilink():
    text = r"\![[Target]]"
    spans = find_wikilinks(text, whole(text))
    assert spans == ()


def test_nested_wikilink_innermost_pair_wins():
    text = "[[a[[b]]"
    (span,) = find_wikilinks(text, whole(text))
    assert span.target == "b"
    assert text[span.span.start : span.span.end] == "[[b]]"
    assert span.span.start == 3


def test_wikilink_content_does_not_cross_newline():
    text = "[[a\nb]]"
    spans = find_wikilinks(text, whole(text))
    assert spans == ()


def test_wikilink_unclosed_yields_no_match():
    text = "[[Target"
    spans = find_wikilinks(text, whole(text))
    assert spans == ()


# --------------------------------------------------------------------------- #
# Markdown inline links
# --------------------------------------------------------------------------- #


def test_markdown_link_basic():
    text = "See [some text](https://example.com) now"
    (span,) = find_markdown_links(text, whole(text))
    assert span.kind == ObsidianSpanKind.MARKDOWN_LINK
    assert span.target == "https://example.com"
    assert text[span.span.start : span.span.end] == "[some text](https://example.com)"


def test_markdown_link_one_level_of_paren_nesting():
    text = "[text](https://example.com/(paren))"
    (span,) = find_markdown_links(text, whole(text))
    assert span.target == "https://example.com/(paren)"


def test_markdown_link_too_deep_nesting_does_not_match():
    text = "[text](a(b(c)))"
    spans = find_markdown_links(text, whole(text))
    assert spans == ()


def test_markdown_link_reference_style_not_matched():
    text = "[text][ref]\n\n[ref]: https://example.com"
    spans = find_markdown_links(text, whole(text))
    assert spans == ()


def test_markdown_link_label_does_not_cross_newline():
    text = "[te\nxt](url)"
    spans = find_markdown_links(text, whole(text))
    assert spans == ()


def test_markdown_link_without_parens_not_matched():
    text = "[just a label] no link here"
    spans = find_markdown_links(text, whole(text))
    assert spans == ()


# --------------------------------------------------------------------------- #
# Block IDs
# --------------------------------------------------------------------------- #


def test_block_id_at_end_of_line():
    text = "Some paragraph text ^abc-123\nNext line"
    (span,) = find_block_ids(text, whole(text))
    assert span.kind == ObsidianSpanKind.BLOCK_ID
    assert span.target == "abc-123"
    assert text[span.span.start : span.span.end] == " ^abc-123"


def test_block_id_at_end_of_region_without_trailing_newline():
    text = "Last line ^tail99"
    (span,) = find_block_ids(text, whole(text))
    assert span.target == "tail99"


def test_block_id_requires_preceding_space():
    text = "textwithout^id\n"
    spans = find_block_ids(text, whole(text))
    assert spans == ()


def test_block_id_not_matched_mid_line():
    text = "before ^id123 after\n"
    spans = find_block_ids(text, whole(text))
    assert spans == ()


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #


def test_comment_closed_single_line():
    text = "before %%hidden text%% after"
    (span,) = find_comments(text, whole(text))
    assert span.kind == ObsidianSpanKind.COMMENT
    assert span.closed is True
    assert text[span.span.start : span.span.end] == "%%hidden text%%"


def test_comment_spans_lines():
    text = "before %%line one\nline two%% after"
    (span,) = find_comments(text, whole(text))
    assert span.closed is True
    assert text[span.span.start : span.span.end] == "%%line one\nline two%%"


def test_comment_unclosed_captures_to_region_end():
    text = "before %%hidden and never closed"
    (span,) = find_comments(text, whole(text))
    assert span.closed is False
    assert span.span.end == len(text)
    assert span.span.start == text.index("%%")


def test_comment_toggle_pairs_two_comments():
    text = "%%first%% keep %%second%%"
    spans = find_comments(text, whole(text))
    assert len(spans) == 2
    assert all(s.closed for s in spans)


# --------------------------------------------------------------------------- #
# Math
# --------------------------------------------------------------------------- #


def test_math_inline_basic():
    text = "energy $x^2$ here"
    (span,) = find_math(text, whole(text))
    assert span.kind == ObsidianSpanKind.MATH_INLINE
    assert text[span.span.start : span.span.end] == "$x^2$"


def test_math_inline_rejects_internal_leading_and_trailing_space():
    text = "$5 and $6"
    spans = find_math(text, whole(text))
    assert spans == ()


def test_math_inline_rejects_empty_content():
    text = "$$"
    spans = find_math(text, whole(text))
    # "$$" alone is an (unclosed) math block, never empty inline math.
    assert len(spans) == 1
    assert spans[0].kind == ObsidianSpanKind.MATH_BLOCK


def test_math_block_single_line():
    text = "$$x^2$$"
    (span,) = find_math(text, whole(text))
    assert span.kind == ObsidianSpanKind.MATH_BLOCK
    assert span.closed is True
    assert text[span.span.start : span.span.end] == "$$x^2$$"


def test_math_block_spans_lines():
    text = "$$\nx^2\n$$"
    (span,) = find_math(text, whole(text))
    assert span.kind == ObsidianSpanKind.MATH_BLOCK
    assert span.closed is True
    assert span.span == SourceSpan(0, len(text))


def test_math_block_unclosed_captures_to_region_end():
    text = "$$\nx^2 and no closing"
    (span,) = find_math(text, whole(text))
    assert span.closed is False
    assert span.span.end == len(text)


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #


def test_tag_basic_at_start_of_string():
    text = "#project more text"
    (span,) = find_tags(text, whole(text))
    assert span.kind == ObsidianSpanKind.TAG
    assert span.target == "project"


def test_tag_after_whitespace():
    text = "see #project/area-one now"
    (span,) = find_tags(text, whole(text))
    assert span.target == "project/area-one"


def test_tag_requires_preceding_whitespace():
    text = "see a#project now"
    spans = find_tags(text, whole(text))
    assert spans == ()


def test_tag_all_digits_is_not_a_tag():
    text = "#123"
    spans = find_tags(text, whole(text))
    assert spans == ()


def test_tag_mixed_digits_and_letters_is_a_tag():
    text = "#2024-report"
    (span,) = find_tags(text, whole(text))
    assert span.target == "2024-report"


# --------------------------------------------------------------------------- #
# Region slicing
# --------------------------------------------------------------------------- #


def test_region_slicing_limits_wikilink_matches():
    text = "[[First]] middle [[Second]]"
    first_end = text.index("]]") + 2
    region = SourceSpan(0, first_end)
    spans = find_wikilinks(text, region)
    assert [s.target for s in spans] == ["First"]


def test_region_slicing_limits_tag_matches():
    text = "#one #two #three"
    region = SourceSpan(0, text.index("#two"))
    spans = find_tags(text, region)
    assert [s.target for s in spans] == ["one"]


def test_region_slicing_offsets_are_absolute():
    text = "prefix text [[Target]]"
    offset = text.index("[[")
    region = SourceSpan(offset, len(text))
    (span,) = find_wikilinks(text, region)
    assert span.span.start == offset


# --------------------------------------------------------------------------- #
# CRLF sources
# --------------------------------------------------------------------------- #


def test_block_id_with_crlf_line_ending():
    text = "line one\r\ntext ^abc-1\r\nline three\r\n"
    (span,) = find_block_ids(text, whole(text))
    assert span.target == "abc-1"
    assert text[span.span.start : span.span.end] == " ^abc-1"


def test_comment_unclosed_with_crlf():
    text = "before\r\n%%hidden forever"
    (span,) = find_comments(text, whole(text))
    assert span.closed is False
    assert span.span.end == len(text)


def test_math_block_spans_crlf_lines():
    text = "$$\r\nx^2\r\n$$"
    (span,) = find_math(text, whole(text))
    assert span.closed is True
    assert span.span == SourceSpan(0, len(text))


def test_wikilink_unaffected_by_crlf_elsewhere():
    text = "line one\r\n[[Target]]\r\nline three\r\n"
    (span,) = find_wikilinks(text, whole(text))
    assert span.target == "Target"
