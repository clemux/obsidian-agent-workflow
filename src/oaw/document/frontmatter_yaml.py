"""Source-preserving YAML frontmatter composition.

This module classifies one note's YAML frontmatter block using
``yaml.compose`` (never ``yaml.load``/``yaml.dump``): it walks the composed
node tree with :class:`yaml.SafeLoader` resolution rules to classify each
top-level field and to recover *absolute* :class:`~oaw.document.types.SourceSpan`
offsets into the whole note source, never into the isolated frontmatter text.

Parsing never raises on malformed input. YAML syntax errors, a non-mapping
root, duplicate keys, and unsupported nodes (anchors, aliases, explicit
tags, merge keys) are all recorded as :class:`~oaw.document.types.Diagnostic`
entries on the resulting :class:`FrontmatterModel`; only programmer errors
(bad arguments) raise.

PyYAML's composer resolves an alias to the *same* ``Node`` object that was
produced at its anchor's definition site, so an aliased node's own
``start_mark``/``end_mark`` describe the anchor definition, not the alias
reference. To flag both sites we track anchor/alias/explicit-tag events as
they are consumed by a small :class:`yaml.SafeLoader` subclass, rather than
inspecting the composed tree after the fact.

``Mark.index`` is a plain Python ``str`` index into whatever text was fed to
the loader (verified empirically against BOM/CRLF sources — CRLF's ``\\r``
and ``\\n`` each count as one index step, exactly like Python string
indexing), so absolute offsets are simply ``inner_span.start + mark.index``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import yaml

from oaw.document.types import Diagnostic, Severity, SourceSpan

_STR_TAG = "tag:yaml.org,2002:str"
_MERGE_KEY = "<<"


class FieldKind(StrEnum):
    """Shape of one frontmatter field's composed value."""

    SCALAR = "scalar"
    STRING_LIST = "string-list"
    OTHER = "other"


@dataclass(frozen=True)
class FrontmatterField:
    """One top-level frontmatter entry, in document order."""

    key: str
    kind: FieldKind
    key_span: SourceSpan
    value_span: SourceSpan | None
    entry_span: SourceSpan
    scalar: str | None
    items: tuple[str, ...] | None
    item_spans: tuple[SourceSpan, ...] | None
    #: PyYAML scalar style for SCALAR fields: ``None`` (plain), ``'`` / ``"``
    #: (quoted), or ``|`` / ``>`` (block). Block scalars' composed spans include
    #: the terminating newline, so mutation helpers must refuse them.
    scalar_style: str | None = None


@dataclass(frozen=True)
class FrontmatterModel:
    """Composed view of one note's frontmatter block."""

    fields: tuple[FrontmatterField, ...]
    diagnostics: tuple[Diagnostic, ...]

    def field(self, key: str) -> FrontmatterField | None:
        """Return the single field named ``key``, or ``None`` if absent/duplicated."""
        matches = [f for f in self.fields if f.key == key]
        return matches[0] if len(matches) == 1 else None

    def value(self, key: str) -> str | list[str] | None:
        """Convenience accessor over :meth:`field` for scalar/string-list fields."""
        found = self.field(key)
        if found is None:
            return None
        if found.kind is FieldKind.SCALAR:
            return found.scalar
        if found.kind is FieldKind.STRING_LIST:
            return list(found.items) if found.items is not None else None
        return None

    def duplicated_keys(self) -> tuple[str, ...]:
        """Keys that appear more than once, in first-seen order."""
        counts = Counter(f.key for f in self.fields)
        seen: list[str] = []
        for f in self.fields:
            if counts[f.key] > 1 and f.key not in seen:
                seen.append(f.key)
        return tuple(seen)

    def safe_to_rewrite(self, key: str) -> bool:
        """Whether a mutation helper may splice this field's value in place."""
        matches = [f for f in self.fields if f.key == key]
        if len(matches) != 1:
            return False
        target = matches[0]
        if target.kind not in (FieldKind.SCALAR, FieldKind.STRING_LIST):
            return False
        if target.scalar_style in ("|", ">"):
            return False
        unsafe_codes = {"frontmatter.unsupported-node", "frontmatter.yaml-error"}
        for diagnostic in self.diagnostics:
            if (
                diagnostic.code in unsafe_codes
                and diagnostic.span is not None
                and diagnostic.span.overlaps(target.entry_span)
            ):
                return False
        for other in self.fields:
            if other is not target and other.entry_span.overlaps(target.entry_span):
                return False
        return True


OWNED_FIELD_TYPES: Mapping[str, FieldKind] = {
    "id": FieldKind.SCALAR,
    "type": FieldKind.SCALAR,
    "status": FieldKind.SCALAR,
    "project": FieldKind.SCALAR,
    "priority": FieldKind.SCALAR,
    "effort": FieldKind.SCALAR,
    "preparedness": FieldKind.SCALAR,
    "created": FieldKind.SCALAR,
    "execution": FieldKind.SCALAR,
    "source-capture": FieldKind.SCALAR,
    "aliases": FieldKind.STRING_LIST,
    "tags": FieldKind.STRING_LIST,
    "session-ids": FieldKind.STRING_LIST,
    "destinations": FieldKind.STRING_LIST,
    "blocked-by": FieldKind.STRING_LIST,
    "follows": FieldKind.STRING_LIST,
    "follow-up-to": FieldKind.STRING_LIST,
}


