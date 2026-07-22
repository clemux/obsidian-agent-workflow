"""Source-preserving edits over a parsed :class:`~oaw.document.model.NoteDocument`.

Every mutation in this module is a plain string splice: a :class:`SourceEdit`
names a half-open :class:`~oaw.document.types.SourceSpan` of the *original*
source to replace and the literal text to put there instead. There is no YAML
dumper and no Markdown renderer anywhere in this module -- bytes outside an
edit's own span are never touched, and the high-level operations below each
compute exactly one (or, for :func:`remove_frontmatter_list_item` with
duplicate values, a small handful of) such edits before handing them to
:func:`apply_edits`.

:func:`apply_edits` never silently repairs malformed input. It refuses (raises
:class:`~oaw.errors.OawError`) whenever an edit is ambiguous or unsafe: empty
edit lists, out-of-range spans, overlapping edits, edits that would split a
``\\r\\n`` pair, and edits that touch a protected region or an existing
``ERROR``-severity diagnostic (unless the caller opts in with
``allow_protected=True``). Every other refusal in this module -- missing
frontmatter, a duplicated or wrongly shaped frontmatter field, a relationship
value that is not present -- mirrors the message style of the legacy
``oaw.frontmatter`` helpers it replaces.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import yaml

from oaw.document.frontmatter_yaml import FieldKind
from oaw.document.model import NoteDocument, ProtectedRegion, Section, parse_note_source
from oaw.document.types import Severity, SourceSpan
from oaw.errors import OawError

_YAML_STR_TAG = "tag:yaml.org,2002:str"

__all__ = [
    "EditResult",
    "SourceEdit",
    "append_block_to_section",
    "append_frontmatter_list_item",
    "apply_edits",
    "normalize_block_newlines",
    "remove_frontmatter_list_item",
    "set_frontmatter_scalar",
]


@dataclass(frozen=True)
class SourceEdit:
    """One replacement of ``span`` (a range of the *original* source) with ``text``.

    An empty ``text`` is a deletion; ``span.start == span.end`` is a pure
    insertion at that offset.
    """

    span: SourceSpan
    text: str


@dataclass(frozen=True)
class EditResult:
    """The outcome of applying one or more :class:`SourceEdit` values."""

    source: str
    document: NoteDocument
    edits: tuple[SourceEdit, ...]


def apply_edits(
    document: NoteDocument,
    edits: Sequence[SourceEdit],
    *,
    verify: Callable[[NoteDocument], None] | None = None,
    allow_protected: bool = False,
) -> EditResult:
    """Splice ``edits`` into ``document.source`` and reparse the result.

    Refuses (raises :class:`OawError`) rather than guessing when the request
    is ambiguous or unsafe: an empty edit list, a span outside
    ``[0, len(source))``, overlapping edits, an edit that would split a
    ``\\r\\n`` pair, or an edit touching a protected region or an existing
    ``ERROR`` diagnostic (unless ``allow_protected=True``). Edits are applied
    end-backward (descending by start) directly on the string; an
    independently computed splice is compared against the result as a
    belt-and-braces byte-preservation check. When ``verify`` is given it runs
    on the reparsed document and may itself raise ``OawError`` to refuse.
    """
    ordered = sorted(edits, key=lambda e: (e.span.start, e.span.end))
    if not ordered:
        raise OawError("apply_edits requires at least one edit")

    source = document.source
    length = len(source)

    for edit in ordered:
        if edit.span.start < 0 or edit.span.end > length:
            raise OawError(
                f"edit span [{edit.span.start}, {edit.span.end}) is outside the source "
                f"(length {length})"
            )
        if _splits_crlf_pair(source, edit.span.start) or _splits_crlf_pair(source, edit.span.end):
            raise OawError(
                f"edit span [{edit.span.start}, {edit.span.end}) splits a CRLF line ending"
            )

    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.span.start < previous.span.end or current.span.start == previous.span.start:
            raise OawError(
                f"edits overlap between [{previous.span.start}, {previous.span.end}) "
                f"and [{current.span.start}, {current.span.end})"
            )

    if not allow_protected:
        for edit in ordered:
            _refuse_if_protected(document, edit.span)

    new_source = source
    for edit in sorted(ordered, key=lambda e: e.span.start, reverse=True):
        new_source = new_source[: edit.span.start] + edit.text + new_source[edit.span.end :]

    _assert_byte_preservation(source, ordered, new_source)

    new_document = parse_note_source(new_source)
    if verify is not None:
        verify(new_document)

    return EditResult(source=new_source, document=new_document, edits=tuple(ordered))


def normalize_block_newlines(text: str, newline: str) -> str:
    """Rewrite every line ending in ``text`` to ``newline`` (``"\\n"`` or ``"\\r\\n"``)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline == "\n":
        return normalized
    return normalized.replace("\n", newline)


