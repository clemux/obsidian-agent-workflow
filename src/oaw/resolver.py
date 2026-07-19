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


def vault_root() -> Path:
    configured = os.environ.get("OAW_VAULT", "").strip()
    if not configured:
        raise OawError("OAW_VAULT is required; set it to the Obsidian vault path")
    return Path(configured).expanduser().resolve()


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


def notes_containing_literal(root: Path, literal: str) -> list[NoteMatch]:
    """Return resolved note metadata for notes containing a literal string."""
    matches: list[NoteMatch] = []
    for path in iter_markdown(root):
        try:
            text, frontmatter, body = read_note(path)
        except UnicodeDecodeError:
            continue
        if literal not in text:
            continue
        data = parse_frontmatter(frontmatter)
        note_id = data.get("id")
        matches.append(
            NoteMatch(
                path=path,
                relpath=path.relative_to(root).as_posix(),
                note_id=note_id if isinstance(note_id, str) else None,
                matched_by="content",
                title=title_from_body(path, body),
                frontmatter_text=frontmatter.rstrip(),
                frontmatter=data,
            )
        )
    return sorted(matches, key=lambda item: item.relpath)


def note_type_matches(data: dict[str, object], note_type: str) -> bool:
    value = data.get("type")
    return isinstance(value, str) and value == note_type


def note_status(data: dict[str, object]) -> str:
    value = data.get("status", "")
    return str(value) if value is not None else ""


# Fields the list command can project as tab-separated or JSON columns.
LIST_PROJECTABLE_FIELDS = (
    "id",
    "status",
    "title",
    "path",
    "goal",
    "priority",
    "effort",
    "preparedness",
    "type",
    "project",
    "created",
    "execution",
)
DEFAULT_LIST_FIELDS = ("id", "status", "title", "path")
LIST_SORT_KEYS = ("priority", "effort", "title")

# The goal snippet is sourced from the note's first `## Problem` content line.
GOAL_SECTION = "Problem"
GOAL_MAX_CHARS = 120

# Vault-wide 1/2/3 priority and S/M/L effort ranks; missing values sort last.
PRIORITY_RANK = {"1": 0, "2": 1, "3": 2}
EFFORT_RANK = {"S": 0, "M": 1, "L": 2}
_MISSING_RANK = 99


@dataclass(frozen=True)
class ProjectNoteRecord:
    note_id: str
    status: str
    title: str
    relpath: str
    goal: str
    frontmatter: dict[str, object]


def _goal_snippet(line: str) -> str:
    """Collapse whitespace and truncate a `## Problem` line into one column."""
    text = " ".join(line.split())
    if len(text) > GOAL_MAX_CHARS:
        text = text[:GOAL_MAX_CHARS].rstrip() + "…"
    return text


def _scan_title_and_goal(handle, default_title: str, want_goal: bool) -> tuple[str, str]:
    """Read the note body once for its H1 title and optional goal snippet."""
    title = default_title
    title_found = False
    goal = ""
    in_goal_section = False
    for line in handle:
        stripped = line.rstrip("\n")
        if stripped.startswith("## "):
            if want_goal and not goal:
                in_goal_section = stripped[3:].strip() == GOAL_SECTION
            continue
        if not title_found and stripped.startswith("# "):
            title = stripped[2:].strip()
            title_found = True
            if not want_goal:
                break
            continue
        if want_goal and in_goal_section and not goal and stripped.strip():
            goal = _goal_snippet(stripped)
            in_goal_section = False
        if title_found and (not want_goal or goal):
            break
    return title, goal


