"""Tests for oaw.document.envelope.scan_envelope."""

from __future__ import annotations

from oaw.document.envelope import BOM, scan_envelope
from oaw.document.types import NewlineStyle, Severity


def _codes(envelope) -> list[str]:
    return [d.code for d in envelope.diagnostics]


def test_empty_source_has_no_frontmatter_and_no_diagnostics() -> None:
    envelope = scan_envelope("")
    assert envelope.bom == ""
    assert envelope.newline is NewlineStyle.NONE
    assert envelope.mixed_newlines is False
    assert envelope.frontmatter_span is None
    assert envelope.frontmatter_inner_span is None
    assert envelope.body_span.start == 0
    assert envelope.body_span.end == 0
    assert envelope.diagnostics == ()


def test_lf_only_source_detected() -> None:
    envelope = scan_envelope("a\nb\nc\n")
    assert envelope.newline is NewlineStyle.LF
    assert envelope.mixed_newlines is False


def test_crlf_only_source_detected() -> None:
    envelope = scan_envelope("a\r\nb\r\nc\r\n")
    assert envelope.newline is NewlineStyle.CRLF
    assert envelope.mixed_newlines is False


def test_mixed_newlines_flagged_with_warning() -> None:
    source = "a\r\nb\nc\r\n"
    envelope = scan_envelope(source)
    assert envelope.mixed_newlines is True
    diag = next(d for d in envelope.diagnostics if d.code == "envelope.mixed-newlines")
    assert diag.severity is Severity.WARNING
    assert diag.span is not None
    assert diag.span.start == 0
    assert diag.span.end == len(source)


def test_newline_tie_resolves_to_lf() -> None:
    # One CRLF terminator and one bare LF terminator: not strictly CRLF-majority.
    envelope = scan_envelope("a\r\nb\n")
    assert envelope.newline is NewlineStyle.LF
    assert envelope.mixed_newlines is True


def test_no_terminators_gives_newline_none() -> None:
    envelope = scan_envelope("just one line, no newline at all")
    assert envelope.newline is NewlineStyle.NONE
    assert envelope.mixed_newlines is False


def test_simple_frontmatter_recognized() -> None:
    source = "---\nkey: value\n---\nbody text\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is not None
    assert envelope.frontmatter_inner_span is not None
    fm = envelope.frontmatter_span
    inner = envelope.frontmatter_inner_span
    assert source[fm.start : fm.end] == "---\nkey: value\n---\n"
    assert source[inner.start : inner.end] == "key: value\n"
    assert source[envelope.body_span.start : envelope.body_span.end] == "body text\n"
    assert not any(d.code == "envelope.unclosed-frontmatter" for d in envelope.diagnostics)


def test_frontmatter_only_file_has_empty_body() -> None:
    source = "---\nkey: value\n---\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is not None
    assert envelope.frontmatter_span.end == len(source)
    assert envelope.body_span.start == len(source)
    assert envelope.body_span.end == len(source)


def test_dashes_later_in_body_are_not_frontmatter() -> None:
    source = "intro text\n---\nnot frontmatter\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is None
    assert envelope.frontmatter_inner_span is None
    assert envelope.body_span.start == 0
    assert envelope.body_span.end == len(source)


def test_unclosed_frontmatter_falls_back_to_whole_body() -> None:
    source = "---\nkey: value\nno closing delimiter\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is None
    assert envelope.frontmatter_inner_span is None
    assert envelope.body_span.start == 0
    assert envelope.body_span.end == len(source)
    diag = next(d for d in envelope.diagnostics if d.code == "envelope.unclosed-frontmatter")
    assert diag.severity is Severity.WARNING
    assert diag.span is not None
    assert diag.span.start == 0
    assert diag.span.end == len(source)


def test_opening_delimiter_with_trailing_spaces_recognized() -> None:
    source = "---   \nkey: value\n---\nbody\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is not None
    assert envelope.frontmatter_inner_span is not None
    inner = envelope.frontmatter_inner_span
    assert source[inner.start : inner.end] == "key: value\n"


def test_closing_delimiter_with_trailing_spaces_recognized() -> None:
    source = "---\nkey: value\n---   \nbody\n"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is not None
    fm = envelope.frontmatter_span
    assert source[fm.start : fm.end] == "---\nkey: value\n---   \n"
    assert source[envelope.body_span.start : envelope.body_span.end] == "body\n"


def test_bom_is_captured_and_excluded_from_spans() -> None:
    source = BOM + "---\nkey: value\n---\nbody\n"
    envelope = scan_envelope(source)
    assert envelope.bom == BOM
    diag = next(d for d in envelope.diagnostics if d.code == "envelope.bom")
    assert diag.severity is Severity.INFO
    assert diag.span is not None
    assert diag.span.start == 0
    assert diag.span.end == len(BOM)
    assert envelope.frontmatter_span is not None
    fm = envelope.frontmatter_span
    # frontmatter span starts right after the BOM, not overlapping it.
    assert fm.start == len(BOM)
    assert source[fm.start : fm.end] == "---\nkey: value\n---\n"
    assert source[envelope.body_span.start : envelope.body_span.end] == "body\n"


def test_bom_without_frontmatter_still_excluded_from_body() -> None:
    source = BOM + "plain body text\n"
    envelope = scan_envelope(source)
    assert envelope.bom == BOM
    assert envelope.frontmatter_span is None
    assert envelope.body_span.start == len(BOM)
    assert source[envelope.body_span.start : envelope.body_span.end] == "plain body text\n"


def test_bom_with_unclosed_frontmatter() -> None:
    source = BOM + "---\nkey: value\nno close\n"
    envelope = scan_envelope(source)
    assert envelope.bom == BOM
    assert envelope.frontmatter_span is None
    assert envelope.body_span.start == len(BOM)
    assert "envelope.unclosed-frontmatter" in _codes(envelope)
    assert "envelope.bom" in _codes(envelope)


def test_crlf_frontmatter_spans_correct() -> None:
    source = "---\r\nkey: value\r\n---\r\nbody\r\n"
    envelope = scan_envelope(source)
    assert envelope.newline is NewlineStyle.CRLF
    assert envelope.frontmatter_span is not None
    fm = envelope.frontmatter_span
    inner = envelope.frontmatter_inner_span
    assert inner is not None
    assert source[fm.start : fm.end] == "---\r\nkey: value\r\n---\r\n"
    assert source[inner.start : inner.end] == "key: value\r\n"
    assert source[envelope.body_span.start : envelope.body_span.end] == "body\r\n"


def test_no_trailing_newline_at_eof_unclosed_frontmatter() -> None:
    source = "---\nkey: value"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is None
    assert "envelope.unclosed-frontmatter" in _codes(envelope)
    assert envelope.body_span.start == 0
    assert envelope.body_span.end == len(source)


def test_no_trailing_newline_at_eof_closes_on_last_line() -> None:
    source = "---\nkey: value\n---"
    envelope = scan_envelope(source)
    assert envelope.frontmatter_span is not None
    assert envelope.frontmatter_span.end == len(source)
    assert envelope.body_span.start == len(source)
    assert envelope.body_span.end == len(source)


def test_diagnostics_are_in_expected_order_bom_then_unclosed_then_mixed() -> None:
    source = BOM + "---\r\nkey: value\nno close\r\n"
    envelope = scan_envelope(source)
    codes = _codes(envelope)
    assert codes == [
        "envelope.bom",
        "envelope.unclosed-frontmatter",
        "envelope.mixed-newlines",
    ]