# --- internal: apply_edits refusal checks --------------------------------------


def _splits_crlf_pair(source: str, pos: int) -> bool:
    return 0 < pos < len(source) and source[pos - 1] == "\r" and source[pos] == "\n"


def _refuse_if_protected(document: NoteDocument, span: SourceSpan) -> None:
    for region in document.protected_regions:
        if region.span.overlaps(span):
            raise OawError(
                f"edit at [{span.start}, {span.end}) overlaps a protected {region.kind} "
                f"region at [{region.span.start}, {region.span.end})"
            )
    for diagnostic in document.diagnostics:
        if (
            diagnostic.severity is Severity.ERROR
            and diagnostic.span is not None
            and diagnostic.span.overlaps(span)
        ):
            raise OawError(
                f"edit at [{span.start}, {span.end}) overlaps an error diagnostic "
                f"({diagnostic.code}) at [{diagnostic.span.start}, {diagnostic.span.end})"
            )


def _assert_byte_preservation(
    original: str, ordered_edits: list[SourceEdit], new_source: str
) -> None:
    """Belt-and-braces check: recompute the splice independently and compare."""
    pieces: list[str] = []
    cursor = 0
    for edit in ordered_edits:
        pieces.append(original[cursor : edit.span.start])
        pieces.append(edit.text)
        cursor = edit.span.end
    pieces.append(original[cursor:])
    expected = "".join(pieces)
    if new_source != expected:  # pragma: no cover - defensive, should be unreachable
        raise OawError("internal error: edit application result does not match expected splice")


# --- append_block_to_section ----------------------------------------------------

_HEADING_LEVEL_RE = re.compile(r"^(#{1,6})\s+\S")


def _normalize_heading_text(section: str) -> str:
    """Mirror ``oaw.notes.normalize_heading``: bare text becomes a level-2 heading."""
    value = section.strip()
    if not value:
        raise OawError("section heading must not be empty")
    if value.startswith("#"):
        if not _HEADING_LEVEL_RE.match(value):
            raise OawError("section heading must look like a Markdown heading")
        return value
    return f"## {value}"


def _unclosed_region_count(document: NoteDocument) -> int:
    """Count protected regions that never closed (fence/comment/math/frontmatter)."""
    return sum(1 for region in document.protected_regions if not region.closed)


def _verify_append_block_to_section(
    heading: str, original_unclosed_count: int
) -> Callable[[NoteDocument], None]:
    def verify(new_document: NoteDocument) -> None:
        if new_document.find_section(heading) is None:
            raise OawError(f"cannot append: section {heading!r} not found after the edit")
        if _unclosed_region_count(new_document) > original_unclosed_count:
            raise OawError("cannot append: the block would introduce an unclosed construct")

    return verify


