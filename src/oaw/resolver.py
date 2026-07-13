"""Vault traversal and frontmatter-based note resolution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import frontmatter_may_match, parse_frontmatter, read_frontmatter_text
from .notes import split_note


@dataclass(frozen=True)
class NoteMatch:
    path: Path
    relpath: str
    note_id: str | None
    matched_by: str
    title: str
    frontmatter_text: str
    frontmatter: dict[str, object]


def strip_obs_prefix(raw_id: str) -> str:
    value = raw_id.strip()
    if value.startswith("obs:"):
        value = value[4:]
    if not value:
        raise OawError("empty ID")
    return value


def title_from_body(path: Path, body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def iter_markdown(root: Path):
    skip = {".git", ".obsidian", ".trash", "node_modules", ".venv", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [directory for directory in dirnames if directory not in skip]
        directory = Path(dirpath)
        for filename in filenames:
            if filename.endswith(".md"):
                yield directory / filename


def match_frontmatter(
    path: Path, root: Path, frontmatter: str, body: str, target: str
) -> NoteMatch | None:
    data = parse_frontmatter(frontmatter)
    note_id = data.get("id")
    aliases = data.get("aliases", [])
    if isinstance(note_id, str) and note_id == target:
        matched_by = "id"
    elif isinstance(aliases, list) and target in aliases:
        matched_by = "aliases"
    else:
        return None
    return NoteMatch(
        path=path,
        relpath=path.relative_to(root).as_posix(),
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by=matched_by,
        title=title_from_body(path, body),
        frontmatter_text=frontmatter.rstrip(),
        frontmatter=data,
    )


def note_match_unoptimized(path: Path, root: Path, target: str) -> NoteMatch | None:
    """Original extraction baseline: parse every complete note before matching."""
    try:
        _, frontmatter, body = split_note(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None
    return match_frontmatter(path, root, frontmatter, body, target)


def note_match_raw_prefilter(path: Path, root: Path, target: str) -> NoteMatch | None:
    """Avoid parsing unrelated notes, while retaining the original full-file reads."""
    try:
        _, frontmatter, body = split_note(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None
    if not frontmatter_may_match(frontmatter, target):
        return None
    return match_frontmatter(path, root, frontmatter, body, target)


def note_match(path: Path, root: Path, target: str) -> NoteMatch | None:
    """Use frontmatter-only reads before parsing and read a body only for a match."""
    try:
        frontmatter = read_frontmatter_text(path)
        if not frontmatter_may_match(frontmatter, target):
            return None
        data = parse_frontmatter(frontmatter)
        note_id = data.get("id")
        aliases = data.get("aliases", [])
        if isinstance(note_id, str) and note_id == target:
            matched_by = "id"
        elif isinstance(aliases, list) and target in aliases:
            matched_by = "aliases"
        else:
            return None
        _, _, body = split_note(path.read_text(encoding="utf-8"))
    except (OawError, UnicodeDecodeError):
        return None
    return NoteMatch(
        path=path,
        relpath=path.relative_to(root).as_posix(),
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by=matched_by,
        title=title_from_body(path, body),
        frontmatter_text=frontmatter.rstrip(),
        frontmatter=data,
    )


def resolve_with_matcher(raw_id: str, root: Path, matcher) -> NoteMatch:
    target = strip_obs_prefix(raw_id)
    matches = [match for path in iter_markdown(root) if (match := matcher(path, root, target))]
    if not matches:
        matches = project_alias_matches(target, root, matcher)
    if not matches:
        raise OawError(f"no note with frontmatter id or alias '{target}' under {root}")
    if len(matches) > 1:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in matches)
        raise OawError(f"id '{target}' is not unique:\n{paths}")
    return matches[0]


def resolve_id(raw_id: str, root: Path) -> NoteMatch:
    return resolve_with_matcher(raw_id, root, note_match)


def resolve_id_unoptimized(raw_id: str, root: Path) -> NoteMatch:
    return resolve_with_matcher(raw_id, root, note_match_unoptimized)


def resolve_id_raw_prefilter(raw_id: str, root: Path) -> NoteMatch:
    return resolve_with_matcher(raw_id, root, note_match_raw_prefilter)


def project_alias_matches(target: str, root: Path, matcher=note_match) -> list[NoteMatch]:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", target):
        return []
    index_id = f"{target}-index"
    projects = root / "Projects"
    if not projects.exists():
        return []
    matches: list[NoteMatch] = []
    for path in sorted(projects.glob("*/Index.md")):
        match = matcher(path, root, index_id)
        if match:
            matches.append(
                NoteMatch(
                    path=match.path,
                    relpath=match.relpath,
                    note_id=match.note_id,
                    matched_by="project-alias",
                    title=match.title,
                    frontmatter_text=match.frontmatter_text,
                    frontmatter=match.frontmatter,
                )
            )
    return matches
