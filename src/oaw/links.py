"""Durable wikilink parsing and commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import parse_frontmatter
from .notes import (
    VaultTransaction,
    append_markdown_block_to_section,
    fence_closes,
    fence_delimiter,
    read_note,
    split_note,
)
from .resolver import (
    NoteMatch,
    NoteReference,
    iter_markdown,
    resolve_id,
    resolve_id_from_references,
    scan_note_references,
    strip_obs_prefix,
    title_from_body,
)


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    start: int
    line: str


@dataclass(frozen=True)
class ObsReferenceReplacement:
    """One explicit obs reference and its durable replacement."""

    reference: str
    link: str


@dataclass(frozen=True)
class ReferenceDefinition:
    """One validated CommonMark link-reference definition span."""

    label: str
    start: int
    end: int


OBS_REFERENCE_RE = re.compile(r"obs:([A-Za-z0-9][A-Za-z0-9_-]*)")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)")


def note_from_path(path: Path, root: Path, matched_by: str = "path") -> NoteMatch:
    try:
        _, fm, body = read_note(path)
        data = parse_frontmatter(fm)
    except UnicodeDecodeError as exc:
        raise OawError(f"note is not valid UTF-8: {path}") from exc
    rel = path.relative_to(root).as_posix()
    note_id = data.get("id")
    return NoteMatch(
        path=path,
        relpath=rel,
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by=matched_by,
        title=title_from_body(path, body),
        frontmatter_text=fm.rstrip(),
        frontmatter=data,
    )


def resolve_note_arg(value: str, root: Path) -> NoteMatch:
    try:
        return resolve_id(value, root)
    except OawError as id_error:
        raw = strip_obs_prefix(value)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.suffix != ".md":
            candidate = candidate.with_suffix(".md")
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise OawError(f"note path is outside vault: {value}") from exc
        if not candidate.exists():
            raise id_error
        return note_from_path(candidate, root)


def durable_link_target(match: NoteMatch) -> str:
    return Path(match.relpath).with_suffix("").as_posix()


def durable_wikilink(match: NoteMatch, label: str | None = None) -> str:
    display = (label or match.note_id or match.title).strip()
    return f"[[{durable_link_target(match)}|{display}]]"


def _is_escaped(text: str, index: int) -> bool:
    """Return whether a character has an odd number of Markdown escapes before it."""
    escapes = 0
    while index > 0 and text[index - 1] == "\\":
        escapes += 1
        index -= 1
    return escapes % 2 == 1


def _starts_inline_block_boundary(text: str, start: int) -> bool:
    """Return whether a new physical line starts a separate Markdown block."""
    end = text.find("\n", start)
    line = text[start:] if end < 0 else text[start:end]
    line = line.rstrip("\r")
    if not line.strip() or line.startswith("\t") or line.startswith("    "):
        return True
    return bool(
        re.match(
            r" {0,3}(?:#{1,6}(?:[ \t]|$)|>|`{3,}|~{3,}|(?:[-+*]|\d+[.)])[ \t]+)",
            line,
        )
        or re.fullmatch(
            r" {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,}|=+[ \t]*)",
            line,
        )
    )


def _balanced_bracket_end(text: str, start: int) -> int | None:
    """Return the inclusive end of a balanced Markdown bracket group."""
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            index += 2
            continue
        if text[index] == "\n" and _starts_inline_block_boundary(text, index + 1):
            return None
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _balanced_parenthesis_end(text: str, start: int) -> int | None:
    """Return the exclusive end of a balanced Markdown link destination."""
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            index += 2
            continue
        if text[index] == "\n" and _starts_inline_block_boundary(text, index + 1):
            return None
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _normalize_reference_label(label: str) -> str:
    unescaped = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])", r"\1", label)
    return re.sub(r"\s+", " ", unescaped.strip()).casefold()


def _valid_reference_label(label: str) -> bool:
    """Return whether ``label`` meets CommonMark link-label constraints."""
    if not label or not label.strip() or len(label) > 999:
        return False
    return "\x00" not in label


def _inline_link_end(line: str, start: int, reference_labels: set[str] | None = None) -> int | None:
    """Return the end of an inline or reference Markdown link."""
    label_end = _balanced_bracket_end(line, start)
    if label_end is None:
        return None
    if label_end + 1 < len(line):
        if line[label_end + 1] == "(":
            return _balanced_parenthesis_end(line, label_end + 1)
        if line[label_end + 1] == "[":
            target_end = _balanced_bracket_end(line, label_end + 1)
            return target_end + 1 if target_end is not None else None
    if reference_labels is not None:
        label = _normalize_reference_label(line[start + 1 : label_end])
        if label in reference_labels:
            return label_end + 1
    return None


def _skip_reference_whitespace(text: str, start: int) -> tuple[int, int]:
    """Skip definition whitespace, allowing at most one physical line ending."""
    index = start
    line_endings = 0
    while index < len(text):
        if text[index] in " \t":
            index += 1
        elif text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            if line_endings:
                break
            line_endings += 1
            index += 2
        elif text[index] == "\n":
            if line_endings:
                break
            line_endings += 1
            index += 1
        else:
            break
    return index, line_endings


def _reference_destination_end(text: str, start: int) -> int | None:
    """Return the end of a valid CommonMark link destination."""
    line_end = text.find("\n", start)
    line_end = len(text) if line_end < 0 else line_end
    if start >= line_end or text[start] == "\r":
        return None
    index = start
    if text[index] == "<":
        index += 1
        while index < line_end:
            if text[index] == "\\" and index + 1 < line_end:
                index += 2
                continue
            if text[index] == ">":
                return index + 1
            if text[index] in "<>\r":
                return None
            index += 1
        return None
    depth = 0
    consumed = False
    while index < line_end and text[index] not in " \t\r":
        if text[index] == "\\" and index + 1 < line_end:
            consumed = True
            index += 2
            continue
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            if depth == 0:
                return None
            depth -= 1
        elif text[index] in "<>":
            return None
        consumed = True
        index += 1
    return index if consumed and depth == 0 else None


def _reference_title_end(text: str, start: int) -> int | None:
    """Return the end of one complete CommonMark reference title."""
    opener = text[start] if start < len(text) else ""
    closer = {'"': '"', "'": "'", "(": ")"}.get(opener)
    if closer is None:
        return None
    index = start + 1
    line_endings = 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            index += 2
            continue
        if opener == "(" and character == "(":
            return None
        if character == closer:
            return index + 1
        if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            line_endings += 1
            if line_endings > 1:
                return None
            index += 2
            continue
        if character == "\n":
            line_endings += 1
            if line_endings > 1:
                return None
        index += 1
    return None


def _reference_definition_at(text: str, start: int) -> ReferenceDefinition | None:
    """Parse the complete valid definition beginning at ``start``, if any."""
    label_end = _balanced_bracket_end(text, start)
    if label_end is None or label_end + 1 >= len(text) or text[label_end + 1] != ":":
        return None
    label = text[start + 1 : label_end]
    if not _valid_reference_label(label):
        return None
    destination_start, destination_breaks = _skip_reference_whitespace(text, label_end + 2)
    if destination_breaks > 1:
        return None
    destination_end = _reference_destination_end(text, destination_start)
    if destination_end is None:
        return None
    definition = ReferenceDefinition(_normalize_reference_label(label), start, destination_end)
    trailing_start, trailing_breaks = _skip_reference_whitespace(text, destination_end)
    if trailing_start == destination_end or trailing_breaks > 1:
        return definition
    if trailing_breaks:
        line_start = text.rfind("\n", destination_end, trailing_start) + 1
        indentation = text[line_start:trailing_start]
        if (
            not 1 <= len(indentation) <= 3
            or set(indentation) != {" "}
            or trailing_start >= len(text)
            or text[trailing_start] not in {'"', "'", "("}
        ):
            return definition
    if trailing_start >= len(text) or text[trailing_start] in "\r\n":
        return definition
    title_end = _reference_title_end(text, trailing_start)
    if title_end is None:
        return definition if trailing_breaks else None
    line_end = text.find("\n", title_end)
    line_end = len(text) if line_end < 0 else line_end
    if text[title_end:line_end].strip(" \t\r"):
        return definition if trailing_breaks else None
    return ReferenceDefinition(_normalize_reference_label(label), start, title_end)


def _inline_states_after_line(
    line: str,
    text: str,
    line_offset: int,
    active_code_ticks: int | None,
    active_link_end: int | None,
) -> tuple[int | None, int | None]:
    """Advance shared inline-code/link state without transforming content."""
    index = 0
    while index < len(line):
        if active_code_ticks is not None:
            closing = _matching_backtick_run(line, index, active_code_ticks)
            if closing is None:
                return active_code_ticks, active_link_end
            index = closing[1]
            active_code_ticks = None
            continue
        if active_link_end is not None:
            index = min(len(line), active_link_end - line_offset)
            if line_offset + index < active_link_end:
                return active_code_ticks, active_link_end
            active_link_end = None
            continue
        if line[index] == "`":
            delimiter_end = _backtick_run_end(line, index)
            delimiter_length = delimiter_end - index
            closing = _matching_backtick_run(line, delimiter_end, delimiter_length)
            if closing is None:
                return delimiter_length, active_link_end
            index = closing[1]
            continue
        if line[index] == "[":
            end = _inline_link_end(text, line_offset + index)
            if end is not None:
                index = min(len(line), end - line_offset)
                if line_offset + index < end:
                    return active_code_ticks, end
                continue
        index += 1
    return active_code_ticks, active_link_end


def _reference_definition_info(text: str) -> tuple[set[str], list[ReferenceDefinition]]:
    """Collect normalized labels and their complete protected definition spans."""
    labels: set[str] = set()
    definitions: list[ReferenceDefinition] = []
    offset = 0
    active_fence: tuple[str, int] | None = None
    active_code_ticks: int | None = None
    active_link_end: int | None = None
    active_indented_code = False
    previous_blank = True
    for line in text.splitlines(keepends=True):
        if definitions and offset < definitions[-1].end:
            previous_blank = _is_blank_line(line)
            offset += len(line)
            continue
        protected_at_line_start = active_code_ticks is not None or active_link_end is not None
        if protected_at_line_start:
            active_code_ticks, active_link_end = _inline_states_after_line(
                line, text, offset, active_code_ticks, active_link_end
            )
            previous_blank = _is_blank_line(line)
            offset += len(line)
            continue
        blank = _is_blank_line(line)
        indented = _is_indented_code_line(line)
        if active_indented_code:
            if blank or indented:
                previous_blank = blank
                offset += len(line)
                continue
            active_indented_code = False
        if active_fence is None and previous_blank and indented:
            active_indented_code = True
            previous_blank = False
            offset += len(line)
            continue
        marker = _fence_marker(line)
        if active_fence is not None:
            if (
                marker is not None
                and marker[0] == active_fence[0]
                and marker[1] >= active_fence[1]
                and not marker[2].strip()
            ):
                active_fence = None
        elif marker is not None:
            active_fence = marker[0], marker[1]
        else:
            indent = len(line) - len(line.lstrip(" "))
            if indent <= 3 and indent < len(line) and line[indent] == "[":
                start = offset + indent
                definition = _reference_definition_at(text, start)
                if definition is not None:
                    labels.add(definition.label)
                    definitions.append(definition)
            active_code_ticks, active_link_end = _inline_states_after_line(
                line, text, offset, active_code_ticks, active_link_end
            )
        previous_blank = blank
        offset += len(line)
    return labels, definitions


def _backtick_run_end(text: str, start: int) -> int:
    end = start + 1
    while end < len(text) and text[end] == "`":
        end += 1
    return end


def _matching_backtick_run(text: str, start: int, length: int) -> tuple[int, int] | None:
    """Find a closing backtick run of exactly the opener's length."""
    index = start
    while index < len(text):
        candidate = text.find("`", index)
        if candidate < 0:
            return None
        end = _backtick_run_end(text, candidate)
        if end - candidate == length:
            return candidate, end
        index = end
    return None