def read_project_note_record(
    path: Path,
    root: Path,
    note_type: str,
    status: str | None,
    include_archived: bool,
    want_goal: bool,
) -> ProjectNoteRecord | None:
    with path.open("r", encoding="utf-8") as handle:
        if handle.readline().strip() != "---":
            return None
        lines: list[str] = []
        for line in handle:
            if line.strip() == "---":
                break
            lines.append(line)
        else:
            return None
        data = parse_frontmatter("".join(lines))
        if not note_type_matches(data, note_type):
            return None
        current_status = note_status(data)
        if status and current_status != status:
            return None
        if not status and current_status == "archived" and not include_archived:
            return None
        title, goal = _scan_title_and_goal(handle, path.stem, want_goal)
    return ProjectNoteRecord(
        note_id=str(data.get("id", "")),
        status=current_status,
        title=title,
        relpath=path.relative_to(root).as_posix(),
        goal=goal,
        frontmatter=data,
    )


def project_note_records(
    project_root: Path,
    root: Path,
    note_type: str,
    status: str | None,
    include_archived: bool,
    want_goal: bool,
) -> list[ProjectNoteRecord]:
    records: list[ProjectNoteRecord] = []
    for path in sorted(project_root.rglob("*.md"), key=os.fspath):
        record = read_project_note_record(
            path, root, note_type, status, include_archived, want_goal
        )
        if record is not None:
            records.append(record)
    return records


def _frontmatter_str(record: ProjectNoteRecord, field: str) -> str:
    value = record.frontmatter.get(field)
    return "" if value is None else str(value).strip()


def _priority_rank(record: ProjectNoteRecord) -> int:
    return PRIORITY_RANK.get(_frontmatter_str(record, "priority"), _MISSING_RANK)


def _effort_rank(record: ProjectNoteRecord) -> int:
    return EFFORT_RANK.get(_frontmatter_str(record, "effort"), _MISSING_RANK)


_SORT_KEYS = {
    "priority": lambda r: (_priority_rank(r), _effort_rank(r), r.title.lower()),
    "effort": lambda r: (_effort_rank(r), _priority_rank(r), r.title.lower()),
    "title": lambda r: (r.title.lower(),),
}


def _record_field(record: ProjectNoteRecord, field: str) -> str:
    if field == "id":
        return record.note_id
    if field == "status":
        return record.status
    if field == "title":
        return record.title
    if field == "path":
        return record.relpath
    if field == "goal":
        return record.goal
    return _frontmatter_str(record, field)


def resolve_list_fields(fields: str | None, goal: bool) -> list[str]:
    """Turn the --fields / --goal request into an ordered, validated column list."""
    if fields is None:
        columns = list(DEFAULT_LIST_FIELDS)
    else:
        columns = []
        for raw in fields.split(","):
            name = raw.strip()
            if not name:
                continue
            if name not in LIST_PROJECTABLE_FIELDS:
                allowed = ", ".join(LIST_PROJECTABLE_FIELDS)
                raise OawError(f"unknown list field: {name} (choose from {allowed})")
            if name not in columns:
                columns.append(name)
        if not columns:
            raise OawError("--fields requires at least one field name")
    if goal and "goal" not in columns:
        columns.append("goal")
    return columns


def list_project(
    root: Path,
    project: str,
    note_type: str,
    status: str | None,
    include_archived: bool,
    *,
    sort: str | None = None,
    fields: str | None = None,
    goal: bool = False,
    json_output: bool = False,
) -> None:
    """List project notes for the stable list subcommand."""
    columns = resolve_list_fields(fields, goal)
    want_goal = "goal" in columns
    project_root, _ = resolve_project_root(project, root)
    if note_type == "task":
        tasks = project_root / "Tasks"
        if not tasks.exists():
            raise OawError(f"project tasks folder not found: {tasks}")
        records = project_note_records(tasks, root, note_type, status, True, want_goal)
    else:
        records = project_note_records(
            project_root, root, note_type, status, include_archived, want_goal
        )
    if sort is not None:
        records.sort(key=_SORT_KEYS[sort])
    if json_output:
        payload = [{field: _record_field(record, field) for field in columns} for record in records]
        print(json.dumps(payload, ensure_ascii=False))
        return
    for record in records:
        print("\t".join(_record_field(record, field) for field in columns))
