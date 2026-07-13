"""Vault traversal and frontmatter-based note resolution."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import (
    frontmatter_may_match,
    parse_frontmatter,
    read_frontmatter_only,
    read_frontmatter_text,
)
from .notes import read_note, split_note

DEFAULT_VAULT = Path("/path/to/vault")


def vault_root() -> Path:
    return Path(os.environ.get("OAW_VAULT", DEFAULT_VAULT)).expanduser().resolve()


@dataclass(frozen=True)
class NoteMatch:
    path: Path
    relpath: str
    note_id: str | None
    matched_by: str
    title: str
    frontmatter_text: str
    frontmatter: dict[str, object]


@dataclass(frozen=True)
class NoteReference:
    """Raw frontmatter collected during one vault walk for deferred matching."""

    path: Path
    relpath: str
    frontmatter_text: str


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
        frontmatter = read_frontmatter_text(path, max_bytes=None, require_closed=False)
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
    except UnicodeDecodeError:
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


def note_reference(path: Path, root: Path) -> NoteReference | None:
    """Return raw frontmatter for a note without parsing it or reading its body."""
    try:
        frontmatter = read_frontmatter_text(path, max_bytes=None, require_closed=False)
    except UnicodeDecodeError:
        return None
    if not frontmatter:
        return None
    return NoteReference(
        path=path,
        relpath=path.relative_to(root).as_posix(),
        frontmatter_text=frontmatter.rstrip(),
    )


def scan_note_references(root: Path) -> list[NoteReference]:
    """Walk a vault once and cache raw frontmatter for deferred pre-filtered matching."""
    return [reference for path in iter_markdown(root) if (reference := note_reference(path, root))]


def note_match_from_reference(reference: NoteReference, target: str) -> NoteMatch | None:
    """Pre-filter and resolve one target, loading its body only on a parsed match."""
    if not frontmatter_may_match(reference.frontmatter_text, target):
        return None
    data = parse_frontmatter(reference.frontmatter_text)
    note_id = data.get("id")
    aliases = data.get("aliases", [])
    if isinstance(note_id, str) and note_id == target:
        matched_by = "id"
    elif isinstance(aliases, list) and target in aliases:
        matched_by = "aliases"
    else:
        return None
    try:
        _, _, body = split_note(reference.path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None
    return NoteMatch(
        path=reference.path,
        relpath=reference.relpath,
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by=matched_by,
        title=title_from_body(reference.path, body),
        frontmatter_text=reference.frontmatter_text,
        frontmatter=data,
    )


def matches_from_references(target: str, references: Sequence[NoteReference]) -> list[NoteMatch]:
    return [
        match
        for reference in references
        if (match := note_match_from_reference(reference, target)) is not None
    ]


def project_alias_matches_from_references(
    target: str, references: Sequence[NoteReference]
) -> list[NoteMatch]:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", target):
        return []
    index_id = f"{target}-index"
    matches: list[NoteMatch] = []
    for reference in sorted(references, key=lambda item: item.relpath):
        parts = Path(reference.relpath).parts
        if len(parts) != 3 or parts[0] != "Projects" or parts[2] != "Index.md":
            continue
        match = note_match_from_reference(reference, index_id)
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


def resolve_id_from_references(
    raw_id: str, root: Path, references: Sequence[NoteReference]
) -> NoteMatch:
    """Resolve an ID from metadata collected by one earlier vault walk."""
    target = strip_obs_prefix(raw_id)
    matches = matches_from_references(target, references)
    if not matches:
        matches = project_alias_matches_from_references(target, references)
    if not matches:
        raise OawError(f"no note with frontmatter id or alias '{target}' under {root}")
    if len(matches) > 1:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in matches)
        raise OawError(f"id '{target}' is not unique:\n{paths}")
    return matches[0]


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


def resolve_project_root(raw: str, root: Path) -> tuple[Path, str | None]:
    """Resolve a project alias or folder name to its folder and alias prefix."""
    target = strip_obs_prefix(raw.strip())
    if not target:
        raise OawError("task create requires a non-empty --project")
    matches = project_alias_matches(target, root)
    if len(matches) > 1:
        paths = "\n".join(f"  {match.relpath}" for match in matches)
        raise OawError(f"project alias '{target}' is ambiguous:\n{paths}")
    if matches:
        return matches[0].path.parent, target
    candidate = root / "Projects" / target
    if candidate.is_dir():
        prefix = None
        index = candidate / "Index.md"
        if index.exists():
            _, data = read_frontmatter_only(index)
            index_id = str(data.get("id") or "")
            if index_id.endswith("-index"):
                prefix = index_id.removesuffix("-index")
        return candidate, prefix
    raise OawError(f"project not found: {raw}")


def outline(path: Path) -> list[str]:
    _, _, body = read_note(path)
    lines: list[str] = []
    in_fence = False
    for number, line in enumerate(body.splitlines(), start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,6} ", line):
            lines.append(f"{number}: {line}")
    return lines


def output_resolve(
    match: NoteMatch,
    full: bool,
    path_only: bool,
    meta: bool,
    show_outline: bool,
    json_output: bool,
) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "id": match.note_id,
                    "path": str(match.path),
                    "relative_path": match.relpath,
                    "title": match.title,
                    "matched_by": match.matched_by,
                    "frontmatter": match.frontmatter,
                    "outline": outline(match.path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if path_only:
        print(match.path)
        return
    if meta:
        print(match.frontmatter_text)
        return
    if show_outline:
        print("\n".join(outline(match.path)))
        return
    if full:
        print(match.path.read_text(encoding="utf-8"), end="")
        return
    print(f"ID: {match.note_id}")
    print(f"Path: {match.path}")
    print(f"Title: {match.title}")
    print(f"Matched by: {match.matched_by}")
    print()
    print("Frontmatter:")
    print(match.frontmatter_text)
    print()
    print("Outline:")
    print("\n".join(outline(match.path)))


def resolve_project_root_from_references(
    raw: str, root: Path, references: Sequence[NoteReference]
) -> tuple[Path, str | None]:
    """Resolve a project without introducing another vault traversal."""
    target = strip_obs_prefix(raw.strip())
    if not target:
        raise OawError("task create requires a non-empty --project")
    matches = project_alias_matches_from_references(target, references)
    if len(matches) > 1:
        paths = "\n".join(f"  {match.relpath}" for match in matches)
        raise OawError(f"project alias '{target}' is ambiguous:\n{paths}")
    if matches:
        return matches[0].path.parent, target
    candidate = root / "Projects" / target
    if candidate.is_dir():
        prefix = None
        index = candidate / "Index.md"
        if index.exists():
            _, data = read_frontmatter_only(index)
            index_id = str(data.get("id") or "")
            if index_id.endswith("-index"):
                prefix = index_id.removesuffix("-index")
        return candidate, prefix
    raise OawError(f"project not found: {raw}")