def _resolve_obs_reference(target: str, root: Path, references: list[NoteReference]) -> NoteMatch:
    """Use the standard exact-ID, alias, and short-project-alias resolver."""
    return resolve_id_from_references(target, root, references)


def _definition_end_at(definitions: list[ReferenceDefinition], position: int) -> int | None:
    """Return the protected definition endpoint covering ``position``, if any."""
    for definition in definitions:
        if definition.start <= position < definition.end:
            return definition.end
        if definition.start > position:
            break
    return None


def _line_uses_table_pipes(
    line: str,
    active_code_ticks: int | None = None,
    *,
    text: str | None = None,
    line_offset: int = 0,
    active_link_end: int | None = None,
    reference_labels: set[str] | None = None,
    definitions: list[ReferenceDefinition] | None = None,
) -> bool:
    """Return whether a line has a pipe outside protected Markdown spans."""
    index = 0
    while index < len(line):
        if definitions is not None and text is not None:
            definition_end = _definition_end_at(definitions, line_offset + index)
            if definition_end is not None:
                index = min(len(line), definition_end - line_offset)
                continue
        if active_code_ticks is not None:
            closing = _matching_backtick_run(line, index, active_code_ticks)
            if closing is None:
                return False
            index = closing[1]
            active_code_ticks = None
            continue
        if active_link_end is not None:
            index = min(len(line), active_link_end - line_offset)
            if line_offset + index < active_link_end:
                return False
            active_link_end = None
            continue
        if line.startswith("[[", index):
            end = line.find("]]", index + 2)
            if end < 0:
                return False
            index = end + 2
            continue
        if line[index] == "`":
            delimiter_end = _backtick_run_end(line, index)
            delimiter_length = delimiter_end - index
            closing = _matching_backtick_run(line, delimiter_end, delimiter_length)
            if closing is None:
                return False
            index = closing[1]
            continue
        if line[index] == "[":
            source = text if text is not None else line
            start = line_offset + index if text is not None else index
            end = _inline_link_end(source, start, reference_labels)
            if end is not None:
                index = min(len(line), end - line_offset if text is not None else end)
                if text is not None and line_offset + index < end:
                    return False
                continue
        if line[index] == "<":
            end = line.find(">", index + 1)
            if end >= 0:
                index = end + 1
                continue
        if line[index] == "|" and not _is_escaped(line, index):
            return True
        index += 1
    return False


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """Return a CommonMark-style fence character, length, and trailing text."""
    match = FENCE_RE.match(line)
    if not match:
        return None
    fence = match.group(1)
    trailing = match.group(2)
    if fence[0] == "`" and "`" in trailing:
        return None
    return fence[0], len(fence), trailing