def _touches_unclosed_region(document: NoteDocument, point: int) -> ProtectedRegion | None:
    """Return the unclosed region touching ``point``, or ``None``.

    An unclosed fence/comment/math/frontmatter construct protects through EOF,
    so its own end coincides with the document's end: a plain
    :meth:`~oaw.document.types.SourceSpan.overlaps` check on a zero-width
    insertion span never flags that boundary. Placement there is inherently
    ambiguous (there is no well-defined "after" the construct), so it is
    refused explicitly here rather than relying on :func:`apply_edits`. The
    caller uses the returned region's ``kind``/``span`` to name the offending
    construct in its refusal message.
    """
    for region in document.protected_regions:
        if not region.closed and region.span.start <= point <= region.span.end:
            return region
    return None


def _unclosed_region_message(prefix: str, region: ProtectedRegion, point: int) -> str:
    """Build a ``cannot append:`` refusal naming ``region``'s kind and span.

    Keeps the literal substring ``"unclosed protected region"`` (an existing
    test matches on it) while also naming the specific construct and its
    span, so a caller doesn't have to go spelunking to find out what actually
    blocked the append.
    """
    return (
        f"cannot append: {prefix} inside an unclosed protected region "
        f"({region.kind}) [{region.span.start}, {region.span.end}) at offset {point}"
    )


def _section_trim_start(document: NoteDocument, section: Section) -> int:
    """Offset where a section's trailing blank lines begin (or its content end, if none)."""
    content = document.slice(section.content_span)
    lines = content.splitlines(keepends=True)
    idx = len(lines)
    while idx > 0 and lines[idx - 1].strip("\r\n") == "":
        idx -= 1
    kept_len = sum(len(line) for line in lines[:idx])
    return section.content_span.start + kept_len


def append_block_to_section(document: NoteDocument, heading: str, block: str) -> EditResult:
    """Append ``block`` to the section under ``heading``, creating it if absent.

    Semantics of ``oaw.notes.append_markdown_block_to_section``, reimplemented
    as a single splice: a CRLF document keeps CRLF endings on every inserted
    line (the legacy helper's whole-document ``"\\n".join(...)`` rejoin lost
    this), and bytes outside the touched range are never rewritten. Refuses
    when the target section's end sits inside (or exactly at the edge of) an
    unclosed protected region, since appending there is ambiguous.
    """
    heading_text = _normalize_heading_text(heading)
    block = block.strip()
    if not block:
        raise OawError("block content must not be empty")
    newline = document.newline
    block = normalize_block_newlines(block, newline)

    original_unclosed_count = _unclosed_region_count(document)
    verify = _verify_append_block_to_section(heading, original_unclosed_count)

    section = document.find_section(heading)
    if section is None:
        insertion_point = document.envelope.body_span.end
        offending = _touches_unclosed_region(document, insertion_point)
        if offending is not None:
            raise OawError(
                _unclosed_region_message("the end of the document is", offending, insertion_point)
            )
        prefix = "" if document.source.endswith("\n") else newline
        text = f"{prefix}{newline}{heading_text}{newline}{newline}{block}{newline}"
        edit = SourceEdit(span=SourceSpan(insertion_point, insertion_point), text=text)
        return apply_edits(document, [edit], verify=verify)

    content_end = section.span.end
    offending = _touches_unclosed_region(document, content_end)
    if offending is not None:
        raise OawError(_unclosed_region_message("the target section ends", offending, content_end))
    has_after = content_end < document.envelope.body_span.end
    trim_start = _section_trim_start(document, section)
    text = f"{newline}{block}{newline}{newline}" if has_after else f"{newline}{block}{newline}"
    edit = SourceEdit(span=SourceSpan(trim_start, content_end), text=text)
    return apply_edits(document, [edit], verify=verify)


# --- frontmatter scalar value serialization -------------------------------------


