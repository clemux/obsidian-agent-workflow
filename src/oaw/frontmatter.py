"""Hand-rolled frontmatter parsing and document-layer-backed mutation helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from oaw.document import FieldKind, FrontmatterField, NoteDocument, SourceSpan, parse_note_source
from oaw.document import editing as _editing

from .errors import OawError

FRONTMATTER_READ_LIMIT = 64 * 1024


def split_inline_comment(value: str) -> tuple[str, str]:
    """Split a YAML-style trailing comment while respecting quoted strings."""
    quote: str | None = None
    escaped = False
    for position, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {'"', "'"}:
            quote = None if quote == character else character if quote is None else quote
            continue
        if character == "#" and quote is None and position > 0 and value[position - 1].isspace():
            comment_start = position - 1
            while comment_start > 0 and value[comment_start - 1].isspace():
                comment_start -= 1
            return value[:comment_start].rstrip(), value[comment_start:]
    return value.rstrip(), ""


def unquote_scalar(value: str) -> str:
    value = value.strip()
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1]
    return value


def parse_frontmatter(fm: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list: str | None = None
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if current_list and re.match(r"^\s*-\s+", line):
            data.setdefault(current_list, [])
            item = re.sub(r"^\s*-\s+", "", line)
            cast = data[current_list]
            if isinstance(cast, list):
                cast.append(unquote_scalar(item))
            continue
        current_list = None
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value, _ = split_inline_comment(value.strip())
        if not value:
            data[key] = []
            current_list = key
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [] if not inner else [unquote_scalar(p) for p in inner.split(",")]
        else:
            data[key] = unquote_scalar(value)
    return data


def frontmatter_may_match(frontmatter: str, target: str) -> bool:
    """Return whether raw frontmatter might contain an ID or alias match.

    This deliberately permits false positives.  Resolver performs the authoritative
    parsed comparison after this inexpensive check.
    """
    return target in frontmatter


def read_frontmatter_text(
    path: Path,
    *,
    max_bytes: int | None = FRONTMATTER_READ_LIMIT,
    require_closed: bool = True,
) -> str:
    """Read only the YAML frontmatter body with configurable safety behavior."""
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return ""
        total = len(first.encode("utf-8"))
        lines: list[str] = []
        for line in handle:
            total += len(line.encode("utf-8"))
            if max_bytes is not None and total > max_bytes:
                raise OawError(f"frontmatter too large or not closed before safety limit: {path}")
            if line.strip() == "---":
                return "".join(lines)
            lines.append(line)
    if require_closed:
        raise OawError(f"frontmatter is not closed: {path}")
    return ""


def read_frontmatter_only(path: Path) -> tuple[str, dict[str, object]]:
    """Read and parse a note's frontmatter without reading its body."""
    frontmatter = read_frontmatter_text(path)
    return frontmatter, parse_frontmatter(frontmatter)


def _frontmatter_is_unclosed(document: NoteDocument) -> bool:
    """Whether ``document`` has an opened-but-never-closed frontmatter block.

    The document layer treats a missing closing ``---`` as "no frontmatter at
    all" (the whole source becomes body), recording an
    ``envelope.unclosed-frontmatter`` diagnostic instead. The legacy helpers
    distinguish that case from a genuinely absent frontmatter block in their
    error wording, so wrappers check this diagnostic to keep the same message.
    """
    return any(d.code == "envelope.unclosed-frontmatter" for d in document.envelope.diagnostics)


def _is_blank_field(field: FrontmatterField | None) -> bool:
    """Whether ``field`` is present but written with no value at all (``key:``).

    The document layer classifies a bare ``key:`` as ``FieldKind.SCALAR`` with
    ``scalar=None`` -- a real, distinct field, just an empty one. The legacy
    hand-rolled parser instead read a value-less key as the *start of an empty
    block list*, so callers routinely scaffold list fields this way (e.g. a
    freshly created capture's blank ``destinations:``). The list mutators
    below restore that equivalence explicitly since the document layer's list
    ops require an existing field to already be ``STRING_LIST``.
    """
    return field is not None and field.kind is FieldKind.SCALAR and field.scalar is None