def _is_blank_line(line: str) -> bool:
    return not line.strip(" \t\r\n")


def _is_indented_code_line(line: str) -> bool:
    """Return whether a line has the indentation required for a code block."""
    return line.startswith("\t") or line.startswith("    ")


def _materialize_line(
    line: str,
    text: str,
    line_offset: int,
    root: Path,
    references: list[NoteReference],
    resolved: dict[str, NoteMatch],
    replacements: list[ObsReferenceReplacement],
    active_code_ticks: int | None = None,
    active_link_end: int | None = None,
    reference_labels: set[str] | None = None,
    definitions: list[ReferenceDefinition] | None = None,
) -> tuple[str, int | None, int | None]:
    """Materialize eligible references in one non-fenced Markdown line."""
    line_uses_table_pipes = _line_uses_table_pipes(
        line,
        active_code_ticks,
        text=text,
        line_offset=line_offset,
        active_link_end=active_link_end,
        reference_labels=reference_labels,
        definitions=definitions,
    )
    rendered: list[str] = []
    index = 0
    while index < len(line):
        if definitions is not None:
            definition_end = _definition_end_at(definitions, line_offset + index)
            if definition_end is not None:
                local_end = min(len(line), definition_end - line_offset)
                rendered.append(line[index:local_end])
                index = local_end
                continue
        if active_code_ticks is not None:
            closing = _matching_backtick_run(line, index, active_code_ticks)
            if closing is None:
                rendered.append(line[index:])
                return "".join(rendered), active_code_ticks, active_link_end
            rendered.append(line[index : closing[1]])
            index = closing[1]
            active_code_ticks = None
            continue
        if active_link_end is not None:
            local_end = min(len(line), active_link_end - line_offset)
            rendered.append(line[index:local_end])
            index = local_end
            if line_offset + index < active_link_end:
                return "".join(rendered), active_code_ticks, active_link_end
            active_link_end = None
            continue
        if line.startswith("[[", index):
            end = line.find("]]", index + 2)
            if end < 0:
                rendered.append(line[index:])
                break
            end += 2
            rendered.append(line[index:end])
            index = end
            continue
        if line[index] == "<":
            end = line.find(">", index + 1)
            if end >= 0:
                end += 1
                rendered.append(line[index:end])
                index = end
                continue
        if line[index] == "`":
            delimiter_end = _backtick_run_end(line, index)
            delimiter_length = delimiter_end - index
            closing = _matching_backtick_run(line, delimiter_end, delimiter_length)
            if closing is None:
                rendered.append(line[index:])
                return "".join(rendered), delimiter_length, active_link_end
            rendered.append(line[index : closing[1]])
            index = closing[1]
            continue
        if line[index] == "[":
            end = _inline_link_end(text, line_offset + index, reference_labels)
            if end is not None:
                local_end = min(len(line), end - line_offset)
                rendered.append(line[index:local_end])
                index = local_end
                if line_offset + index < end:
                    return "".join(rendered), active_code_ticks, end
                continue
        starts_reference = line.startswith("obs:", index)
        match = OBS_REFERENCE_RE.match(line, index) if starts_reference else None
        before = line[index - 1] if index else ""
        eligible_start = not before or not (before.isalnum() or before in "_./\\:-")
        if starts_reference and eligible_start and not _is_escaped(line, index):
            if match is None:
                raise OawError("malformed obs reference: expected obs:<ID>")
            after = line[match.end() :]
            if after[:1] in {"/", "\\", "#", "^"} or after[:3].lower() == ".md":
                rendered.append(line[index : match.end()])
                index = match.end()
                continue
            target = match.group(1)
            reference = match.group(0)
            if target not in resolved:
                resolved[target] = _resolve_obs_reference(target, root, references)
            resolved_match = resolved[target]
            link = durable_wikilink(resolved_match, target)
            if line_uses_table_pipes:
                link = link.replace("|", r"\|", 1)
            rendered.append(link)
            replacements.append(ObsReferenceReplacement(reference, link))
            index = match.end()
            continue
        rendered.append(line[index])
        index += 1
    return "".join(rendered), active_code_ticks, active_link_end


