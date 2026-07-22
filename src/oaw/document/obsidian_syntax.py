"""Pure recognizers for Obsidian-specific inline/block syntax.

Every function here scans exactly the ``[region.start, region.end)`` slice of
``source`` and returns half-open :class:`~oaw.document.types.SourceSpan`
absolute offsets into ``source``. No ``markdown_it`` dependency lives in this
module: it is a lexical layer only, unaware of code fences or other block
context. Callers (the model layer) decide which regions to scan and how to
resolve overlaps between recognizer kinds; these functions do not de-overlap
their own or each other's results.

Grammar profile (Obsidian 1.12.x semantics, v1 scope — see the document-layer
module contract for the authoritative rules):

- Wikilinks ``[[target]]``/``[[target|alias]]`` and embeds ``![[target]]``:
  the target may contain anything except ``[``, ``]``, ``|``, or a newline.
  ``[[a[[b]]`` resolves to the innermost complete pair (``[[b]]``): the outer
  attempt aborts the moment a bare ``[`` appears in target position, and
  scanning resumes from that character. A backslash directly before the
  opening ``[[`` (or before ``!`` for an embed) escapes the whole construct.
- Markdown links are the CommonMark **inline** form only (``[text](target)``);
  reference-style links are intentionally treated as plain text in v1. The
  link text may not itself contain an unescaped ``]`` or span a line; the
  target is balanced-paren-aware for one level of nesting only.
- Block IDs are a single literal space followed by ``^`` and
  ``[A-Za-z0-9-]+`` anchored at the end of a physical line (or end of region).
- Comments ``%% ... %%`` toggle; an unmatched final ``%%`` captures through
  the end of the region with ``closed=False``.
- Math: ``$$ ... $$`` may span lines (unclosed captures to region end with
  ``closed=False``); inline ``$...$`` must stay on one line, have non-empty
  content, and must not have whitespace immediately inside either delimiter
  (so ``$5 and $6`` matches neither).
- Tags are ``#`` followed by one or more Unicode letters/digits/``_``/``-``/``/``
  (``\\w`` plus ``-``/``/``) that is not all digits, preceded by start-of-string
  or whitespace -- so ``#café``, ``#日本語``, and ``#abc日本`` all match in full.
  Emoji-only tags are a known gap pending probes against the installed
  Obsidian app (emoji code points are not ``\\w``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from oaw.document.types import SourceSpan


class ObsidianSpanKind(StrEnum):
    WIKILINK = "wikilink"
    EMBED = "embed"
    MARKDOWN_LINK = "markdown-link"
    BLOCK_ID = "block-id"
    COMMENT = "comment"
    MATH_INLINE = "math-inline"
    MATH_BLOCK = "math-block"
    TAG = "tag"


@dataclass(frozen=True)
class ObsidianSpan:
    kind: ObsidianSpanKind
    span: SourceSpan
    target: str | None = None
    alias: str | None = None
    closed: bool = True


_BLOCK_ID_RE = re.compile(r" \^([A-Za-z0-9-]+)(?=\r?\n|$)", re.MULTILINE)
_TAG_TOKEN_RE = re.compile(r"[\w/-]+")
_WHITESPACE = (" ", "\t", "\n", "\r")


def _is_escaped(text: str, index: int) -> bool:
    """Return whether an odd number of literal backslashes precede ``index``."""
    escapes = 0
    j = index - 1
    while j >= 0 and text[j] == "\\":
        escapes += 1
        j -= 1
    return escapes % 2 == 1


def _scan_wikilink_content(text: str, start: int, end: int) -> tuple[int, int | None] | None:
    """Scan wikilink/embed content from ``start``.

    Returns ``(content_end, pipe_pos)`` on a complete ``]]`` close within
    ``[start, end)``, where ``content_end`` is the index of the closing pair's
    first ``]`` and ``pipe_pos`` is the index of the first unescaped ``|``
    (``None`` when absent). Returns ``None`` when no complete pair closes
    within the region — the caller then abandons the wikilink attempt.
    Disallowed target characters (bare ``[``, bare ``]`` not part of ``]]``,
    or a newline) also abort the current attempt; the caller resumes scanning
    from the offending character so an inner ``[[...]]`` can still match.
    """
    pipe_pos: int | None = None
    j = start
    while j < end:
        char = text[j]
        if char == "]" and j + 1 < end and text[j + 1] == "]":
            return j, pipe_pos
        if char in ("[", "]", "\n"):
            return None
        if char == "|" and pipe_pos is None and not _is_escaped(text, j):
            pipe_pos = j
        j += 1
    return None


def find_wikilinks(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find wikilinks ``[[...]]`` and embeds ``![[...]]`` inside ``region``."""
    spans: list[ObsidianSpan] = []
    end = region.end
    i = region.start
    while i < end:
        is_embed = source.startswith("![[", i, end)
        if not is_embed and not source.startswith("[[", i, end):
            i += 1
            continue
        marker_start = i
        pair_start = i + 1 if is_embed else i
        if _is_escaped(source, marker_start):
            i = pair_start + 2
            continue
        content_start = pair_start + 2
        result = _scan_wikilink_content(source, content_start, end)
        if result is None:
            i = marker_start + 1
            continue
        content_end, pipe_pos = result
        close_end = content_end + 2
        if pipe_pos is None:
            target = source[content_start:content_end]
            alias = None
        else:
            target = source[content_start:pipe_pos]
            alias = source[pipe_pos + 1 : content_end]
        kind = ObsidianSpanKind.EMBED if is_embed else ObsidianSpanKind.WIKILINK
        spans.append(
            ObsidianSpan(
                kind=kind,
                span=SourceSpan(marker_start, close_end),
                target=target,
                alias=alias,
            )
        )
        i = close_end
    return tuple(spans)


