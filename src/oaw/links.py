"""Durable wikilink parsing and commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import parse_frontmatter
from .notes import append_markdown_block_to_section, fence_closes, fence_delimiter, read_note
from .resolver import NoteMatch, iter_markdown, resolve_id, strip_obs_prefix, title_from_body


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    start: int
    line: str


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