def materialize_obs_references(
    text: str, root: Path, references: list[NoteReference] | None = None
) -> tuple[str, list[ObsReferenceReplacement]]:
    """Replace resolvable ``obs:ID`` prose with durable wikilinks.

    Existing wiki/Markdown links, code spans, fenced code, and escaped literals
    are intentionally left untouched. Missing, ambiguous, or malformed eligible
    references raise before callers perform any write.
    """
    references = scan_note_references(root) if references is None else references
    rendered: list[str] = []
    replacements: list[ObsReferenceReplacement] = []
    resolved: dict[str, NoteMatch] = {}
    reference_labels, definitions = _reference_definition_info(text)
    active_fence: tuple[str, int] | None = None
    active_code_ticks: int | None = None
    active_link_end: int | None = None
    active_indented_code = False
    previous_blank = True
    line_offset = 0
    for line in text.splitlines(keepends=True):
        if active_code_ticks is not None or active_link_end is not None:
            materialized, active_code_ticks, active_link_end = _materialize_line(
                line,
                text,
                line_offset,
                root,
                references,
                resolved,
                replacements,
                active_code_ticks,
                active_link_end,
                reference_labels,
                definitions,
            )
            rendered.append(materialized)
            previous_blank = _is_blank_line(line)
            line_offset += len(line)
            continue
        if active_indented_code:
            if _is_blank_line(line) or _is_indented_code_line(line):
                rendered.append(line)
                previous_blank = _is_blank_line(line)
                line_offset += len(line)
                continue
            active_indented_code = False
        if (
            active_code_ticks is None
            and active_fence is None
            and previous_blank
            and _is_indented_code_line(line)
        ):
            rendered.append(line)
            active_indented_code = True
            previous_blank = False
            line_offset += len(line)
            continue
        marker = _fence_marker(line)
        if active_fence is None and marker is not None:
            character, length, _ = marker
            active_fence = character, length
            rendered.append(line)
        elif active_fence is not None:
            if (
                marker is not None
                and marker[0] == active_fence[0]
                and marker[1] >= active_fence[1]
                and not marker[2].strip()
            ):
                active_fence = None
            rendered.append(line)
        else:
            materialized, active_code_ticks, active_link_end = _materialize_line(
                line,
                text,
                line_offset,
                root,
                references,
                resolved,
                replacements,
                reference_labels=reference_labels,
                definitions=definitions,
            )
            rendered.append(materialized)
        previous_blank = _is_blank_line(line)
        line_offset += len(line)
    return "".join(rendered), replacements


