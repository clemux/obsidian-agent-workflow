"""CommonMark/GFM structural parsing over one note body, on markdown-it-py.

This module wraps a single, module-level ``MarkdownIt("commonmark")`` parser
with the ``table`` and ``strikethrough`` extensions enabled — the profile
this package targets (a CommonMark-plus-GFM-subset reading of Obsidian's own
renderer). The ``commonmark`` preset already treats trailing double-space and
backslash line breaks the same way the CommonMark spec (and Obsidian) do, so
no extra ``breaks``/line-break configuration is applied.

``markdown_it`` tokens carry only *line* positions (``token.map == [start,
past_end]``, 0-based, relative to whatever text was handed to ``.parse()``).
Every span this module publishes is instead an absolute string-offset
:class:`~oaw.document.types.SourceSpan` into the *whole* note source: spans
are computed on a body-local :class:`SourceIndex` and shifted by
``body_span.start``, which stays correct even when the body begins mid-line
(a BOM with no frontmatter). The raw ``Token`` objects returned in
``MarkdownStructure.tokens`` are passed through unchanged (opaque, local line
numbers) — only ``headings``, ``regions``, and ``inline_code_spans`` are
absolute.

Parsing never raises on malformed input. An unterminated fence is recorded as
an open ``BlockRegion`` (``closed=False``) spanning through the end of the
parsed region, with a ``markdown.unclosed-fence`` warning. When an inline
code span's exact source offsets cannot be reconstructed from the token
stream, a conservative span (the whole enclosing inline run) is used instead,
with an INFO-level ``markdown.inexact-inline-span`` diagnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from oaw.document.types import Diagnostic, Severity, SourceIndex, SourceSpan

__all__ = [
    "BlockRegion",
    "Heading",
    "MarkdownStructure",
    "parse_markdown",
]


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    marker: Literal["atx", "setext"]
    span: SourceSpan
    content_span: SourceSpan


@dataclass(frozen=True)
class BlockRegion:
    kind: str
    span: SourceSpan
    closed: bool = True
    info: str | None = None


@dataclass(frozen=True)
class MarkdownStructure:
    tokens: tuple[Token, ...]
    headings: tuple[Heading, ...]
    regions: tuple[BlockRegion, ...]
    inline_code_spans: tuple[SourceSpan, ...]
    diagnostics: tuple[Diagnostic, ...]


_OPEN_KIND_BY_TYPE = {
    "table_open": "table",
    "blockquote_open": "blockquote",
    "bullet_list_open": "list",
    "ordered_list_open": "list",
    "list_item_open": "list-item",
    "paragraph_open": "paragraph",
}


def _build_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table").enable("strikethrough")


_PARSER = _build_parser()


def parse_markdown(source: str, body_span: SourceSpan, index: SourceIndex) -> MarkdownStructure:
    """Parse ``source[body_span.start:body_span.end]`` into a MarkdownStructure.

    All span math runs on a *body-local* :class:`SourceIndex` and is shifted by
    ``body_span.start`` at the end. A whole-source line shift cannot represent
    a body that begins mid-line — exactly what happens for a BOM with no
    frontmatter — so ``index`` (the whole-source index) is accepted for API
    stability but not used for conversion.
    """
    del index  # see docstring: conversion must be body-local
    body_text = source[body_span.start : body_span.end]
    offset = body_span.start
    local_index = SourceIndex.build(body_text)
    tokens = tuple(_PARSER.parse(body_text))

    diagnostics: list[Diagnostic] = []
    regions: list[BlockRegion] = []

    for tok in tokens:
        if tok.type == "fence":
            region, closed = _build_fence_region(tok, body_text, local_index, offset)
            regions.append(region)
            if not closed:
                diagnostics.append(
                    Diagnostic(
                        code="markdown.unclosed-fence",
                        message=(
                            "fenced code block has no closing marker; "
                            "treated as extending to the end of the region"
                        ),
                        severity=Severity.WARNING,
                        span=region.span,
                    )
                )
        elif tok.type == "code_block":
            regions.append(
                BlockRegion(kind="indented-code", span=_abs_span(local_index, offset, tok.map))
            )
        elif tok.type == "html_block":
            span = _abs_span(local_index, offset, tok.map)
            closed = _is_html_block_closed(source[span.start : span.end])
            regions.append(BlockRegion(kind="html-block", span=span, closed=closed))
            if not closed:
                diagnostics.append(
                    Diagnostic(
                        code="markdown.unclosed-html-block",
                        message=(
                            "HTML block has no closing marker; "
                            "treated as extending to the end of the region"
                        ),
                        severity=Severity.WARNING,
                        span=span,
                    )
                )
        elif tok.type in _OPEN_KIND_BY_TYPE:
            regions.append(
                BlockRegion(
                    kind=_OPEN_KIND_BY_TYPE[tok.type],
                    span=_abs_span(local_index, offset, tok.map),
                )
            )

    headings = _collect_headings(tokens, body_text, local_index, offset)
    inline_code_spans, inline_diagnostics = _collect_inline_code_spans(
        tokens, body_text, local_index, offset
    )
    diagnostics.extend(inline_diagnostics)

    return MarkdownStructure(
        tokens=tokens,
        headings=headings,
        regions=tuple(regions),
        inline_code_spans=inline_code_spans,
        diagnostics=tuple(diagnostics),
    )


def _abs_span(local_index: SourceIndex, offset: int, local_map: list[int] | None) -> SourceSpan:
    assert local_map is not None
    first, past_last = local_map
    local = local_index.line_span(first, past_last)
    return SourceSpan(local.start + offset, local.end + offset)


# --- fences -----------------------------------------------------------------


def _build_fence_region(
    tok: Token, body_text: str, local_index: SourceIndex, offset: int
) -> tuple[BlockRegion, bool]:
    span = _abs_span(local_index, offset, tok.map)
    assert tok.map is not None
    closed = _is_fence_closed(body_text, local_index, tok.markup, tok.map[1] - 1)
    info = tok.info if tok.info else None
    return BlockRegion(kind="fence", span=span, closed=closed, info=info), closed


_CONTAINER_PREFIX_RE = re.compile(r"^(?:[ \t]*>)*[ \t]*")


def _is_fence_closed(body_text: str, local_index: SourceIndex, markup: str, last_line: int) -> bool:
    if last_line < 0 or last_line >= local_index.line_count:
        return False
    start = local_index.line_start(last_line)
    end = local_index.line_end(last_line)
    line_text = body_text[start:end].rstrip("\n").rstrip("\r")
    # A fence can close inside a container block (blockquote, list item); the
    # closing marker run only starts after those container prefixes.
    stripped = _CONTAINER_PREFIX_RE.sub("", line_text, count=1).strip()
    if not stripped:
        return False
    marker_char = markup[0]
    if any(char != marker_char for char in stripped):
        return False
    return len(stripped) >= len(markup)


# CommonMark HTML block types that end on a terminator string rather than a
# blank line; a missing terminator makes the block run to end of input.
_HTML_TERMINATORS: tuple[tuple[str, str], ...] = (
    ("<!--", "-->"),
    ("<?", "?>"),
    ("<![CDATA[", "]]>"),
)


def _is_html_block_closed(block_text: str) -> bool:
    stripped = block_text.lstrip()
    for opener, terminator in _HTML_TERMINATORS:
        if stripped.startswith(opener):
            return terminator in stripped[len(opener) :]
    if stripped.startswith("<!") and not stripped.startswith("<!--"):
        return ">" in stripped[2:]
    # Tag-based HTML blocks (CommonMark types 1, 6, 7) end on a blank line or
    # end of input; there is no unclosed state that captures following text.
    return True


# --- headings -----------------------------------------------------------------


def _collect_headings(
    tokens: tuple[Token, ...], body_text: str, local_index: SourceIndex, offset: int
) -> tuple[Heading, ...]:
    headings: list[Heading] = []
    count = len(tokens)
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open":
            continue
        assert tok.map is not None
        inline_tok = tokens[i + 1] if i + 1 < count and tokens[i + 1].type == "inline" else None
        level = int(tok.tag[1:])
        marker: Literal["atx", "setext"] = "atx" if tok.markup.startswith("#") else "setext"
        span = _abs_span(local_index, offset, tok.map)
        if marker == "atx":
            content_span, text = _atx_content(body_text, local_index, offset, tok.map[0])
        else:
            inline_map = inline_tok.map if inline_tok is not None and inline_tok.map else tok.map
            content_span, text = _setext_content(body_text, local_index, offset, inline_map)
        headings.append(
            Heading(level=level, text=text, marker=marker, span=span, content_span=content_span)
        )
    return tuple(headings)


def _atx_content(
    body_text: str, local_index: SourceIndex, offset: int, local_line: int
) -> tuple[SourceSpan, str]:
    line_start = local_index.line_start(local_line)
    line_end = local_index.line_end(local_line)
    raw_line = body_text[line_start:line_end]
    line_text = raw_line.rstrip("\n").rstrip("\r")

    stripped_leading = line_text.lstrip(" ")
    indent_len = len(line_text) - len(stripped_leading)
    h = indent_len
    while h < len(line_text) and line_text[h] == "#":
        h += 1
    rest = line_text[h:]

    start_local, end_local = _atx_bounds(rest)
    content_start = line_start + h + start_local
    content_end = line_start + h + end_local
    span = SourceSpan(content_start + offset, content_end + offset)
    return span, body_text[content_start:content_end]


def _atx_bounds(rest: str) -> tuple[int, int]:
    n = len(rest)
    i = 0
    while i < n and rest[i] in " \t":
        i += 1
    start = i

    j = n
    while j > start and rest[j - 1] in " \t":
        j -= 1
    end = j

    k = end
    while k > start and rest[k - 1] == "#":
        k -= 1
    if k < end and (k == start or rest[k - 1] in " \t"):
        end2 = k
        while end2 > start and rest[end2 - 1] in " \t":
            end2 -= 1
        end = end2

    return start, end


def _setext_content(
    body_text: str, local_index: SourceIndex, offset: int, inline_map: list[int]
) -> tuple[SourceSpan, str]:
    block_span = local_index.line_span(inline_map[0], inline_map[1])
    text_slice = body_text[block_span.start : block_span.end]

    lstripped = text_slice.lstrip(" \t")
    lead_trim = len(text_slice) - len(lstripped)
    rstripped = text_slice.rstrip()
    trail_len = len(text_slice) - len(rstripped)

    content_start = block_span.start + lead_trim
    content_end = block_span.end - trail_len
    span = SourceSpan(content_start + offset, content_end + offset)
    return span, body_text[content_start:content_end]


# --- inline code spans --------------------------------------------------------


def _code_inline_pattern(markup: str) -> re.Pattern[str]:
    escaped = re.escape(markup)
    return re.compile(escaped + r"(?!`)(.*?)(?<!`)" + escaped + r"(?!`)", re.DOTALL)


def _locate_code_inline(region_text: str, cursor: int, child: Token) -> tuple[int, int] | None:
    pattern = _code_inline_pattern(child.markup)
    match = pattern.search(region_text, cursor)
    if match is None:
        return None
    raw = match.group(1)
    # ``region_text`` is sliced from the original, un-normalized source, so a
    # CRLF (or lone-CR) source still carries "\r" here. markdown-it normalizes
    # every line ending to "\n" before tokenizing, so ``child.content`` never
    # contains "\r" -- normalize the same way before comparing, or a CRLF
    # multiline code span never matches exactly and falls back to the
    # conservative whole-inline-run span.
    candidate = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if candidate.startswith(" ") and candidate.endswith(" ") and candidate.strip() != "":
        candidate = candidate[1:-1]
    if candidate != child.content:
        return None
    return match.start(), match.end()


def _collect_inline_code_spans(
    tokens: tuple[Token, ...], body_text: str, local_index: SourceIndex, offset: int
) -> tuple[tuple[SourceSpan, ...], tuple[Diagnostic, ...]]:
    spans: list[SourceSpan] = []
    diagnostics: list[Diagnostic] = []

    for tok in tokens:
        if tok.type != "inline" or not tok.children or tok.map is None:
            continue
        if not any(child.type == "code_inline" for child in tok.children):
            continue

        region_span = _abs_span(local_index, offset, tok.map)
        region_text = body_text[region_span.start - offset : region_span.end - offset]
        cursor = 0
        for child in tok.children:
            if child.type != "code_inline":
                continue
            found = _locate_code_inline(region_text, cursor, child)
            if found is None:
                spans.append(region_span)
                diagnostics.append(
                    Diagnostic(
                        code="markdown.inexact-inline-span",
                        message=(
                            "could not derive an exact offset for an inline code span; "
                            "using the enclosing inline run instead"
                        ),
                        severity=Severity.INFO,
                        span=region_span,
                    )
                )
                continue
            local_start, local_end = found
            spans.append(SourceSpan(region_span.start + local_start, region_span.start + local_end))
            cursor = local_end

    return tuple(spans), tuple(diagnostics)