def validate_owned_fields(model: FrontmatterModel) -> tuple[Diagnostic, ...]:
    """Flag OAW-owned fields whose composed kind does not match its schema."""
    diagnostics: list[Diagnostic] = []
    for f in model.fields:
        expected = OWNED_FIELD_TYPES.get(f.key)
        if expected is None or f.kind is expected:
            continue
        if f.kind is FieldKind.SCALAR and f.scalar is None and f.value_span is None:
            # A bare ``key:`` with nothing after the colon is the vault's
            # established blank-field convention: it satisfies either an
            # empty scalar or an empty string-list, so it is never flagged.
            continue
        diagnostics.append(
            Diagnostic(
                code="frontmatter.owned-field-type",
                message=f"field {f.key!r} must be {expected.value}, found {f.kind.value}",
                severity=Severity.ERROR,
                span=f.entry_span,
            )
        )
    return tuple(diagnostics)


class _TrackingSafeLoader(yaml.SafeLoader):
    """SafeLoader that records anchor/alias/explicit-tag events as it composes.

    A node produced from an alias reuses the anchor definition's own node
    object (and therefore its marks), so both sites must be captured while
    events are still being consumed rather than recovered from the tree.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.oaw_flag_spans: list[tuple[int, int]] = []
        # Keyed by id(key_node): an alias used directly as a mapping value
        # reuses its anchor definition's Node (and thus its marks), so the
        # *reference* site is recorded separately, by the mapping key that
        # immediately owns it, while events are still live.
        self.oaw_direct_alias_spans: dict[int, tuple[int, int]] = {}

    def compose_node(self, parent, index):  # noqa: ANN001 - matches PyYAML signature
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            span = (event.start_mark.index, event.end_mark.index)
            self.oaw_flag_spans.append(span)
            if index is not None and not isinstance(index, int):
                self.oaw_direct_alias_spans[id(index)] = span
            return super().compose_node(parent, index)
        peek = self.peek_event()
        anchor = peek.anchor
        explicit_tag = getattr(peek, "tag", None) is not None
        node = super().compose_node(parent, index)
        if node is not None and (anchor is not None or explicit_tag):
            self.oaw_flag_spans.append((node.start_mark.index, node.end_mark.index))
        return node


def _start_of_line(source: str, pos: int) -> int:
    newline = source.rfind("\n", 0, pos)
    return newline + 1


def _end_of_line(source: str, pos: int) -> int:
    """Extend ``pos`` to just past the end of its physical line.

    If ``pos`` already sits at a line boundary (immediately after a ``\\n``,
    or at offset 0), it is returned unchanged: a block collection's
    ``end_mark`` already lands there because the scanner consumed the
    trailing newline of its last item.
    """
    if pos > 0 and source[pos - 1] == "\n":
        return pos
    end = len(source)
    idx = pos
    while idx < end and source[idx] not in "\r\n":
        idx += 1
    if idx < end and source[idx] == "\r":
        idx += 1
    if idx < end and source[idx] == "\n":
        idx += 1
    return idx


def _is_empty_value(node: yaml.Node | None) -> bool:
    """True for ``key:`` with nothing after the colon (zero-width null scalar)."""
    return (
        node is not None
        and isinstance(node, yaml.ScalarNode)
        and node.start_mark.index == node.end_mark.index
    )


def _node_span(node: yaml.Node, offset: int) -> SourceSpan:
    return SourceSpan(offset + node.start_mark.index, offset + node.end_mark.index)


def _occurrence_span(
    value_node: yaml.Node | None,
    alias_override: tuple[int, int] | None,
    offset: int,
) -> SourceSpan | None:
    """Span of the value as it actually occurs at this position.

    A direct alias reuses its anchor definition's node (and marks), so its
    true position comes from the alias reference event, not the node.
    """
    if alias_override is not None:
        return SourceSpan(offset + alias_override[0], offset + alias_override[1])
    if value_node is not None:
        return _node_span(value_node, offset)
    return None


def compose_frontmatter(source: str, inner_span: SourceSpan) -> FrontmatterModel:
    """Compose the YAML frontmatter text at ``inner_span`` of ``source``.

    ``inner_span`` is the envelope's frontmatter inner span (the YAML text
    strictly between the ``---`` delimiter lines); all spans in the returned
    model are absolute offsets into the whole ``source``.
    """
    offset = inner_span.start
    inner_text = source[inner_span.start : inner_span.end]

    loader = _TrackingSafeLoader(inner_text)
    try:
        try:
            root = loader.get_single_node()
        except yaml.YAMLError as exc:
            return FrontmatterModel(
                fields=(),
                diagnostics=(
                    Diagnostic(
                        code="frontmatter.yaml-error",
                        message=str(exc),
                        severity=Severity.ERROR,
                        span=inner_span,
                    ),
                ),
            )
    finally:
        loader.dispose()

    if root is None:
        return FrontmatterModel(fields=(), diagnostics=())

    if not isinstance(root, yaml.MappingNode):
        return FrontmatterModel(
            fields=(),
            diagnostics=(
                Diagnostic(
                    code="frontmatter.not-a-mapping",
                    message="frontmatter root is not a YAML mapping",
                    severity=Severity.ERROR,
                    span=_node_span(root, offset),
                ),
            ),
        )

    flag_spans = [SourceSpan(offset + s, offset + e) for s, e in loader.oaw_flag_spans]

    diagnostics: list[Diagnostic] = []
    fields: list[FrontmatterField] = []
    key_counts: Counter[str] = Counter()

    Entry = tuple[str, yaml.Node | None, tuple[int, int] | None, SourceSpan, SourceSpan]
    raw_entries: list[Entry] = []
    for key_node, value_node in root.value:
        key_text = key_node.value if isinstance(key_node.value, str) else str(key_node.value)
        key_span = _node_span(key_node, offset)
        alias_override = loader.oaw_direct_alias_spans.get(id(key_node))
        effective_value = None if _is_empty_value(value_node) else value_node
        occurrence = _occurrence_span(effective_value, alias_override, offset)
        entry_end_ref = occurrence.end if occurrence is not None else key_span.end
        entry_span = SourceSpan(
            _start_of_line(source, key_span.start), _end_of_line(source, entry_end_ref)
        )
        raw_entries.append((key_text, effective_value, alias_override, key_span, entry_span))
        key_counts[key_text] += 1

    for key_text, value_node, alias_override, key_span, entry_span in raw_entries:
        occurrence = _occurrence_span(value_node, alias_override, offset)
        tight_span = SourceSpan(
            key_span.start, occurrence.end if occurrence is not None else key_span.end
        )
        has_unsupported = key_text == _MERGE_KEY or any(
            tight_span.contains(flag) for flag in flag_spans
        )
        if has_unsupported:
            diagnostics.append(
                Diagnostic(
                    code="frontmatter.unsupported-node",
                    message=f"field {key_text!r} contains an anchor, alias, explicit tag, "
                    "or merge key that OAW does not support",
                    severity=Severity.ERROR,
                    span=occurrence if occurrence is not None else key_span,
                )
            )

        if key_counts[key_text] > 1:
            diagnostics.append(
                Diagnostic(
                    code="frontmatter.duplicate-key",
                    message=f"duplicate frontmatter key: {key_text}",
                    severity=Severity.ERROR,
                    span=key_span,
                )
            )

        kind, scalar, value_span, items, item_spans, scalar_style = _classify(
            value_node, occurrence, offset, has_unsupported
        )
        fields.append(
            FrontmatterField(
                key=key_text,
                kind=kind,
                key_span=key_span,
                value_span=value_span,
                entry_span=entry_span,
                scalar=scalar,
                items=items,
                item_spans=item_spans,
                scalar_style=scalar_style,
            )
        )

    return FrontmatterModel(fields=tuple(fields), diagnostics=tuple(diagnostics))


def _classify(
    value_node: yaml.Node | None,
    occurrence: SourceSpan | None,
    offset: int,
    has_unsupported: bool,
) -> tuple[
    FieldKind,
    str | None,
    SourceSpan | None,
    tuple[str, ...] | None,
    tuple[SourceSpan, ...] | None,
    str | None,
]:
    if value_node is None:
        return FieldKind.SCALAR, None, None, None, None, None

    if has_unsupported:
        # occurrence reflects the true reference site: a direct alias value
        # reuses its anchor definition's own, differently-positioned, node.
        return FieldKind.OTHER, None, occurrence, None, None, None

    # Not a direct alias/anchor/tag reference here, so value_node's own marks
    # are trustworthy for absolute-offset math.
    value_span = _node_span(value_node, offset)

    if isinstance(value_node, yaml.ScalarNode):
        scalar = value_node.value if value_node.tag == _STR_TAG else str(value_node.value)
        return FieldKind.SCALAR, scalar, value_span, None, None, value_node.style

    if isinstance(value_node, yaml.SequenceNode):
        # Block-scalar items ("|"/">") compose with spans that include their
        # terminating newline, which breaks per-line splice math downstream;
        # a list containing one is not a *flat* string list for mutation.
        if all(
            isinstance(item, yaml.ScalarNode)
            and item.tag == _STR_TAG
            and item.style not in ("|", ">")
            for item in value_node.value
        ):
            items = tuple(item.value for item in value_node.value)
            item_spans = tuple(_node_span(item, offset) for item in value_node.value)
            return FieldKind.STRING_LIST, None, value_span, items, item_spans, None
        return FieldKind.OTHER, None, value_span, None, None, None

    return FieldKind.OTHER, None, value_span, None, None, None