def _balanced_label_end(text: str, start: int, end: int) -> int | None:
    """Return the index of the closing ``]`` of an inline link label."""
    j = start + 1
    while j < end:
        char = text[j]
        if char == "\\" and j + 1 < end:
            j += 2
            continue
        if char == "\n":
            return None
        if char == "]":
            return j
        j += 1
    return None


def _balanced_target_end(text: str, start: int, end: int) -> int | None:
    """Return the index past a balanced ``(...)`` link destination.

    Allows exactly one level of nested parentheses.
    """
    depth = 1
    j = start + 1
    while j < end:
        char = text[j]
        if char == "\\" and j + 1 < end:
            j += 2
            continue
        if char == "\n":
            return None
        if char == "(":
            depth += 1
            if depth > 2:
                return None
        elif char == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def find_markdown_links(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find CommonMark inline links ``[text](target)`` inside ``region``.

    Reference-style links are not recognized in v1.
    """
    spans: list[ObsidianSpan] = []
    end = region.end
    i = region.start
    while i < end:
        if source[i] != "[" or _is_escaped(source, i):
            i += 1
            continue
        label_end = _balanced_label_end(source, i, end)
        if label_end is None or label_end + 1 >= end or source[label_end + 1] != "(":
            i += 1
            continue
        target_end = _balanced_target_end(source, label_end + 1, end)
        if target_end is None:
            i += 1
            continue
        target = source[label_end + 2 : target_end - 1]
        spans.append(
            ObsidianSpan(
                kind=ObsidianSpanKind.MARKDOWN_LINK,
                span=SourceSpan(i, target_end),
                target=target,
            )
        )
        i = target_end
    return tuple(spans)


def find_block_ids(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find trailing ``^block-id`` markers inside ``region``."""
    spans: list[ObsidianSpan] = []
    for match in _BLOCK_ID_RE.finditer(source, region.start, region.end):
        spans.append(
            ObsidianSpan(
                kind=ObsidianSpanKind.BLOCK_ID,
                span=SourceSpan(match.start(), match.end()),
                target=match.group(1),
            )
        )
    return tuple(spans)


def find_comments(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find ``%% ... %%`` Obsidian comments inside ``region``.

    An unmatched trailing ``%%`` captures through the end of ``region`` with
    ``closed=False``.
    """
    spans: list[ObsidianSpan] = []
    end = region.end
    i = region.start
    while i < end:
        open_pos = source.find("%%", i, end)
        if open_pos < 0:
            break
        close_pos = source.find("%%", open_pos + 2, end)
        if close_pos < 0:
            spans.append(
                ObsidianSpan(
                    kind=ObsidianSpanKind.COMMENT,
                    span=SourceSpan(open_pos, end),
                    closed=False,
                )
            )
            break
        span_end = close_pos + 2
        spans.append(
            ObsidianSpan(kind=ObsidianSpanKind.COMMENT, span=SourceSpan(open_pos, span_end))
        )
        i = span_end
    return tuple(spans)


def _match_inline_math(text: str, start: int, end: int) -> int | None:
    """Return the end offset of a one-line ``$...$`` span starting at ``start``."""
    if start + 1 >= end or text[start + 1] in _WHITESPACE:
        return None
    j = start + 1
    while j < end:
        char = text[j]
        if char == "\n":
            return None
        if char == "$":
            if text[j - 1] in (" ", "\t"):
                return None
            return j + 1
        j += 1
    return None


def find_math(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find ``$$...$$`` math blocks and ``$...$`` inline math inside ``region``."""
    spans: list[ObsidianSpan] = []
    end = region.end
    i = region.start
    while i < end:
        if source.startswith("$$", i, end):
            close_pos = source.find("$$", i + 2, end)
            if close_pos < 0:
                spans.append(
                    ObsidianSpan(
                        kind=ObsidianSpanKind.MATH_BLOCK,
                        span=SourceSpan(i, end),
                        closed=False,
                    )
                )
                break
            span_end = close_pos + 2
            spans.append(
                ObsidianSpan(kind=ObsidianSpanKind.MATH_BLOCK, span=SourceSpan(i, span_end))
            )
            i = span_end
            continue
        if source[i] == "$":
            match_end = _match_inline_math(source, i, end)
            if match_end is not None:
                spans.append(
                    ObsidianSpan(kind=ObsidianSpanKind.MATH_INLINE, span=SourceSpan(i, match_end))
                )
                i = match_end
                continue
        i += 1
    return tuple(spans)


def find_tags(source: str, region: SourceSpan) -> tuple[ObsidianSpan, ...]:
    """Find ``#tag`` tokens inside ``region``."""
    spans: list[ObsidianSpan] = []
    end = region.end
    i = region.start
    while i < end:
        if source[i] != "#":
            i += 1
            continue
        prev_ok = i == 0 or source[i - 1] in _WHITESPACE
        token_match = _TAG_TOKEN_RE.match(source, i + 1, end)
        if token_match is None or not prev_ok or token_match.group(0).isdigit():
            i += 1
            continue
        spans.append(
            ObsidianSpan(
                kind=ObsidianSpanKind.TAG,
                span=SourceSpan(i, token_match.end()),
                target=token_match.group(0),
            )
        )
        i = token_match.end()
    return tuple(spans)