def _blank_field_has_trailing_content(text: str, field: FrontmatterField) -> bool:
    """Whether anything -- even just a comment -- follows the colon on ``field``'s key line.

    The document layer's YAML composer strips comments while scanning, so a
    bare ``key:`` line and ``key: # comment`` both compose to the same empty
    scalar node (see :func:`_is_blank_field`). Legacy parity requires
    refusing the comment-carrying case (rather than silently discarding the
    comment while migrating the field), so this inspects the raw source text
    following the colon instead of the composed node.
    """
    colon = text.index(":", field.key_span.end, field.entry_span.end)
    remainder = text[colon + 1 : field.entry_span.end]
    return remainder.strip() != ""


def _append_first_list_item_in_place(
    document: NoteDocument, field: FrontmatterField, key: str, value: str
) -> str:
    """Insert the first ``- "item"`` line directly below a bare ``key:`` line.

    Legacy parity: a value-less ``key:`` line scaffolds an empty block list
    (see :func:`_is_blank_field`), but the field must stay exactly where it
    was written. The new item line is spliced in immediately after the
    existing key line rather than deleting the field and recreating it at
    the end of the frontmatter block, which would silently move it.
    """
    item_literal = json.dumps(value, ensure_ascii=False)
    insertion_point = field.entry_span.end
    inserted = f"  - {item_literal}{document.newline}"
    edit = _editing.SourceEdit(span=SourceSpan(insertion_point, insertion_point), text=inserted)

    def verify(new_document: NoteDocument) -> None:
        new_field = new_document.frontmatter.field(key) if new_document.frontmatter else None
        if (
            new_field is None
            or new_field.kind is not FieldKind.STRING_LIST
            or value not in (new_field.items or ())
        ):
            raise OawError(f"failed to verify frontmatter list field {key!r} after the edit")

    return _editing.apply_edits(document, [edit], verify=verify).source


def set_frontmatter_scalar(text: str, key: str, value: str, *, raw: bool = False) -> str:
    document = parse_note_source(text)
    if document.frontmatter is None:
        if _frontmatter_is_unclosed(document):
            raise OawError("task note frontmatter is not closed")
        raise OawError("task note has no YAML frontmatter")
    return _editing.set_frontmatter_scalar(document, key, value, raw=raw).source


def parse_yaml_string_list_item(raw: str, key: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            parsed, end = json.JSONDecoder().raw_decode(value)
        except json.JSONDecodeError as exc:
            raise OawError(f"{key} contains an invalid quoted string") from exc
        remainder = value[end:].strip()
        if not isinstance(parsed, str) or (remainder and not remainder.startswith("#")):
            raise OawError(f"{key} must contain only string list items")
        return parsed
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        if not match:
            raise OawError(f"{key} contains an invalid quoted string")
        return match.group(1).replace("''", "'")

    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    ambiguous = re.fullmatch(
        r"(?i:true|false|null|yes|no|on|off|~|[-+]?\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2})",
        value,
    )
    if (
        not value
        or value.startswith(("[", "{", "&", "*", "!", "|", ">", "@", "`"))
        or re.search(r":\s", value)
        or ambiguous
    ):
        raise OawError(f"{key} must contain only unambiguous string list items")
    return value


def append_frontmatter_list_value(text: str, key: str, value: str) -> str:
    document = parse_note_source(text)
    if document.frontmatter is None:
        if _frontmatter_is_unclosed(document):
            raise OawError("note frontmatter is not closed")
        raise OawError("note has no YAML frontmatter")
    field = document.frontmatter.field(key)
    if _is_blank_field(field):
        assert field is not None
        if _blank_field_has_trailing_content(text, field):
            raise OawError(f"{key} must use a YAML block list before OAW can append safely")
        return _append_first_list_item_in_place(document, field, key, value)
    return _editing.append_frontmatter_list_item(document, key, value).source


def remove_frontmatter_list_value(text: str, key: str, value: str) -> str:
    """Remove one exact string from a flat YAML block list without reformatting it."""
    document = parse_note_source(text)
    if document.frontmatter is None:
        if _frontmatter_is_unclosed(document):
            raise OawError("note frontmatter is not closed")
        raise OawError("note has no YAML frontmatter")
    field = document.frontmatter.field(key)
    if _is_blank_field(field):
        assert field is not None
        if not _blank_field_has_trailing_content(text, field):
            raise OawError(f"{key} relationship is not present")
    return _editing.remove_frontmatter_list_item(document, key, value).source