def _scalar_literal(value: str) -> str:
    """Serialize ``value`` as it would appear written raw on a fresh YAML line.

    Composes the candidate line (``yaml.compose``, never a dumper) and checks
    whether the resulting node round-trips to exactly ``value`` *as an
    implicit string* -- not merely ``kind is SCALAR``, since a bare
    ``FrontmatterField`` deliberately does not distinguish a string scalar
    from a bool/int/date one (see ``frontmatter_yaml``'s scalar-resolution
    rule) and writing ``status: true`` unquoted would silently become a YAML
    boolean on the next parse. Ambiguous scalars, values needing YAML-special
    leading characters, values YAML would truncate at a comment marker, and
    multi-line values all fall back to a JSON-quoted string, matching the
    existing frontmatter list-item serialization.
    """
    candidate = f"k: {value}\n"
    try:
        node = yaml.compose(candidate, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return json.dumps(value, ensure_ascii=False)
    if isinstance(node, yaml.MappingNode) and len(node.value) == 1:
        _, value_node = node.value[0]
        if (
            isinstance(value_node, yaml.ScalarNode)
            and value_node.tag == _YAML_STR_TAG
            and value_node.value == value
        ):
            return value
    return json.dumps(value, ensure_ascii=False)


# --- frontmatter guard + reparse verification -----------------------------------

_BROKEN_FRONTMATTER_CODES = {"frontmatter.yaml-error", "frontmatter.not-a-mapping"}


def _refuse_broken_frontmatter(document: NoteDocument) -> None:
    """Refuse frontmatter edits when the frontmatter failed to parse as YAML.

    A ``frontmatter.yaml-error``/``frontmatter.not-a-mapping`` diagnostic means
    :class:`~oaw.document.frontmatter_yaml.FrontmatterModel` has zero usable
    fields for the whole block; splicing based on that empty model would
    silently corrupt whatever malformed YAML is actually on disk.
    """
    if any(d.code in _BROKEN_FRONTMATTER_CODES for d in document.diagnostics):
        raise OawError("note frontmatter is not parseable YAML; refusing frontmatter edits")


def _verify_scalar_set(key: str, value: str) -> Callable[[NoteDocument], None]:
    def verify(new_document: NoteDocument) -> None:
        broken = any(d.code in _BROKEN_FRONTMATTER_CODES for d in new_document.diagnostics)
        field = new_document.frontmatter.field(key) if new_document.frontmatter else None
        if broken or field is None or field.kind is not FieldKind.SCALAR or field.scalar != value:
            raise OawError(f"failed to verify frontmatter field {key!r} after the edit")

    return verify


def _verify_list_item_appended(key: str, value: str) -> Callable[[NoteDocument], None]:
    def verify(new_document: NoteDocument) -> None:
        field = new_document.frontmatter.field(key) if new_document.frontmatter else None
        if (
            field is None
            or field.kind is not FieldKind.STRING_LIST
            or value not in (field.items or ())
        ):
            raise OawError(f"failed to verify frontmatter list field {key!r} after the edit")

    return verify


def _verify_list_item_removed(key: str, value: str) -> Callable[[NoteDocument], None]:
    def verify(new_document: NoteDocument) -> None:
        field = new_document.frontmatter.field(key) if new_document.frontmatter else None
        if field is None:
            return
        if field.kind is not FieldKind.STRING_LIST or value in (field.items or ()):
            raise OawError(f"failed to verify removal from frontmatter field {key!r}")

    return verify


# --- set_frontmatter_scalar ------------------------------------------------------


def _validate_raw_scalar(key: str, value: str) -> None:
    """Refuse ``value`` unless it round-trips verbatim as a single YAML scalar.

    Used when a caller opts out of :func:`_scalar_literal`'s JSON-quoting
    default (``raw=True``) for a field whose external consumers (e.g. an
    Obsidian Base) require a bare, unquoted value. Composes the candidate
    line and requires a single :class:`yaml.ScalarNode` whose raw text is
    exactly ``value`` -- this rejects values YAML would truncate at a
    comment marker (the composed node's text would then be shorter than
    ``value``), multi-line values (the candidate would either fail to
    compose as one mapping entry or fold/re-render with different text),
    and flow collections (``[a, b]``/``{a: b}`` compose to a non-scalar
    node).
    """
    error = OawError(f"{key} value cannot be written as a raw YAML scalar")
    if "\n" in value or "\r" in value:
        raise error
    candidate = f"k: {value}\n"
    try:
        node = yaml.compose(candidate, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise error from None
    if not isinstance(node, yaml.MappingNode) or len(node.value) != 1:
        raise error
    _, value_node = node.value[0]
    if not isinstance(value_node, yaml.ScalarNode) or value_node.value != value:
        raise error


def set_frontmatter_scalar(
    document: NoteDocument, key: str, value: str, *, raw: bool = False
) -> EditResult:
    """Rewrite (or insert) a single scalar frontmatter field.

    Requires ``document.frontmatter.safe_to_rewrite(key)`` and a ``SCALAR``
    kind when the field already exists; a missing key inserts a new
    ``key: value`` line just before the closing ``---`` delimiter.

    ``raw=True`` opts out of :func:`_scalar_literal`'s default JSON-quoting
    for YAML-ambiguous values (numbers, booleans, dates, ...) and splices
    ``value`` verbatim instead, after :func:`_validate_raw_scalar` confirms
    it is safe to write unquoted. Use this only for fields whose values are
    already constrained elsewhere (e.g. task priority) -- an unconstrained
    value written raw could silently change YAML type on the next parse.
    """
    if document.frontmatter is None:
        raise OawError("note has no YAML frontmatter")
    _refuse_broken_frontmatter(document)
    if key in document.frontmatter.duplicated_keys():
        raise OawError(f"note frontmatter contains duplicate field: {key}")

    if raw:
        _validate_raw_scalar(key, value)
        literal = value
    else:
        literal = _scalar_literal(value)
    field = document.frontmatter.field(key)
    verify = _verify_scalar_set(key, value)

    if field is not None:
        if field.kind is not FieldKind.SCALAR or not document.frontmatter.safe_to_rewrite(key):
            raise OawError(f"{key} must be a scalar field before OAW can rewrite it safely")
        if field.value_span is not None:
            edit = SourceEdit(span=field.value_span, text=literal)
        else:
            colon = document.source.index(":", field.key_span.end, field.entry_span.end)
            insertion_point = colon + 1
            edit = SourceEdit(span=SourceSpan(insertion_point, insertion_point), text=f" {literal}")
        return apply_edits(document, [edit], verify=verify)

    assert document.envelope.frontmatter_inner_span is not None
    insertion_point = document.envelope.frontmatter_inner_span.end
    edit = SourceEdit(
        span=SourceSpan(insertion_point, insertion_point),
        text=f"{key}: {literal}{document.newline}",
    )
    return apply_edits(document, [edit], verify=verify)


# --- append_frontmatter_list_item / remove_frontmatter_list_item ----------------


def append_frontmatter_list_item(document: NoteDocument, key: str, value: str) -> EditResult:
    """Append ``value`` to a flat YAML block list field, creating it if absent.

    A ``value`` already present is a no-op: returns the original ``document``
    unchanged with zero edits. Refuses (matching the message style of
    ``oaw.frontmatter.append_frontmatter_list_value``) when the field is a
    flow-style list (``[a, b]``), a scalar, or a nested/non-scalar ("OTHER")
    value -- none of those can be safely spliced as a new ``- item`` line.
    """
    if document.frontmatter is None:
        raise OawError("note has no YAML frontmatter")
    _refuse_broken_frontmatter(document)
    if key in document.frontmatter.duplicated_keys():
        raise OawError(f"note frontmatter contains duplicate field: {key}")

    field = document.frontmatter.field(key)
    verify = _verify_list_item_appended(key, value)
    if field is None:
        assert document.envelope.frontmatter_inner_span is not None
        insertion_point = document.envelope.frontmatter_inner_span.end
        item_literal = json.dumps(value, ensure_ascii=False)
        text = f"{key}:{document.newline}  - {item_literal}{document.newline}"
        edit = SourceEdit(span=SourceSpan(insertion_point, insertion_point), text=text)
        return apply_edits(document, [edit], verify=verify)

    if field.kind is FieldKind.OTHER:
        raise OawError(f"{key} must be a flat YAML block list before OAW can append safely")
    if field.kind is not FieldKind.STRING_LIST:
        raise OawError(f"{key} must use a YAML block list before OAW can append safely")
    assert field.value_span is not None
    if document.slice(field.value_span).lstrip().startswith("["):
        raise OawError(f"{key} must use a YAML block list before OAW can append safely")
    if not document.frontmatter.safe_to_rewrite(key):
        raise OawError(f"{key} must be a flat YAML block list before OAW can append safely")

    items = field.items or ()
    if value in items:
        return EditResult(source=document.source, document=document, edits=())

    assert field.item_spans
    item_literal = json.dumps(value, ensure_ascii=False)
    first_item_start = field.item_spans[0].start
    line_start = document.source.rfind("\n", 0, first_item_start) + 1
    prefix = document.source[line_start:first_item_start]
    insertion_point = field.value_span.end
    text = f"{prefix}{item_literal}{document.newline}"
    edit = SourceEdit(span=SourceSpan(insertion_point, insertion_point), text=text)
    return apply_edits(document, [edit], verify=verify)


def remove_frontmatter_list_item(document: NoteDocument, key: str, value: str) -> EditResult:
    """Remove ``value`` from a flat YAML block list field.

    Mirrors ``oaw.frontmatter.remove_frontmatter_list_value``: a missing key
    or a value not present in the list both raise
    ``OawError(f"{key} relationship is not present")``; removing the last
    remaining item removes the whole entry (key line and all item lines)
    rather than leaving an empty ``key:``.
    """
    if document.frontmatter is None:
        raise OawError("note has no YAML frontmatter")
    _refuse_broken_frontmatter(document)
    if key in document.frontmatter.duplicated_keys():
        raise OawError(f"note frontmatter contains duplicate field: {key}")

    field = document.frontmatter.field(key)
    if field is None:
        raise OawError(f"{key} relationship is not present")

    if field.kind is FieldKind.OTHER:
        raise OawError(f"{key} must be a flat YAML block list before OAW can remove safely")
    if field.kind is not FieldKind.STRING_LIST:
        raise OawError(f"{key} must use a YAML block list before OAW can remove safely")
    assert field.value_span is not None
    if document.slice(field.value_span).lstrip().startswith("["):
        raise OawError(f"{key} must use a YAML block list before OAW can remove safely")
    if not document.frontmatter.safe_to_rewrite(key):
        raise OawError(f"{key} must be a flat YAML block list before OAW can remove safely")

    items = field.items or ()
    if value not in items:
        raise OawError(f"{key} relationship is not present")

    assert field.item_spans is not None
    matching = [i for i, item in enumerate(items) if item == value]
    verify = _verify_list_item_removed(key, value)

    if len(matching) == len(items):
        edit = SourceEdit(span=field.entry_span, text="")
        return apply_edits(document, [edit], verify=verify)

    edits = []
    for idx in matching:
        item_span = field.item_spans[idx]
        line_start = document.source.rfind("\n", 0, item_span.start) + 1
        if item_span.end > 0 and document.source[item_span.end - 1] == "\n":
            line_end = item_span.end
        else:
            newline_pos = document.source.find("\n", item_span.end)
            line_end = newline_pos + 1 if newline_pos != -1 else len(document.source)
        edits.append(SourceEdit(span=SourceSpan(line_start, line_end), text=""))
    return apply_edits(document, edits, verify=verify)