def split_wikilink_inner(inner: str) -> tuple[str, str | None]:
    for idx, char in enumerate(inner):
        if char == "|":
            return inner[:idx], inner[idx + 1 :]
        if char == "\\" and idx + 1 < len(inner) and inner[idx + 1] == "|":
            return inner[:idx], inner[idx + 2 :]
    return inner, None


def parse_wikilinks(text: str) -> list[WikiLink]:
    links: list[WikiLink] = []
    active_fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        delimiter = fence_delimiter(line)
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif fence_closes(active_fence, line):
                active_fence = None
            offset += len(line)
            continue
        if active_fence is None:
            for match in re.finditer(r"!?\[\[([^\]]+)\]\]", line):
                target, alias = split_wikilink_inner(match.group(1))
                links.append(
                    WikiLink(
                        raw=match.group(0),
                        target=target.strip().replace("\\|", "|"),
                        alias=alias.strip().replace("\\|", "|") if alias is not None else None,
                        start=offset + match.start(),
                        line=line.rstrip("\r\n"),
                    )
                )
        offset += len(line)
    return links


def normalize_link_target(target: str) -> str:
    clean = target.strip()
    clean = re.split(r"[#^]", clean, maxsplit=1)[0]
    clean = clean.removesuffix(".md")
    return clean.strip("/")


