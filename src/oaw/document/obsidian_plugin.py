"""Thin markdown-it-py plugin wiring for the ``oaw.document.obsidian_syntax``
recognizers.

This module exposes the same lexical grammar as ``obsidian_syntax`` (wikilinks,
embeds, markdown links, block ids, comments, math, tags) as markdown-it
inline-rule tokens, prefixed ``obsidian_`` (for example ``obsidian_wikilink``).
It exists purely as a future extraction seam and correctness-parity check: it
is deliberately *not* used by ``oaw.document.model`` (phase 2), which instead
calls the pure recognizers directly over raw source text. Keep additions here
minimal — this is not the model layer's parsing path.

Every recognizer here is wired as an inline rule because markdown-it-py hands
each inline rule the exact per-block text chunk it is scanning as
``state.src`` (with ``state.pos`` a local offset into that chunk) — precisely
the ``(source, region)`` shape the recognizers already expect, with
``region = SourceSpan(0, len(state.src))``. Multi-line constructs (comments,
math blocks) are still recognized correctly as long as they stay within one
block's inline chunk (for example inside one multi-line paragraph); a
construct spanning *across* separate block-level elements is out of scope for
this thin adapter.

Rules are registered ``before`` the built-in ``link`` rule, in the precedence
order from the ``obsidian_syntax`` module contract (comments > math >
wikilinks/embeds > markdown links > tags/block ids), so Obsidian-specific
syntax gets first refusal ahead of CommonMark's own link/image handling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline

from oaw.document.obsidian_syntax import (
    ObsidianSpan,
    ObsidianSpanKind,
    find_block_ids,
    find_comments,
    find_markdown_links,
    find_math,
    find_tags,
    find_wikilinks,
)
from oaw.document.types import SourceSpan

__all__ = ["obsidian_plugin"]

_Recognizer = Callable[[str, SourceSpan], tuple[ObsidianSpan, ...]]

_WIKILINK_TYPES = {
    ObsidianSpanKind.WIKILINK: "obsidian_wikilink",
    ObsidianSpanKind.EMBED: "obsidian_embed",
}
_MATH_TYPES = {
    ObsidianSpanKind.MATH_INLINE: "obsidian_math_inline",
    ObsidianSpanKind.MATH_BLOCK: "obsidian_math_block",
}


def _make_rule(
    recognizer: _Recognizer, type_by_kind: dict[ObsidianSpanKind, str]
) -> Callable[[StateInline, bool], bool]:
    def rule(state: StateInline, silent: bool) -> bool:
        region = SourceSpan(0, len(state.src))
        match = next(
            (span for span in recognizer(state.src, region) if span.span.start == state.pos),
            None,
        )
        if match is None:
            return False
        if not silent:
            token = state.push(type_by_kind[match.kind], "", 0)
            token.content = state.src[match.span.start : match.span.end]
            token.markup = ""
            token.meta["kind"] = match.kind.value
            token.meta["target"] = match.target
            token.meta["alias"] = match.alias
            token.meta["closed"] = match.closed
        state.pos = match.span.end
        return True

    return rule


def _single_kind_rule(
    recognizer: _Recognizer, kind: ObsidianSpanKind, token_type: str
) -> Callable[[StateInline, bool], bool]:
    return _make_rule(recognizer, {kind: token_type})


def obsidian_plugin(md: MarkdownIt, **options: Any) -> None:
    """Register ``obsidian_*`` inline token rules on ``md``.

    Usage: ``MarkdownIt("commonmark").use(obsidian_plugin)``. ``options`` is
    accepted (per the markdown-it-py plugin convention) but currently unused.
    """
    md.inline.ruler.before(
        "link",
        "obsidian_comment",
        _single_kind_rule(find_comments, ObsidianSpanKind.COMMENT, "obsidian_comment"),
    )
    md.inline.ruler.before(
        "link",
        "obsidian_math",
        _make_rule(find_math, _MATH_TYPES),
    )
    md.inline.ruler.before(
        "link",
        "obsidian_wikilink",
        _make_rule(find_wikilinks, _WIKILINK_TYPES),
    )
    md.inline.ruler.before(
        "link",
        "obsidian_markdown_link",
        _single_kind_rule(
            find_markdown_links, ObsidianSpanKind.MARKDOWN_LINK, "obsidian_markdown_link"
        ),
    )
    md.inline.ruler.before(
        "link",
        "obsidian_tag",
        _single_kind_rule(find_tags, ObsidianSpanKind.TAG, "obsidian_tag"),
    )
    md.inline.ruler.before(
        "link",
        "obsidian_block_id",
        _single_kind_rule(find_block_ids, ObsidianSpanKind.BLOCK_ID, "obsidian_block_id"),
    )
