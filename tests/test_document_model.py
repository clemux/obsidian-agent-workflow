"""Tests for oaw.document.model: parse_note_source, parse_note, NoteDocument,
Section, and ProtectedRegion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oaw.document.model import ProtectedRegion, Section, parse_note, parse_note_source
from oaw.document.obsidian_syntax import ObsidianSpanKind
from oaw.document.types import NewlineStyle, Severity, SourceSpan
from oaw.errors import OawError


def _codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


def _kinds(spans) -> list[str]:
    return [s.kind.value if hasattr(s.kind, "value") else s.kind for s in spans]


# --------------------------------------------------------------------------- #
# End-to-end realistic note
# --------------------------------------------------------------------------- #

REALISTIC_NOTE = (
    "---\n"
    "id: OAW-TSK-example\n"
    "type: task\n"
    "status: doing\n"
    "tags:\n"
    "  - alpha\n"
    "  - beta\n"
    "---\n"
    "\n"
    "# Example task\n"
    "\n"
    "See [[Related Note]] and #project-tag for context.\n"
    "\n"
    "## Agent sessions\n"
    "\n"
    "- entry one\n"
    "\n"
    "## Notes\n"
    "\n"
    "```python\n"
    "# not a heading: ## Notes\n"
    "[[Not A Link]]\n"
    "```\n"
    "\n"
    "%% [[Hidden Link]] is hidden %%\n"
)


def test_realistic_note_parses_all_layers() -> None:
    document = parse_note_source(REALISTIC_NOTE)

    assert document.frontmatter is not None
    assert document.frontmatter.value("id") == "OAW-TSK-example"
    assert document.frontmatter.value("tags") == ["alpha", "beta"]

    heading_texts = [h.text for h in document.markdown.headings]
    assert heading_texts == ["Example task", "Agent sessions", "Notes"]

    wikilink_targets = [
        s.target for s in document.obsidian_spans if s.kind is ObsidianSpanKind.WIKILINK
    ]
    assert wikilink_targets == ["Related Note"]

    tag_targets = [s.target for s in document.obsidian_spans if s.kind is ObsidianSpanKind.TAG]
    assert tag_targets == ["project-tag"]

    assert document.newline == "\n"
    assert document.diagnostics == ()


def test_wikilink_inside_fence_is_protected_not_an_obsidian_span() -> None:
    document = parse_note_source(REALISTIC_NOTE)

    fence_regions = [r for r in document.protected_regions if r.kind == "fence"]
    assert len(fence_regions) == 1
    fence_span = fence_regions[0].span
    assert "[[Not A Link]]" in document.slice(fence_span)

    # The fenced wikilink-looking text must not appear among obsidian_spans.
    assert all(document.slice(s.span) != "[[Not A Link]]" for s in document.obsidian_spans)
    # But the fence text itself is protected.
    assert document.is_protected(fence_span)


def test_comment_hides_a_wikilink_from_obsidian_spans() -> None:
    document = parse_note_source(REALISTIC_NOTE)

    hidden_index = REALISTIC_NOTE.index("[[Hidden Link]]")
    hidden_span = SourceSpan(hidden_index, hidden_index + len("[[Hidden Link]]"))

    assert all(
        not s.span.overlaps(hidden_span)
        for s in document.obsidian_spans
        if s.kind is not ObsidianSpanKind.COMMENT
    )
    assert document.is_protected(hidden_span)

    comment_regions = [r for r in document.protected_regions if r.kind == "obsidian-comment"]
    assert len(comment_regions) == 1
    assert comment_regions[0].span.contains(hidden_span)


def test_section_lookup_finds_agent_sessions_section_body() -> None:
    document = parse_note_source(REALISTIC_NOTE)

    section = document.find_section("## Agent sessions")
    assert section is not None
    assert document.slice(section.content_span).strip() == "- entry one"


def test_section_lookup_ignores_decoy_heading_inside_fence() -> None:
    document = parse_note_source(REALISTIC_NOTE)

    section = document.find_section("## Notes")
    assert section is not None
    # The decoy "## Notes" comment inside the fence must not be matched: the
    # real "## Notes" section's content should include the fence itself.
    assert "```python" in document.slice(section.content_span)


def test_section_lookup_bare_text_defaults_to_level_two() -> None:
    document = parse_note_source(REALISTIC_NOTE)
    assert document.find_section("Agent sessions") == document.find_section("## Agent sessions")


def test_section_lookup_missing_heading_returns_none() -> None:
    document = parse_note_source(REALISTIC_NOTE)
    assert document.find_section("## Does Not Exist") is None


def test_section_lookup_last_section_extends_to_body_end() -> None:
    text = "## First\n\ncontent\n\n## Second\n\nmore\n"
    document = parse_note_source(text)
    section = document.find_section("## Second")
    assert section is not None
    assert section.span.end == len(text)


def test_section_lookup_stops_at_next_same_or_higher_level_heading() -> None:
    text = "## Parent\n\n### Child\n\nchild body\n\n## Sibling\n\nsibling body\n"
    document = parse_note_source(text)
    section = document.find_section("## Parent")
    assert section is not None
    assert document.slice(section.content_span).strip() == "### Child\n\nchild body"


def test_section_lookup_tolerates_trailing_heading_whitespace() -> None:
    text = "## Agent sessions   \n\n- entry\n"
    document = parse_note_source(text)
    section = document.find_section("## Agent sessions")
    assert section is not None
    assert document.slice(section.content_span).strip() == "- entry"


# --------------------------------------------------------------------------- #
# CRLF and BOM offset correctness
# --------------------------------------------------------------------------- #


def test_crlf_document_offsets_and_newline_property() -> None:
    text = "---\r\nid: x\r\n---\r\n\r\n# Title\r\n\r\n[[Target]]\r\n"
    document = parse_note_source(text)

    assert document.newline == "\r\n"
    assert document.frontmatter is not None
    assert document.frontmatter.value("id") == "x"

    wikilinks = [s for s in document.obsidian_spans if s.kind is ObsidianSpanKind.WIKILINK]
    assert len(wikilinks) == 1
    assert document.slice(wikilinks[0].span) == "[[Target]]"

    heading = document.markdown.headings[0]
    assert document.slice(heading.span).startswith("# Title")


def test_crlf_multiline_inline_code_does_not_hide_adjacent_wikilink() -> None:
    text = "para `code\r\nmore` and [[Target]] here.\r\n"
    document = parse_note_source(text)

    assert "markdown.inexact-inline-span" not in _codes(document.diagnostics)
    wikilinks = [s for s in document.obsidian_spans if s.kind is ObsidianSpanKind.WIKILINK]
    assert len(wikilinks) == 1
    assert not document.is_protected(wikilinks[0].span)


def test_bom_frontmatter_and_body_offsets_are_correct() -> None:
    text = "﻿---\nid: y\n---\nBody [[Link]] text\n"
    document = parse_note_source(text)

    assert document.envelope.bom == "﻿"
    assert document.frontmatter is not None
    assert document.frontmatter.value("id") == "y"

    wikilinks = [s for s in document.obsidian_spans if s.kind is ObsidianSpanKind.WIKILINK]
    assert len(wikilinks) == 1
    assert document.slice(wikilinks[0].span) == "[[Link]]"
    # The BOM must not leak into the body slice.
    assert not document.slice(document.envelope.body_span).startswith("﻿")


# --------------------------------------------------------------------------- #
# Diagnostics union
# --------------------------------------------------------------------------- #


def test_diagnostics_union_includes_every_layer() -> None:
    text = "﻿---\naliases: &a [one]\nsame: 1\nsame: 2\n---\n# Title\n\n```\nunclosed fence\n"
    document = parse_note_source(text)

    codes = set(_codes(document.diagnostics))
    assert "envelope.bom" in codes
    assert "frontmatter.unsupported-node" in codes
    assert "frontmatter.duplicate-key" in codes
    assert "markdown.unclosed-fence" in codes


def test_diagnostics_are_sorted_by_span_start() -> None:
    text = "﻿---\nsame: 1\nsame: 2\n---\n```\nunclosed\n"
    document = parse_note_source(text)
    starts = [d.span.start for d in document.diagnostics if d.span is not None]
    assert starts == sorted(starts)


def test_unclosed_obsidian_comment_yields_diagnostic_and_protected_region() -> None:
    text = "Before %% never closed\n"
    document = parse_note_source(text)

    assert "obsidian.unclosed-comment" in _codes(document.diagnostics)
    comment_spans = [s for s in document.obsidian_spans if s.kind is ObsidianSpanKind.COMMENT]
    assert len(comment_spans) == 1
    assert comment_spans[0].closed is False

    protected = [r for r in document.protected_regions if r.kind == "obsidian-comment"]
    assert len(protected) == 1
    assert protected[0].closed is False


def test_unclosed_math_block_yields_diagnostic() -> None:
    text = "$$\nx = 1\n"
    document = parse_note_source(text)
    assert "obsidian.unclosed-math" in _codes(document.diagnostics)
    math_regions = [r for r in document.protected_regions if r.kind == "math"]
    assert any(not r.closed for r in math_regions)


def test_unclosed_frontmatter_protects_whole_source_and_still_parses_body() -> None:
    text = "---\nid: unclosed\n\n# Heading Anyway\n"
    document = parse_note_source(text)

    assert document.frontmatter is None
    assert "envelope.unclosed-frontmatter" in _codes(document.diagnostics)

    unclosed_regions = [r for r in document.protected_regions if r.kind == "unclosed-frontmatter"]
    assert len(unclosed_regions) == 1
    assert unclosed_regions[0].closed is False

    # The body is still parsed as markdown despite being wholly protected.
    assert document.markdown.headings
    assert document.markdown.headings[0].text == "Heading Anyway"


# --------------------------------------------------------------------------- #
# ProtectedRegion / is_protected / diagnostics_in / slice
# --------------------------------------------------------------------------- #


def test_is_protected_true_only_for_overlapping_spans() -> None:
    text = "plain text\n\n```\ncode\n```\n\nmore plain\n"
    document = parse_note_source(text)

    fence_start = text.index("```")
    inside_fence = SourceSpan(fence_start + 1, fence_start + 2)
    outside_fence = SourceSpan(0, 5)

    assert document.is_protected(inside_fence)
    assert not document.is_protected(outside_fence)


def test_diagnostics_in_returns_only_overlapping_diagnostics() -> None:
    text = "```\nunclosed\n"
    document = parse_note_source(text)
    fence_span = document.markdown.regions[0].span
    outside = SourceSpan(0, 0)

    in_fence = document.diagnostics_in(fence_span)
    assert any(d.code == "markdown.unclosed-fence" for d in in_fence)
    assert document.diagnostics_in(outside) == ()


def test_slice_returns_exact_source_substring() -> None:
    text = "abcdef"
    document = parse_note_source(text)
    assert document.slice(SourceSpan(1, 4)) == "bcd"


def test_inline_code_span_is_protected_region() -> None:
    text = "Some `code` here.\n"
    document = parse_note_source(text)
    inline_regions = [r for r in document.protected_regions if r.kind == "inline-code"]
    assert len(inline_regions) == 1
    assert document.slice(inline_regions[0].span) == "`code`"


def test_html_comment_vs_html_block_distinguished() -> None:
    text = "<!-- a comment -->\n\n<div>\nblock\n</div>\n"
    document = parse_note_source(text)
    kinds = {r.kind for r in document.protected_regions}
    assert "html-comment" in kinds
    assert "html-block" in kinds


# --------------------------------------------------------------------------- #
# parse_note (file reading)
# --------------------------------------------------------------------------- #


def test_parse_note_reads_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Title\n\n[[Link]]\n", encoding="utf-8")
    document = parse_note(path)
    assert document.markdown.headings[0].text == "Title"


def test_parse_note_raises_oaw_error_on_bad_decode(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(OawError):
        parse_note(path)


def test_parse_note_raises_oaw_error_when_missing(tmp_path: Path) -> None:
    with pytest.raises(OawError):
        parse_note(tmp_path / "missing.md")


# --------------------------------------------------------------------------- #
# Dataclass sanity
# --------------------------------------------------------------------------- #


def test_protected_region_defaults_to_closed_true() -> None:
    region = ProtectedRegion(kind="fence", span=SourceSpan(0, 1))
    assert region.closed is True


def test_section_dataclass_is_frozen() -> None:
    document = parse_note_source("## H\n\nbody\n")
    section = document.find_section("## H")
    assert isinstance(section, Section)
    with pytest.raises(AttributeError):
        section.span = SourceSpan(0, 0)  # type: ignore[misc]


def test_empty_source_parses_with_no_diagnostics() -> None:
    document = parse_note_source("")
    assert document.diagnostics == ()
    assert document.frontmatter is None
    assert document.obsidian_spans == ()
    assert document.protected_regions == ()
    assert document.newline == "\n"


def test_severity_of_unclosed_fence_diagnostic_is_warning() -> None:
    document = parse_note_source("```\nx\n")
    diag = next(d for d in document.diagnostics if d.code == "markdown.unclosed-fence")
    assert diag.severity is Severity.WARNING


def test_envelope_newline_none_reports_lf() -> None:
    document = parse_note_source("no newline at all")
    assert document.envelope.newline is NewlineStyle.NONE
    assert document.newline == "\n"


# --------------------------------------------------------------------------- #
# parse_note byte preservation (CRLF) and BOM-without-frontmatter offsets
# --------------------------------------------------------------------------- #


def test_parse_note_preserves_crlf_bytes_exactly(tmp_path: Path) -> None:
    raw = b"---\r\nid: x\r\n---\r\n\r\n# Title\r\n\r\nBody line\r\n"
    path = tmp_path / "note.md"
    with path.open("wb") as f:
        f.write(raw)

    document = parse_note(path)

    assert document.source == path.read_bytes().decode("utf-8")
    assert "\r\n" in document.source
    assert document.source == raw.decode("utf-8")


def test_bom_without_frontmatter_heading_text_and_section_lookup() -> None:
    text = "﻿## Notes\n\nbody\n"
    document = parse_note_source(text)

    assert document.frontmatter is None
    heading = document.markdown.headings[0]
    assert heading.text == "Notes"
    assert heading.span.start != 0

    section = document.find_section("## Notes")
    assert section is not None
    assert document.slice(section.content_span).strip() == "body"