def link_matches_note(link: WikiLink, note: NoteMatch, include_id: bool = True) -> bool:
    target = normalize_link_target(link.target)
    durable = durable_link_target(note)
    candidates = {durable, note.relpath.removesuffix(".md")}
    if include_id and note.note_id:
        candidates.add(note.note_id)
    return target in candidates


def note_has_link_to(source: NoteMatch, target: NoteMatch, include_id: bool = True) -> bool:
    text = source.path.read_text(encoding="utf-8")
    return any(link_matches_note(link, target, include_id) for link in parse_wikilinks(text))


def append_to_section(text: str, section: str, line: str) -> str:
    return append_markdown_block_to_section(text, section, f"- {line}")


def ensure_link(
    source: NoteMatch,
    target: NoteMatch,
    section: str,
    label: str | None,
    write: bool,
) -> bool:
    if note_has_link_to(source, target, include_id=False):
        return False
    link = durable_wikilink(target, label)
    text = source.path.read_text(encoding="utf-8")
    updated = append_to_section(text, section, link)
    print(f"Source: {source.relpath}")
    print(f"Target: {target.relpath}")
    print(f"Action: append {link} to ## {section}")
    if write:
        source.path.write_text(updated, encoding="utf-8")
        print(f"Updated: {source.relpath}")
    else:
        print(f"Dry-run: would update {source.relpath}")
    return True


