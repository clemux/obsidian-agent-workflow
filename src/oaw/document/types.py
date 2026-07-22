"""Shared immutable value types for the source-preserving note document layer.

Every offset in this package is a Python string index into the exact note
source (``str``), never a byte offset and never a line number. Line-based
information from markdown-it-py tokens is converted through
:class:`SourceIndex`. Spans are half-open ``[start, end)`` ranges.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from enum import StrEnum


class NewlineStyle(StrEnum):
    """Dominant line-ending convention of one note source."""

    LF = "lf"
    CRLF = "crlf"
    NONE = "none"  # single line, no terminator observed


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class SourceSpan:
    """Half-open ``[start, end)`` range of string offsets into the source."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span: [{self.start}, {self.end})")

    def contains(self, other: SourceSpan) -> bool:
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: SourceSpan) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Diagnostic:
    """One parse- or safety-relevant observation about a note source.

    ``code`` is a stable dotted identifier (for example
    ``"frontmatter.duplicate-key"``); tests and ``oaw doctor`` match on it,
    so changing a code is a behavior change.
    """

    code: str
    message: str
    severity: Severity
    span: SourceSpan | None = None


@dataclass(frozen=True)
class SourceIndex:
    """Line/offset conversion for one immutable source string."""

    source: str
    line_starts: tuple[int, ...] = field(default=(), compare=False)

    @staticmethod
    def build(source: str) -> SourceIndex:
        starts = [0]
        for idx, char in enumerate(source):
            if char == "\n":
                starts.append(idx + 1)
        return SourceIndex(source=source, line_starts=tuple(starts))

    @property
    def line_count(self) -> int:
        return len(self.line_starts)

    def line_start(self, line: int) -> int:
        """Offset of the first character of 0-based ``line``."""
        return self.line_starts[line]

    def line_end(self, line: int) -> int:
        """Offset just past 0-based ``line``, including its newline."""
        if line + 1 < len(self.line_starts):
            return self.line_starts[line + 1]
        return len(self.source)

    def line_span(self, first_line: int, past_last_line: int) -> SourceSpan:
        """Span covering 0-based lines ``[first_line, past_last_line)``.

        This matches the markdown-it-py ``token.map`` convention directly:
        ``line_span(*token.map)``.
        """
        if past_last_line <= first_line:
            return SourceSpan(self.line_start(first_line), self.line_start(first_line))
        return SourceSpan(self.line_start(first_line), self.line_end(past_last_line - 1))

    def offset_to_line(self, offset: int) -> int:
        """0-based line containing ``offset``."""
        return bisect.bisect_right(self.line_starts, offset) - 1
