"""Whole-note assembly: envelope + frontmatter + markdown + Obsidian syntax.

``parse_note_source`` / ``parse_note`` are the document layer's single public
entry point: they run the four foundation layers in a fixed order and fold
their results into one immutable :class:`NoteDocument`, never raising on
malformed input (only :func:`parse_note`'s file read can raise, and only for
an undecodable file).

Assembly order (per the module contract):

1. :func:`~oaw.document.envelope.scan_envelope` recognizes the BOM, newline
   convention, and frontmatter boundaries.
2. :func:`~oaw.document.frontmatter_yaml.compose_frontmatter` composes the
   YAML frontmatter block, when one was recognized.
3. :func:`~oaw.document.markdown.parse_markdown` parses the body into
   headings, block regions, and inline code spans.
4. The Obsidian recognizers (:mod:`oaw.document.obsidian_syntax`) scan the
   body a second time, restricted to the text left over once fenced code,
   indented code, and HTML blocks are cut out. Within what remains, comments
   and math are recognized first; wikilinks/embeds, markdown links, and
   tags/block ids are then recognized only outside comment and math spans,
   matching the precedence documented in ``obsidian_syntax``.

:class:`ProtectedRegion` unions every span an editing layer must never splice
into: fenced/indented code, inline code, HTML blocks/comments, Obsidian
comments, math, and (as a whole-document fallback) an unclosed frontmatter
attempt. Diagnostics for unclosed Obsidian comments/math are synthesized here
(the pure recognizers only report ``closed=False`` on the span itself) using
the same ``<module>.unclosed-<construct>`` naming as ``markdown.unclosed-fence``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from oaw.document.envelope import NoteEnvelope, scan_envelope
from oaw.document.frontmatter_yaml import FrontmatterModel, compose_frontmatter
from oaw.document.markdown import Heading, MarkdownStructure, parse_markdown
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
from oaw.document.types import Diagnostic, NewlineStyle, Severity, SourceIndex, SourceSpan
from oaw.errors import OawError

__all__ = [
    "NoteDocument",
    "ProtectedRegion",
    "Section",
    "parse_note",
    "parse_note_source",
]

# Block-level regions cut out of the text before the Obsidian recognizers run.
_CODE_EXCLUDED_KINDS = {"fence", "indented-code", "html-block"}


@dataclass(frozen=True)
class ProtectedRegion:
    """One splice-off-limits span of a note's source.

    ``closed`` mirrors the underlying construct's own open/close state (a
    fence, an Obsidian comment, or math that never closed is still protected,
    but is flagged ``closed=False`` so a diagnostic-aware caller can tell an
    ordinary code block from a malformed, EOF-swallowing one). This field is
    not in the module contract's literal ``ProtectedRegion`` sketch, but the
    fixtures runner's own assertions (``region.closed``) require it, so it is
    added here as the least-surprising extension: it reuses each source
    layer's own closed/open concept (``BlockRegion.closed``,
    ``ObsidianSpan.closed``) rather than inventing a parallel ``unclosed-<x>``
    kind for every construct. The one case with no natural "closed" concept
    of its own -- an unclosed frontmatter attempt -- uses the literal
    ``unclosed-frontmatter`` kind from the contract's enum instead.
    """

    kind: str
    span: SourceSpan
    closed: bool = True


@dataclass(frozen=True)
class Section:
    """One heading and the span of the section it introduces."""

    heading: Heading
    span: SourceSpan
    content_span: SourceSpan


@dataclass(frozen=True)
class NoteDocument:
    """The fully assembled, source-preserving view of one note."""

    source: str
    index: SourceIndex
    envelope: NoteEnvelope
    frontmatter: FrontmatterModel | None
    markdown: MarkdownStructure
    obsidian_spans: tuple[ObsidianSpan, ...]
    protected_regions: tuple[ProtectedRegion, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def newline(self) -> str:
        if self.envelope.newline is NewlineStyle.CRLF:
            return "\r\n"
        return "\n"

    def slice(self, span: SourceSpan) -> str:
        return self.source[span.start : span.end]

    def is_protected(self, span: SourceSpan) -> bool:
        return any(region.span.overlaps(span) for region in self.protected_regions)

    def diagnostics_in(self, span: SourceSpan) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.span is not None and d.span.overlaps(span))

    def find_section(self, heading: str) -> Section | None:
        """Find a fence/protected-region-aware, heading-exact section.

        Mirrors ``oaw.notes.locate_section``: only ATX headings participate,
        a bare (non-``#``-prefixed) argument is normalized to a level-2
        heading, matching is an exact comparison of the heading's whole
        source line (after stripping trailing whitespace) against the
        normalized target, and both the target heading and any candidate
        section-ending heading are skipped when they fall inside a protected
        region (the fence-aware behavior of the legacy helper generalized to
        every protected-region kind).
        """
        target = _normalize_heading(heading)
        target_level = _heading_level(target)
        if target_level is None:  # pragma: no cover - _normalize_heading already validated
            raise OawError("section heading must look like a Markdown heading")

        candidates = [
            h for h in self.markdown.headings if h.marker == "atx" and not self.is_protected(h.span)
        ]

        target_heading: Heading | None = None
        for h in candidates:
            if self.slice(h.span).rstrip() == target:
                target_heading = h
                break
        if target_heading is None:
            return None

        section_end = self.envelope.body_span.end
        for h in candidates:
            if h.span.start <= target_heading.span.start:
                continue
            if h.level <= target_level:
                section_end = h.span.start
                break

        return Section(
            heading=target_heading,
            span=SourceSpan(target_heading.span.start, section_end),
            content_span=SourceSpan(target_heading.span.end, section_end),
        )


def parse_note_source(source: str) -> NoteDocument:
    """Parse one whole note source into a :class:`NoteDocument`.

    Never raises on malformed input: parse problems are recorded as
    :class:`Diagnostic` entries and the affected region is protected.
    """
    envelope = scan_envelope(source)
    index = SourceIndex.build(source)

    frontmatter: FrontmatterModel | None = None
    if envelope.frontmatter_inner_span is not None:
        frontmatter = compose_frontmatter(source, envelope.frontmatter_inner_span)

    markdown = parse_markdown(source, envelope.body_span, index)
    obsidian_spans = _scan_obsidian_spans(source, envelope.body_span, markdown)
    protected_regions = _build_protected_regions(source, envelope, markdown, obsidian_spans)

    diagnostics = (
        *envelope.diagnostics,
        *(frontmatter.diagnostics if frontmatter is not None else ()),
        *markdown.diagnostics,
        *_unclosed_obsidian_diagnostics(obsidian_spans),
    )
    diagnostics = tuple(sorted(diagnostics, key=_diagnostic_sort_key))

    return NoteDocument(
        source=source,
        index=index,
        envelope=envelope,
        frontmatter=frontmatter,
        markdown=markdown,
        obsidian_spans=obsidian_spans,
        protected_regions=protected_regions,
        diagnostics=diagnostics,
    )


def parse_note(path: Path) -> NoteDocument:
    """Read ``path`` as UTF-8 and parse it. Raises ``OawError`` on bad decode.

    The file is read as bytes and decoded explicitly: ``Path.read_text``'s
    universal-newline translation would silently rewrite every CRLF to LF in
    ``NoteDocument.source``, defeating the whole layer's byte-preservation
    guarantees for CRLF notes on disk.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OawError(f"could not read note as UTF-8: {path}") from exc
    return parse_note_source(text)


def _diagnostic_sort_key(d: Diagnostic) -> tuple[int, int, str]:
    if d.span is None:
        return (-1, -1, d.code)
    return (d.span.start, d.span.end, d.code)


# --- Obsidian span scanning ---------------------------------------------------


def _scan_obsidian_spans(
    source: str, body_span: SourceSpan, markdown: MarkdownStructure
) -> tuple[ObsidianSpan, ...]:
    code_excluded = [r.span for r in markdown.regions if r.kind in _CODE_EXCLUDED_KINDS]
    scan_regions = _complement(code_excluded, body_span)

    comments: list[ObsidianSpan] = []
    maths: list[ObsidianSpan] = []
    for region in scan_regions:
        comments.extend(find_comments(source, region))
        maths.extend(find_math(source, region))

    comment_math_spans = [c.span for c in comments] + [m.span for m in maths]

    wikilinks: list[ObsidianSpan] = []
    links: list[ObsidianSpan] = []
    block_ids: list[ObsidianSpan] = []
    tags: list[ObsidianSpan] = []
    for region in scan_regions:
        overlapping = [s for s in comment_math_spans if s.overlaps(region)]
        for sub in _complement(overlapping, region):
            wikilinks.extend(find_wikilinks(source, sub))
            links.extend(find_markdown_links(source, sub))
            block_ids.extend(find_block_ids(source, sub))
            tags.extend(find_tags(source, sub))

    all_spans = [*comments, *maths, *wikilinks, *links, *block_ids, *tags]
    all_spans.sort(key=lambda s: (s.span.start, s.span.end))
    return tuple(all_spans)


def _unclosed_obsidian_diagnostics(
    obsidian_spans: tuple[ObsidianSpan, ...],
) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    for span in obsidian_spans:
        if span.closed:
            continue
        if span.kind is ObsidianSpanKind.COMMENT:
            diagnostics.append(
                Diagnostic(
                    code="obsidian.unclosed-comment",
                    message=(
                        "Obsidian comment has no closing `%%`; "
                        "treated as extending to the end of the region"
                    ),
                    severity=Severity.WARNING,
                    span=span.span,
                )
            )
        elif span.kind is ObsidianSpanKind.MATH_BLOCK:
            diagnostics.append(
                Diagnostic(
                    code="obsidian.unclosed-math",
                    message=(
                        "Math block has no closing `$$`; "
                        "treated as extending to the end of the region"
                    ),
                    severity=Severity.WARNING,
                    span=span.span,
                )
            )
    return tuple(diagnostics)


# --- protected regions ---------------------------------------------------------


def _build_protected_regions(
    source: str,
    envelope: NoteEnvelope,
    markdown: MarkdownStructure,
    obsidian_spans: tuple[ObsidianSpan, ...],
) -> tuple[ProtectedRegion, ...]:
    regions: list[ProtectedRegion] = []

    for r in markdown.regions:
        if r.kind == "fence":
            regions.append(ProtectedRegion(kind="fence", span=r.span, closed=r.closed))
        elif r.kind == "indented-code":
            regions.append(ProtectedRegion(kind="indented-code", span=r.span))
        elif r.kind == "html-block":
            kind = "html-comment" if _looks_like_html_comment(source, r.span) else "html-block"
            regions.append(ProtectedRegion(kind=kind, span=r.span, closed=r.closed))

    for span in markdown.inline_code_spans:
        regions.append(ProtectedRegion(kind="inline-code", span=span))

    for obs in obsidian_spans:
        if obs.kind is ObsidianSpanKind.COMMENT:
            regions.append(
                ProtectedRegion(kind="obsidian-comment", span=obs.span, closed=obs.closed)
            )
        elif obs.kind in (ObsidianSpanKind.MATH_INLINE, ObsidianSpanKind.MATH_BLOCK):
            regions.append(ProtectedRegion(kind="math", span=obs.span, closed=obs.closed))

    if envelope.frontmatter_span is None:
        unclosed = next(
            (d for d in envelope.diagnostics if d.code == "envelope.unclosed-frontmatter"),
            None,
        )
        if unclosed is not None and unclosed.span is not None:
            regions.append(
                ProtectedRegion(kind="unclosed-frontmatter", span=unclosed.span, closed=False)
            )

    regions.sort(key=lambda r: (r.span.start, r.span.end))
    return tuple(regions)


def _looks_like_html_comment(source: str, span: SourceSpan) -> bool:
    return source[span.start : span.end].lstrip().startswith("<!--")


# --- span-set arithmetic --------------------------------------------------------


def _merge_spans(spans: Sequence[SourceSpan]) -> list[SourceSpan]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged = [ordered[0]]
    for s in ordered[1:]:
        last = merged[-1]
        if s.start <= last.end:
            if s.end > last.end:
                merged[-1] = SourceSpan(last.start, s.end)
        else:
            merged.append(s)
    return merged


def _complement(excluded: Sequence[SourceSpan], within: SourceSpan) -> list[SourceSpan]:
    """Gaps of ``within`` left over once every span in ``excluded`` is cut out."""
    relevant = [s for s in excluded if s.overlaps(within)]
    merged = _merge_spans(relevant)
    clipped = [SourceSpan(max(s.start, within.start), min(s.end, within.end)) for s in merged]
    clipped = [s for s in clipped if s.start < s.end]

    gaps: list[SourceSpan] = []
    cursor = within.start
    for s in clipped:
        if cursor < s.start:
            gaps.append(SourceSpan(cursor, s.start))
        cursor = max(cursor, s.end)
    if cursor < within.end:
        gaps.append(SourceSpan(cursor, within.end))
    return gaps


# --- heading normalization (mirrors oaw.notes, kept local to stay import-pure) --

_HEADING_LEVEL_RE = re.compile(r"^(#{1,6})\s+\S")


def _heading_level(line: str) -> int | None:
    match = _HEADING_LEVEL_RE.match(line)
    return len(match.group(1)) if match else None


def _normalize_heading(section: str) -> str:
    value = section.strip()
    if not value:
        raise OawError("section heading must not be empty")
    if value.startswith("#"):
        if _heading_level(value) is None:
            raise OawError("section heading must look like a Markdown heading")
        return value
    return f"## {value}"
