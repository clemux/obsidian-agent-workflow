"""Note envelope scanning: BOM, newline convention, and frontmatter boundaries.

``scan_envelope`` recognizes the outermost structure of one note source
before any YAML or Markdown parsing happens. Frontmatter recognition
deliberately mirrors :func:`oaw.notes.split_note` exactly: the first line
(after an optional leading BOM) opens a frontmatter block only when it
*strips* (both leading and trailing whitespace, like ``str.strip()``) to
``"---"``; the block closes on the next line that strips the same way. A
missing closing delimiter means there is no frontmatter at all and the
whole source is body text. This function never raises on malformed input;
problems are recorded as :class:`~oaw.document.types.Diagnostic` entries.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Diagnostic, NewlineStyle, Severity, SourceSpan

BOM = "﻿"
DELIMITER = "---"


@dataclass(frozen=True)
class NoteEnvelope:
    """Outermost structural scan of one note source."""

    bom: str
    newline: NewlineStyle
    mixed_newlines: bool
    frontmatter_span: SourceSpan | None
    frontmatter_inner_span: SourceSpan | None
    body_span: SourceSpan
    diagnostics: tuple[Diagnostic, ...]


def _detect_newlines(text: str) -> tuple[NewlineStyle, bool]:
    """Classify the dominant newline convention of ``text``.

    Counts ``\\r\\n`` pairs against bare (non-``\\r``-prefixed) ``\\n``
    terminators. CRLF wins only when strictly more common; a tie or an LF
    majority yields LF. No terminators at all yields ``NONE``.
    """
    crlf = text.count("\r\n")
    bare_lf = text.count("\n") - crlf
    if crlf == 0 and bare_lf == 0:
        return NewlineStyle.NONE, False
    mixed = crlf > 0 and bare_lf > 0
    dominant = NewlineStyle.CRLF if crlf > bare_lf else NewlineStyle.LF
    return dominant, mixed


def scan_envelope(source: str) -> NoteEnvelope:
    """Scan ``source`` for its BOM, newline style, and frontmatter block."""
    diagnostics: list[Diagnostic] = []

    bom = ""
    start = 0
    if source.startswith(BOM):
        bom = BOM
        start = len(BOM)
        diagnostics.append(
            Diagnostic(
                code="envelope.bom",
                message="source begins with a UTF-8 byte-order mark",
                severity=Severity.INFO,
                span=SourceSpan(0, start),
            )
        )

    rest = source[start:]
    lines = rest.splitlines(keepends=True)

    frontmatter_span: SourceSpan | None = None
    frontmatter_inner_span: SourceSpan | None = None
    body_start = start

    if lines and lines[0].strip() == DELIMITER:
        closing_idx: int | None = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == DELIMITER:
                closing_idx = idx
                break
        if closing_idx is None:
            diagnostics.append(
                Diagnostic(
                    code="envelope.unclosed-frontmatter",
                    message="frontmatter opening delimiter has no closing `---` line",
                    severity=Severity.WARNING,
                    span=SourceSpan(start, len(source)),
                )
            )
        else:
            first_line_len = len(lines[0])
            inner_len = sum(len(line) for line in lines[1:closing_idx])
            block_len = first_line_len + inner_len + len(lines[closing_idx])
            inner_start = start + first_line_len
            inner_end = inner_start + inner_len
            block_end = start + block_len
            frontmatter_span = SourceSpan(start, block_end)
            frontmatter_inner_span = SourceSpan(inner_start, inner_end)
            body_start = block_end

    body_span = SourceSpan(body_start, len(source))

    newline, mixed = _detect_newlines(source)
    if mixed:
        diagnostics.append(
            Diagnostic(
                code="envelope.mixed-newlines",
                message="source mixes CRLF and bare LF line endings",
                severity=Severity.WARNING,
                span=SourceSpan(0, len(source)),
            )
        )

    return NoteEnvelope(
        bom=bom,
        newline=newline,
        mixed_newlines=mixed,
        frontmatter_span=frontmatter_span,
        frontmatter_inner_span=frontmatter_inner_span,
        body_span=body_span,
        diagnostics=tuple(diagnostics),
    )