def link_check(root: Path, left_value: str, right_value: str) -> None:
    left = resolve_note_arg(left_value, root)
    right = resolve_note_arg(right_value, root)
    print(f"Left: {left.relpath} | id: {left.note_id or '(none)'}")
    print(f"Right: {right.relpath} | id: {right.note_id or '(none)'}")
    print(f"Left links right: {'yes' if note_has_link_to(left, right) else 'no'}")
    print(f"Right links left: {'yes' if note_has_link_to(right, left) else 'no'}")


def link_list(root: Path, note_value: str) -> None:
    source = resolve_note_arg(note_value, root)
    print(f"Links: {source.relpath}")
    text = source.path.read_text(encoding="utf-8")
    for link in parse_wikilinks(text):
        target = normalize_link_target(link.target)
        try:
            resolved = resolve_note_arg(target, root)
            suffix = f"{resolved.relpath} | id: {resolved.note_id or '(none)'}"
        except OawError:
            suffix = "unresolved"
        alias = f" | alias: {link.alias}" if link.alias else ""
        print(f"- {link.raw} -> {suffix}{alias}")


def link_ensure(
    root: Path, source_value: str, target_value: str, section: str, label: str | None, write: bool
) -> None:
    source = resolve_note_arg(source_value, root)
    target = resolve_note_arg(target_value, root)
    changed = ensure_link(source, target, section, label, write)
    if not changed:
        print(f"Source: {source.relpath}")
        print(f"Target: {target.relpath}")
        print("Link: present")


def link_ensure_bidirectional(
    root: Path, left_value: str, right_value: str, section: str, write: bool
) -> None:
    left = resolve_note_arg(left_value, root)
    right = resolve_note_arg(right_value, root)
    changed_left = ensure_link(left, right, section, right.note_id, write)
    changed_right = ensure_link(right, left, section, left.note_id, write)
    if not changed_left and not changed_right:
        print("Links: present")


def link_materialize(root: Path, source_value: str, write: bool) -> None:
    """Preview or commit safe obs-reference materialization for one note."""
    source = resolve_note_arg(source_value, root)
    try:
        original = source.path.read_bytes()
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OawError(f"note is not valid UTF-8: {source.relpath}") from exc
    before, _, body = split_note(text)
    if text.startswith("---") and not before:
        raise OawError(f"note has unclosed frontmatter: {source.relpath}")
    updated_body, replacements = materialize_obs_references(body, root)
    updated = before + updated_body
    print(f"Source: {source.relpath}")
    if not replacements:
        print("References: none")
        return
    for replacement in replacements:
        print(f"- {replacement.reference} -> {replacement.link}")
    if write:
        transaction = VaultTransaction()
        transaction.stage(source.path, updated, expected=original)
        transaction.commit()
        print(f"Updated: {source.relpath}")
    else:
        print(f"Dry-run: would update {source.relpath}")


OPAQUE_LINK_RE = re.compile(r"^(?:OAW|AGT|SR|CDX|FAB|PMX)-[A-Za-z0-9][A-Za-z0-9-]*$")


def link_lint(root: Path) -> None:
    found = False
    for path in iter_markdown(root):
        try:
            source = note_from_path(path, root)
            text = path.read_text(encoding="utf-8")
        except (OawError, UnicodeDecodeError):
            continue
        for link in parse_wikilinks(text):
            target = normalize_link_target(link.target)
            if not OPAQUE_LINK_RE.fullmatch(target):
                continue
            found = True
            try:
                resolved = resolve_id(target, root)
                suggestion = durable_wikilink(resolved, target)
            except OawError:
                suggestion = "(unresolved)"
            print(f"{source.relpath}: {link.raw} -> {suggestion}")
    if not found:
        print("No opaque ID wikilinks found.")
