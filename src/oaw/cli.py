#!/usr/bin/env python3
"""Resolve Obsidian IDs and update project task lifecycle state."""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import (
    append_frontmatter_list_value,
    parse_frontmatter,
    set_frontmatter_scalar,
)
from .notes import read_note, split_note
from .runs import VaultTransaction

DEFAULT_VAULT = Path("/path/to/vault")
NEXT_BOARD = Path("Projects/Next steps.md")
RETRO_ATTACHMENTS = Path("Agents/Retrospectives/attachments")
BOARD_COLUMN_ORDER = ["Backlog", "Todo", "Active", "Done"]
SAFE_EXPORT_DESTINATION = Path("Imports/Safe export")
SAFE_EXPORT_QUARANTINE = Path(".rejected")
SAFE_EXPORT_TAG = "safe-export-personal"
SAFE_EXPORT_SCOPE = "personal"
RESEARCH_PACKET_TEMPLATE = Path("Templates/Research packet.md")
PROJECT_INDEX_TEMPLATE = Path("Templates/Small project index.md")
DEEP_RESEARCH_HEADING = "## Deep research prompt"
RUNNING_RESEARCH_HEADING = "## Running research sessions"
RESEARCH_PACKET_BASE = Path("Bases/Research packet.base")
FRONTMATTER_READ_LIMIT = 64 * 1024
DEFAULT_EXPORT_ROOT = Path("~/obsidian-export")
SESSION_ENV = [
    ("Codex", "CODEX_THREAD_ID"),
    ("Claude Code", "CLAUDE_SESSION_ID"),
    ("Claude Code", "CLAUDE_CODE_SESSION_ID"),
    ("OpenCode", "OPENCODE_SESSION_ID"),
    ("Gemini", "GEMINI_SESSION_ID"),
]


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
class SnapshotCopy:
    source: Path
    destination: Path
    category: str
    completeness: str = "complete"


@dataclass(frozen=True)
class ExportCandidate:
    source: Path
    relative_source: Path
    safe: bool
    marker: str
    reason: str
    destination: Path | None = None


@dataclass(frozen=True)
class SessionArtifact:
    kind: str
    path: Path


@dataclass(frozen=True)
class WikiLink:
    raw: str
    target: str
    alias: str | None
    start: int
    line: str


def vault_root() -> Path:
    return Path(os.environ.get("OAW_VAULT", DEFAULT_VAULT)).expanduser().resolve()


def safe_relative_path(raw: str, label: str) -> Path:
    path = Path(raw.strip())
    if not raw.strip() or path.is_absolute() or ".." in path.parts:
        raise OawError(f"{label} must be a non-empty vault-relative path without '..'")
    return path


def single_line_value(raw: str | None, label: str, *, required: bool = True) -> str | None:
    if raw is None:
        if required:
            raise OawError(f"project create requires {label}")
        return None
    value = raw.strip()
    if (required and not value) or "\n" in raw or "\r" in raw:
        qualifier = "non-empty, " if required else ""
        raise OawError(f"project create {label} must be a {qualifier}single-line value")
    return value or None


def safe_project_name(raw: str) -> str:
    name = single_line_value(raw, "--name")
    assert name is not None
    if (
        name != raw
        or name in {".", ".."}
        or name.startswith(".")
        or name.endswith((".", " "))
        or any(character in name for character in '/\\:*?"<>|')
        or any(unicodedata.category(character).startswith("C") for character in name)
        or Path(name).name != name
    ):
        raise OawError(
            "project create --name must be a safe one-segment folder name without "
            "surrounding whitespace, separators, traversal, or reserved characters"
        )
    return name


def safe_project_tag(raw: str) -> str:
    tag = single_line_value(raw, "--tag")
    assert tag is not None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]*", tag) or "//" in tag:
        raise OawError(
            "project create --tag must use letters, numbers, underscores, hyphens, or slashes"
        )
    return tag


def replace_h2_body(text: str, heading: str, body: str) -> str:
    matches = list(re.finditer(rf"(?m)^## {re.escape(heading)}[ \t]*$", text))
    if len(matches) != 1:
        raise OawError(f"project template must contain exactly one '## {heading}' heading")
    match = matches[0]
    next_heading = re.search(r"(?m)^#{1,2} \S.*$", text[match.end() :])
    end = match.end() + (next_heading.start() if next_heading else len(text[match.end() :]))
    return f"{text[: match.end()]}\n\n{body.strip()}\n\n{text[end:].lstrip()}"


def project_frontmatter(
    project: str,
    alias: str,
    repo: str | None,
    tags: list[str],
    session_ref: str,
    preserved: str,
) -> str:
    note_id = f"{alias}-index"
    lines = [
        "---",
        "type: project",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        "status: active",
    ]
    if repo is not None:
        lines.append(f"repo: {json.dumps(repo, ensure_ascii=False)}")
    lines += [
        f"id: {json.dumps(note_id, ensure_ascii=False)}",
        "aliases:",
        f"  - {json.dumps(note_id, ensure_ascii=False)}",
        "tags:",
    ]
    for tag in tags:
        lines.append(f"  - {json.dumps(tag, ensure_ascii=False)}")
    session_id = session_ref.partition("=")[2]
    if session_id and session_id != "unavailable":
        lines += ["session-ids:", f"  - {json.dumps(session_id, ensure_ascii=False)}"]
    if preserved.strip():
        lines.extend(preserved.strip().splitlines())
    return "\n".join([*lines, "---"]) + "\n"


def preserve_unmanaged_project_frontmatter(frontmatter: str) -> str:
    managed = {"type", "project", "status", "repo", "id", "aliases", "tags", "session-ids"}
    kept: list[str] = []
    skipping = False
    for line in frontmatter.splitlines():
        key = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if key:
            skipping = key.group(1) in managed
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def render_project_index(
    template_text: str,
    name: str,
    alias: str,
    goal: str,
    repo: str | None,
    tags: list[str],
    session_ref: str,
) -> str:
    h1_lines = re.findall(r"(?m)^# (.+?)\s*$", template_text)
    if len(h1_lines) != 1 or "{{title}}" not in h1_lines[0]:
        raise OawError("project template must contain exactly one H1 containing {{title}}")
    for heading in ("Goal", "Current state"):
        if len(re.findall(rf"(?m)^## {re.escape(heading)}[ \t]*$", template_text)) != 1:
            raise OawError(f"project template must contain exactly one '## {heading}' heading")

    rendered = template_text.replace("{{title}}", name).replace(
        "{{date}}", _dt.date.today().isoformat()
    )
    before, template_frontmatter, template_body = split_note(rendered)
    if not before:
        raise OawError("project template must contain closed YAML frontmatter")
    parse_frontmatter(template_frontmatter)
    current_state = ["- Status: active"]
    if repo is not None:
        current_state.append(f"- Repo: {repo}")
    current_state.append("- Next action: create or select the first task when work is selected.")
    template_body = replace_h2_body(template_body, "Goal", goal)
    template_body = replace_h2_body(template_body, "Current state", "\n".join(current_state))
    preserved = preserve_unmanaged_project_frontmatter(template_frontmatter)
    rendered = project_frontmatter(
        slugify(name), alias, repo, tags, session_ref, preserved
    ) + template_body.lstrip("\n")
    if re.search(r"{{[^{}\n]+}}", rendered):
        raise OawError("rendered project index contains unresolved template expressions")
    _, frontmatter, body = split_note(rendered)
    metadata = parse_frontmatter(frontmatter)
    note_id = f"{alias}-index"
    if (
        metadata.get("type") != "project"
        or metadata.get("status") != "active"
        or metadata.get("id") != note_id
        or metadata.get("aliases") != [note_id]
        or len(re.findall(r"(?m)^## Goal[ \t]*$", body)) != 1
        or len(re.findall(r"(?m)^## Current state[ \t]*$", body)) != 1
    ):
        raise OawError("rendered project index failed structural validation")
    return rendered.rstrip() + "\n"


def create_project(args: argparse.Namespace) -> None:
    root = vault_root()
    name = safe_project_name(args.name)
    goal = single_line_value(args.goal, "--goal")
    assert goal is not None
    alias = single_line_value(args.alias, "--alias")
    assert alias is not None
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", alias):
        raise OawError("project create --alias must match [A-Z][A-Z0-9]{1,7}")
    repo = single_line_value(args.repo, "--repo", required=False)
    extra_tags = [safe_project_tag(tag) for tag in args.tag or []]
    project_slug = slugify(name)
    tags = list(dict.fromkeys(["projects", project_slug, *extra_tags]))
    template_rel = safe_relative_path(args.template, "template")
    template_path = root / template_rel
    if not template_path.is_file():
        raise OawError(f"project template not found: {template_rel.as_posix()}")

    project_root = root / "Projects" / name
    destination = project_root / "Index.md"
    if project_root.exists():
        raise OawError(f"project folder already exists: Projects/{name}")
    note_id = f"{alias}-index"
    conflicts = [
        match
        for candidate in iter_markdown(root)
        if (match := note_match(candidate, root, note_id))
    ]
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    provider, session_ref = detect_session(args.allow_missing_session_id)
    del provider  # The project index records stable session provenance in frontmatter.
    template_text = template_path.read_text(encoding="utf-8")
    rendered = render_project_index(template_text, name, alias, goal, repo, tags, session_ref)

    transaction = VaultTransaction()
    transaction.stage(destination, rendered)
    try:
        transaction.commit()
    except OawError:
        if project_root.exists() and not any(project_root.iterdir()):
            project_root.rmdir()
        raise
    print(f"Created: {destination.relative_to(root).as_posix()}")
    print(f"ID: {note_id}")
    print("Status: active")


def create_research_packet(args: argparse.Namespace) -> None:
    root = vault_root()
    project_root, _ = resolve_project_root(args.project, root)
    track = safe_relative_path(args.track, "track")
    title = args.title.strip()
    if not title or "\n" in title or "\r" in title:
        raise OawError("research scaffold requires a non-empty, single-line --title")
    try:
        date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    except ValueError as exc:
        raise OawError("--date must use YYYY-MM-DD") from exc
    template_rel = safe_relative_path(args.template, "template")
    template_path = root / template_rel
    if not template_path.is_file():
        raise OawError(f"research packet template not found: {template_rel.as_posix()}")

    template_text = template_path.read_text(encoding="utf-8")
    boundary_matches = list(
        re.finditer(rf"(?m)^{re.escape(DEEP_RESEARCH_HEADING)}[ \t]*$", template_text)
    )
    if len(boundary_matches) != 1:
        raise OawError(
            f"research packet template must contain exactly one '{DEEP_RESEARCH_HEADING}' heading"
        )
    boundary = boundary_matches[0]
    provider_template = template_text[boundary.end() :]
    local_tokens = [token for token in ("{{project}}", "{{track}}") if token in provider_template]
    if local_tokens:
        raise OawError(
            "research packet template places local-only fields after "
            f"'{DEEP_RESEARCH_HEADING}': {', '.join(local_tokens)}"
        )
    rendered = template_text
    fields = {
        "{{project}}": slugify(project_root.name),
        "{{track}}": track.as_posix(),
        "{{title}}": title,
        "{{date}}": date.isoformat(),
    }
    for token, value in fields.items():
        if token not in rendered:
            raise OawError(f"research packet template is missing required field {token}")
        rendered = rendered.replace(token, value)
    rendered_boundary = re.search(rf"(?m)^{re.escape(DEEP_RESEARCH_HEADING)}[ \t]*$", rendered)
    if rendered_boundary is None:
        raise OawError(f"rendered research prompt is missing '{DEEP_RESEARCH_HEADING}' heading")
    rendered_provider = rendered[rendered_boundary.end() :]
    leaked_fields = [
        name
        for name in ("project", "track")
        if re.search(
            rf"(?<!\w){re.escape(fields[f'{{{{{name}}}}}'])}(?!\w)",
            rendered_provider,
        )
    ]
    if leaked_fields:
        raise OawError(
            "rendered research prompt places local-only metadata after "
            f"'{DEEP_RESEARCH_HEADING}': {', '.join(leaked_fields)}"
        )
    provider_prompt_from_text(rendered)
    packet_dir = project_root / "Research" / track
    destination = packet_dir / "Prompt.md"
    if destination.exists() and not args.force:
        raise OawError(
            f"research prompt already exists: {destination.relative_to(root).as_posix()}"
        )
    synthesis = packet_dir / "Synthesis.md"
    base = root / RESEARCH_PACKET_BASE
    transaction = VaultTransaction()
    transaction.stage(destination, rendered)
    if not synthesis.exists():
        transaction.stage(
            synthesis,
            research_synthesis_text(
                slugify(project_root.name), track.as_posix(), title, date.isoformat()
            ),
        )
    if not base.exists():
        transaction.stage(base, research_packet_base_text())
    transaction.commit()
    print(f"Created: {destination.relative_to(root).as_posix()}")
    print(f"Synthesis: {synthesis.relative_to(root).as_posix()}")
    print(f"Base: {RESEARCH_PACKET_BASE.as_posix()}")
    print(f"Template: {template_rel.as_posix()}")
    print("Deep research prompt: self-contained provider-visible body")


def provider_prompt_from_text(text: str) -> str:
    matches = list(re.finditer(rf"(?m)^{re.escape(DEEP_RESEARCH_HEADING)}[ \t]*$", text))
    if len(matches) != 1:
        raise OawError(
            f"research prompt must contain exactly one '{DEEP_RESEARCH_HEADING}' heading"
        )
    remainder = text[matches[0].end() :]
    block = re.fullmatch(r"\s*```text[ \t]*\n(.*?)\n```[ \t]*\s*", remainder, re.DOTALL)
    if block is None or not block.group(1).strip():
        raise OawError(
            f"'{DEEP_RESEARCH_HEADING}' must contain exactly one non-empty fenced text block"
        )
    return block.group(1).strip() + "\n"


def research_synthesis_text(project: str, track: str, title: str, date: str) -> str:
    return (
        "---\n"
        "type: research-synthesis\n"
        f"project: {project}\n"
        f"track: {track}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        "status: todo\n"
        f"created: {date}\n"
        "tags:\n"
        "  - projects\n"
        f"  - {project}\n"
        "  - research-synthesis\n"
        "---\n\n"
        f"# Synthesis - {title}\n\n"
        "## Source reports\n\n"
        "![[Bases/Research packet.base#Source reports]]\n\n"
        "## Synthesis\n\n"
    )


def research_packet_base_text() -> str:
    return (
        "filters:\n"
        "  and:\n"
        '    - type == "research-result"\n'
        "    - file.folder == this.file.folder\n"
        "views:\n"
        "  - type: table\n"
        "    name: Source reports\n"
        "    order:\n"
        "      - file.name\n"
        "      - source\n"
        "      - status\n"
        "      - url\n"
        "      - created\n"
    )


def safe_research_source(raw: str) -> str:
    source = raw.strip()
    if (
        not source
        or source in {".", ".."}
        or source != raw
        or any(ch in source for ch in '/\\:*?"<>|')
        or any(unicodedata.category(ch).startswith("C") for ch in source)
        or Path(source).name != source
    ):
        raise OawError(
            "research start requires a safe --source label without surrounding whitespace, "
            "path separators, traversal components, or control characters"
        )
    return source


def http_url(raw: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise OawError("research start --url must be an absolute HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OawError("research start --url must be an absolute HTTP(S) URL")
    return raw


def start_research_run(args: argparse.Namespace) -> None:
    root = vault_root()
    project_root, _ = resolve_project_root(args.project, root)
    track = safe_relative_path(args.track, "track")
    source = safe_research_source(args.source)
    url = http_url(args.url)
    packet_dir = project_root / "Research" / track
    prompt_path = packet_dir / "Prompt.md"
    if not prompt_path.is_file():
        raise OawError(f"research prompt not found: {prompt_path.relative_to(root).as_posix()}")
    prompt = prompt_path.read_text(encoding="utf-8")
    provider_prompt_from_text(prompt)
    if len(re.findall(rf"(?m)^{re.escape(RUNNING_RESEARCH_HEADING)}[ \t]*$", prompt)) != 1:
        raise OawError(
            f"research prompt must contain exactly one '{RUNNING_RESEARCH_HEADING}' heading"
        )
    result = packet_dir / f"Results - {source}.md"
    if result.exists():
        raise OawError(f"research source already exists: {result.relative_to(root).as_posix()}")
    _, frontmatter, _ = split_note(prompt)
    metadata = parse_frontmatter(frontmatter)
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        raise OawError("research prompt frontmatter requires a non-empty title")
    project = slugify(project_root.name)
    created = _dt.date.today().isoformat()
    entry = f"{source}: [running]({url})"
    updated_prompt = append_to_section(prompt, RUNNING_RESEARCH_HEADING[3:], entry)
    result_text = (
        "---\n"
        "type: research-result\n"
        f"source: {json.dumps(source, ensure_ascii=False)}\n"
        f"url: {json.dumps(url, ensure_ascii=False)}\n"
        "status: running\n"
        f"project: {project}\n"
        f"track: {track.as_posix()}\n"
        f"created: {created}\n"
        "tags:\n"
        "  - projects\n"
        f"  - {project}\n"
        "  - research-result\n"
        "---\n\n"
        f"# Results - {source}\n\n"
        f"Topic: {title}\n\n"
        "The provider run is in progress. Ingest the completed report with the "
        "obsidian-research helper.\n"
    )
    synthesis = packet_dir / "Synthesis.md"
    base = root / RESEARCH_PACKET_BASE
    transaction = VaultTransaction()
    transaction.stage(result, result_text)
    transaction.stage(prompt_path, updated_prompt)
    if not synthesis.exists():
        transaction.stage(
            synthesis,
            research_synthesis_text(project, track.as_posix(), title, created),
        )
    if not base.exists():
        transaction.stage(base, research_packet_base_text())
    transaction.commit()
    print(f"Created: {result.relative_to(root).as_posix()}")
    print("Status: running")
    print("Prompt: updated")


def strip_obs_prefix(raw_id: str) -> str:
    value = raw_id.strip()
    if value.startswith("obs:"):
        value = value[4:]
    if not value:
        raise OawError("empty ID")
    return value


def read_frontmatter_only(path: Path) -> tuple[str, dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return "", {}
        total = len(first.encode("utf-8"))
        lines: list[str] = []
        for line in handle:
            total += len(line.encode("utf-8"))
            if total > FRONTMATTER_READ_LIMIT:
                raise OawError(f"frontmatter too large or not closed before safety limit: {path}")
            if line.strip() == "---":
                return "".join(lines), parse_frontmatter("".join(lines))
            lines.append(line)
    raise OawError(f"frontmatter is not closed: {path}")


def title_from_body(path: Path, body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def iter_markdown(root: Path):
    skip = {".git", ".obsidian", ".trash", "node_modules", ".venv", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for filename in filenames:
            if filename.endswith(".md"):
                yield Path(dirpath) / filename


def note_match(path: Path, root: Path, target: str) -> NoteMatch | None:
    try:
        _, fm, body, data = read_note(path)
    except UnicodeDecodeError:
        return None
    note_id = data.get("id")
    aliases = data.get("aliases", [])
    matched_by = ""
    if isinstance(note_id, str) and note_id == target:
        matched_by = "id"
    elif isinstance(aliases, list) and target in aliases:
        matched_by = "aliases"
    else:
        return None
    rel = path.relative_to(root).as_posix()
    return NoteMatch(
        path=path,
        relpath=rel,
        note_id=note_id if isinstance(note_id, str) else None,
        matched_by=matched_by,
        title=title_from_body(path, body),
        frontmatter_text=fm.rstrip(),
        frontmatter=data,
    )


def resolve_id(raw_id: str, root: Path) -> NoteMatch:
    target = strip_obs_prefix(raw_id)
    matches = [m for p in iter_markdown(root) if (m := note_match(p, root, target))]
    if not matches:
        matches = project_alias_matches(target, root)
    if not matches:
        raise OawError(f"no note with frontmatter id or alias '{target}' under {root}")
    if len(matches) > 1:
        paths = "\n".join(f"  {m.relpath} ({m.matched_by})" for m in matches)
        raise OawError(f"id '{target}' is not unique:\n{paths}")
    return matches[0]


def project_alias_matches(target: str, root: Path) -> list[NoteMatch]:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", target):
        return []
    index_id = f"{target}-index"
    matches: list[NoteMatch] = []
    projects = root / "Projects"
    if not projects.exists():
        return []
    for path in sorted(projects.glob("*/Index.md")):
        match = note_match(path, root, index_id)
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


def outline(path: Path) -> list[str]:
    _, _, body, _ = read_note(path)
    lines: list[str] = []
    in_fence = False
    for number, line in enumerate(body.splitlines(), start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,6} ", line):
            lines.append(f"{number}: {line}")
    return lines


def output_resolve(match: NoteMatch, args: argparse.Namespace) -> None:
    if args.json:
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
    if args.path:
        print(match.path)
        return
    if args.meta:
        print(match.frontmatter_text)
        return
    if args.outline:
        print("\n".join(outline(match.path)))
        return
    if args.full:
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


def is_project_task(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    return len(parts) >= 4 and parts[0] == "Projects" and parts[-2] == "Tasks"


def project_root_for_task(path: Path, root: Path) -> Path:
    if not is_project_task(path, root):
        raise OawError("lifecycle writes are only supported for Projects/*/Tasks notes in v1")
    rel = path.relative_to(root)
    return root / rel.parts[0] / rel.parts[1]


def detect_session(allow_missing: bool) -> tuple[str, str]:
    for provider, env_name in SESSION_ENV:
        value = os.environ.get(env_name)
        if value:
            return provider, f"{env_name}={value}"
    if allow_missing:
        return "Unknown", "session_id=unavailable"
    raise OawError(
        "no stable session ID found; set CODEX_THREAD_ID or pass --allow-missing-session-id"
    )


def append_session_id_frontmatter(text: str, session_ref: str) -> str:
    name, separator, value = session_ref.partition("=")
    if not separator or (name == "session_id" and value == "unavailable"):
        return text
    return append_frontmatter_list_value(text, "session-ids", value)


def append_session_entry(
    text: str, provider: str, session_ref: str, note: str, checks: str | None
) -> str:
    today = _dt.date.today().isoformat()
    detail = note.strip()
    if checks:
        detail = f"{detail}; checks: {checks.strip()}"
    entry = f"- {today} - {provider} - `{session_ref}` - {detail}\n"
    if "## Agent sessions" not in text:
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}\n## Agent sessions\n\n{entry}"
    marker = "## Agent sessions"
    idx = text.index(marker)
    after = text.index("\n", idx) + 1
    if after >= len(text):
        return f"{text}\n\n{entry}"
    next_heading = re.search(r"\n## ", text[after:])
    if not next_heading:
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}{entry}"
    insert_at = after + next_heading.start() + 1
    before = text[:insert_at]
    after_text = text[insert_at:]
    if not before.endswith("\n"):
        before += "\n"
    return before + entry + after_text


def heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+\S", line)
    return len(match.group(1)) if match else None


def fence_delimiter(line: str) -> str | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
    return match.group(1)[0] if match else None


def normalize_heading(section: str) -> str:
    value = section.strip()
    if not value:
        raise OawError("section heading must not be empty")
    if value.startswith("#"):
        if not heading_level(value):
            raise OawError("section heading must look like a Markdown heading")
        return value
    return f"## {value}"


def append_markdown_block_to_section(text: str, section: str, block: str) -> str:
    heading = normalize_heading(section)
    block = block.strip()
    if not block:
        raise OawError("block content must not be empty")
    lines = text.splitlines()
    target_idx: int | None = None
    target_level = heading_level(heading)
    if target_level is None:
        raise OawError("section heading must look like a Markdown heading")
    active_fence: str | None = None
    for idx, line in enumerate(lines):
        delimiter = fence_delimiter(line)
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif active_fence == delimiter:
                active_fence = None
            continue
        if active_fence is None and line.strip() == heading:
            target_idx = idx
            break
    if target_idx is None:
        prefix = "" if text.endswith("\n") else "\n"
        return f"{text}{prefix}\n{heading}\n\n{block}\n"

    insert_at = len(lines)
    active_fence = None
    for idx in range(target_idx + 1, len(lines)):
        delimiter = fence_delimiter(lines[idx])
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif active_fence == delimiter:
                active_fence = None
            continue
        if active_fence is not None:
            continue
        level = heading_level(lines[idx])
        if level is not None and level <= target_level:
            insert_at = idx
            break

    before = lines[:insert_at]
    after = lines[insert_at:]
    while before and before[-1] == "":
        before.pop()
    new_lines = [*before, "", block, ""]
    if after:
        new_lines.extend(after)
    return "\n".join(new_lines).rstrip() + "\n"


def append_observation_entry(text: str, section: str, title: str, body: str) -> str:
    clean_title = title.strip()
    clean_body = body.strip()
    if not clean_title:
        raise OawError("observation title must not be empty")
    if not clean_body:
        raise OawError("observation body must not be empty")
    today = _dt.date.today().isoformat()
    return append_markdown_block_to_section(
        text,
        section,
        f"### {today} - {clean_title}\n\n{clean_body}",
    )


def update_note_session(
    raw_id: str,
    note: str,
    checks: str | None,
    allow_missing: bool,
) -> None:
    root = vault_root()
    match = resolve_id(raw_id, root)
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    match.path.write_text(text, encoding="utf-8")
    print(f"Updated: {match.relpath}")
    print("Section: Agent sessions")


def update_note_observation(raw_id: str, section: str, title: str, body: str) -> None:
    root = vault_root()
    match = resolve_id(raw_id, root)
    text = match.path.read_text(encoding="utf-8")
    text = append_observation_entry(text, section, title, body)
    match.path.write_text(text, encoding="utf-8")
    print(f"Updated: {match.relpath}")
    print(f"Section: {normalize_heading(section)}")


def card_line(task_path: Path, project_root: Path, title: str, note_id: str | None) -> str:
    rel_no_ext = task_path.relative_to(project_root).with_suffix("").as_posix()
    suffix = f" - {note_id}" if note_id else ""
    return f"- [ ] [[{rel_no_ext}|{title}]]{suffix}"


def board_column_insert_at(lines: list[str], target_column: str) -> int:
    target_order = BOARD_COLUMN_ORDER.index(target_column)
    for idx, line in enumerate(lines):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if (
            heading
            and heading.group(1) in BOARD_COLUMN_ORDER
            and BOARD_COLUMN_ORDER.index(heading.group(1)) > target_order
        ):
            return idx
    return len(lines)


def move_board_card(
    project_root: Path, task_path: Path, title: str, note_id: str | None, status: str
) -> bool:
    board = project_root / "Board.md"
    if not board.exists():
        return False
    text = board.read_text(encoding="utf-8")
    lines = text.splitlines()
    target_column = {
        "backlog": "Backlog",
        "todo": "Todo",
        "active": "Active",
        "done": "Done",
    }[status]
    identifiers = {task_path.stem}
    if note_id:
        identifiers.add(note_id)
    new_lines: list[str] = []
    existing_card = ""
    target_heading_idx: int | None = None
    in_column: str | None = None
    for line in lines:
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            in_column = heading.group(1)
            if in_column == target_column:
                target_heading_idx = len(new_lines)
            new_lines.append(line)
            continue
        if line.startswith("- [ ] ") and any(token in line for token in identifiers):
            existing_card = line
            continue
        new_lines.append(line)
    card = existing_card or card_line(task_path, project_root, title, note_id)
    if target_heading_idx is None:
        insert_at = board_column_insert_at(new_lines, target_column)
        new_lines[insert_at:insert_at] = [f"## {target_column}", "", card, ""]
    else:
        insert_at = target_heading_idx + 1
        while insert_at < len(new_lines) and new_lines[insert_at] == "":
            insert_at += 1
        new_lines.insert(insert_at, card)
    board.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def updated_board_text(
    project_root: Path, task_path: Path, title: str, note_id: str, status: str
) -> tuple[Path | None, str | None]:
    """Return a board update without writing it, for multi-file transactions."""
    board = project_root / "Board.md"
    if not board.exists():
        return None, None
    lines = board.read_text(encoding="utf-8").splitlines()
    target_column = {
        "backlog": "Backlog",
        "todo": "Todo",
        "active": "Active",
        "done": "Done",
    }[status]
    identifiers = {task_path.stem, note_id}
    new_lines: list[str] = []
    existing_card = ""
    target_heading_idx: int | None = None
    for line in lines:
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            if heading.group(1) == target_column:
                target_heading_idx = len(new_lines)
            new_lines.append(line)
            continue
        if line.startswith("- [ ] ") and any(token in line for token in identifiers):
            existing_card = line
            continue
        new_lines.append(line)
    card = existing_card or card_line(task_path, project_root, title, note_id)
    if target_heading_idx is None:
        insert_at = board_column_insert_at(new_lines, target_column)
        new_lines[insert_at:insert_at] = [f"## {target_column}", "", card, ""]
    else:
        insert_at = target_heading_idx + 1
        while insert_at < len(new_lines) and new_lines[insert_at] == "":
            insert_at += 1
        new_lines.insert(insert_at, card)
    return board, "\n".join(new_lines) + "\n"


def board_path(root: Path) -> Path:
    board = root / NEXT_BOARD
    if not board.exists():
        raise OawError(f"board not found: {board}")
    return board


def board_card(link: str, title: str, why: str, card_id: str) -> str:
    clean_link = link.strip().removesuffix(".md")
    clean_title = title.strip()
    clean_why = why.strip()
    clean_id = card_id.strip()
    if not clean_link or not clean_title or not clean_why or not clean_id:
        raise OawError("board add requires non-empty --link, --title, --why, and --id")
    return f"- [ ] [[{clean_link}|{clean_title}]] - {clean_why} ({clean_id})"


def find_board_column(lines: list[str], column: str) -> int | None:
    for idx, line in enumerate(lines):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading and heading.group(1) == column:
            return idx
    return None


def remove_board_card(lines: list[str], token: str) -> tuple[list[str], str | None]:
    kept: list[str] = []
    found: str | None = None
    for line in lines:
        if re.match(r"^-\s+\[[ xX]\]\s+", line) and token in line:
            if found is not None:
                raise OawError(f"multiple board cards match '{token}'")
            found = line
            continue
        kept.append(line)
    return kept, found


def insert_board_card(lines: list[str], column: str, card: str) -> list[str]:
    target_idx = find_board_column(lines, column)
    updated = list(lines)
    if target_idx is None:
        if updated and updated[-1] != "":
            updated.append("")
        updated.extend([f"## {column}", "", card])
        return updated
    insert_at = target_idx + 1
    while insert_at < len(updated) and updated[insert_at] == "":
        insert_at += 1
    updated.insert(insert_at, card)
    return updated


def ensure_project_backlog_column(project: str) -> None:
    root = vault_root()
    project_root = root / "Projects" / project
    if not project_root.exists():
        raise OawError(f"project not found: {project_root}")
    path = project_root / "Board.md"
    if not path.exists():
        raise OawError(f"project board not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if find_board_column(lines, "Backlog") is not None:
        print(f"Board: {path.relative_to(root).as_posix()}")
        print("Backlog: present")
        return
    insert_at = board_column_insert_at(lines, "Backlog")
    insertion = ["## Backlog", ""]
    if insert_at > 0 and lines[insert_at - 1] != "":
        insertion.insert(0, "")
    lines[insert_at:insert_at] = insertion
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Board: {path.relative_to(root).as_posix()}")
    print("Backlog: added")


def update_next_board(
    column: str,
    token: str | None,
    card: str | None,
    done: bool,
) -> None:
    root = vault_root()
    path = board_path(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    if token:
        lines, existing = remove_board_card(lines, token)
        if existing is None:
            raise OawError(f"no board card matches '{token}'")
        card = existing
    if card is None:
        raise OawError("missing board card")
    if done:
        card = re.sub(r"^-\s+\[[ xX]\]", "- [x]", card, count=1)
    else:
        card = re.sub(r"^-\s+\[[ xX]\]", "- [ ]", card, count=1)
    lines = insert_board_card(lines, column, card)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Board: {NEXT_BOARD.as_posix()}")
    print(f"Column: {column}")
    if token:
        print(f"Matched: {token}")


def update_task(
    raw_id: str, status: str, note: str, checks: str | None, allow_missing: bool
) -> None:
    root = vault_root()
    match = resolve_id(raw_id, root)
    if not is_project_task(match.path, root):
        raise OawError("lifecycle writes are only supported for Projects/*/Tasks notes in v1")
    if status == "done" and not checks:
        raise OawError("task complete requires --checks")
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = set_frontmatter_scalar(text, "status", status)
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    match.path.write_text(text, encoding="utf-8")
    refreshed = resolve_id(raw_id, root)
    moved = move_board_card(
        project_root_for_task(refreshed.path, root),
        refreshed.path,
        refreshed.title,
        refreshed.note_id,
        status,
    )
    print(f"Updated: {refreshed.relpath}")
    print(f"Status: {status}")
    print(f"Board: {'updated' if moved else 'not found'}")


def append_task_note(raw_id: str, note: str, checks: str | None, allow_missing: bool) -> None:
    root = vault_root()
    match = resolve_id(raw_id, root)
    if not is_project_task(match.path, root):
        raise OawError("lifecycle writes are only supported for Projects/*/Tasks notes in v1")
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    match.path.write_text(text, encoding="utf-8")
    refreshed = resolve_id(raw_id, root)
    status = refreshed.frontmatter.get("status", "")
    print(f"Updated: {refreshed.relpath}")
    print(f"Status: {status}")
    print("Board: unchanged")


def resolve_project_root(raw: str, root: Path) -> tuple[Path, str | None]:
    """Resolve a project alias or folder name to (project folder, alias prefix)."""
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


def create_task(args: argparse.Namespace) -> None:
    root = vault_root()
    capture = resolve_id(args.from_capture, root) if args.from_capture else None
    if capture:
        if capture.frontmatter.get("type") != "capture":
            raise OawError(f"from-capture source is not a capture note: {capture.relpath}")
        if not capture.note_id:
            raise OawError("from-capture source must have a stable frontmatter id")
        if capture.frontmatter.get("status") == "triaged":
            raise OawError(f"capture is already triaged: {capture.note_id}")
    elif args.start:
        raise OawError("--start is only supported with --from-capture")
    title = (args.title or (capture.title if capture else "")).strip()
    if not title:
        raise OawError("task create requires a non-empty --title")
    if "/" in title or title.startswith("."):
        raise OawError("task title must not contain '/' or start with '.'")
    raw_project = args.project
    if not raw_project and capture:
        try:
            relative = capture.path.relative_to(root / "Projects")
            raw_project = relative.parts[0]
        except (ValueError, IndexError) as exc:
            raise OawError("--project is required when the capture is outside Projects/") from exc
    if not raw_project:
        raise OawError(
            "task create requires --project unless --from-capture identifies a project capture"
        )
    project_root, alias = resolve_project_root(raw_project, root)
    provider, session_ref = detect_session(args.allow_missing_session_id)
    if args.id is not None:
        note_id = args.id.strip()
        if not note_id:
            raise OawError("task create requires a non-empty --id")
    elif alias:
        note_id = f"{alias}-TSK-{slugify(title)}"
    else:
        raise OawError(
            "cannot derive a task ID: project index has no '<ALIAS>-index' id; pass --id"
        )
    path = project_root / "Tasks" / f"{title}.md"
    relpath = path.relative_to(root)
    conflicts = [
        match
        for candidate in iter_markdown(root)
        if (match := note_match(candidate, root, note_id))
    ]
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    if path.exists():
        raise OawError(f"task note already exists: {relpath.as_posix()}")
    today = _dt.date.today().isoformat()
    project_slug = slugify(project_root.name)
    status = "active" if args.start else args.status
    lines = [
        "---",
        "type: task",
        f"project: {project_slug}",
        f"status: {status}",
        f"created: {today}",
    ]
    if args.priority is not None:
        lines.append(f"priority: {args.priority}")
    if args.effort:
        lines.append(f"effort: {args.effort}")
    lines += [
        f"id: {note_id}",
        "aliases:",
        f"  - {note_id}",
        "tags:",
        "  - projects",
        f"  - {project_slug}",
        "  - task",
    ]
    if capture:
        lines.append(f"source-capture: {capture.note_id}")
    for tag in args.tag or []:
        cleaned = tag.strip()
        if cleaned:
            lines.append(f"  - {cleaned}")
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else ""
    if session_id and session_id != "unavailable":
        lines += ["session-ids:", f"  - {session_id}"]
    lines += ["---", "", f"# {title}", "", "## Problem", ""]
    lines.append(args.note.strip() if args.note else "_To be defined._")
    lines += ["", "## Related", ""]
    index = project_root / "Index.md"
    if alias and index.exists():
        index_rel = index.relative_to(root).with_suffix("").as_posix()
        lines.append(f"- [[{index_rel}|{alias}-index]]")
        lines.append("")
    if capture:
        lines.append(f"- {durable_wikilink(capture, capture.note_id)}")
        lines.append("")
    lines += [
        "## Agent sessions",
        "",
        f"- {today} - {provider} - `{session_ref}` - Created task note.",
    ]
    task_text = "\n".join(lines) + "\n"
    if capture:
        task_link = f"[[{relpath.with_suffix('').as_posix()}|{note_id}]]"
        capture_text = capture.path.read_text(encoding="utf-8")
        capture_text = append_to_section(
            capture_text,
            "Related",
            task_link,
        )
        capture_text = append_frontmatter_list_value(capture_text, "destinations", task_link)
        capture_text = set_frontmatter_scalar(capture_text, "status", "triaged")
        board, board_text = updated_board_text(project_root, path, title, note_id, status)
        transaction = VaultTransaction()
        transaction.stage(path, task_text)
        if board and board_text:
            transaction.stage(board, board_text)
        transaction.stage(capture.path, capture_text)  # transition is deliberately last
        transaction.commit()
        moved = board is not None
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(task_text, encoding="utf-8")
        moved = move_board_card(project_root, path, title, note_id, status)
    print(f"Created: {relpath.as_posix()}")
    print(f"ID: {note_id}")
    print(f"Status: {status}")
    print(f"Board: {'updated' if moved else 'not found'}")
    if capture:
        print(f"Capture: {capture.note_id} -> triaged")


def note_type_matches(data: dict[str, object], note_type: str) -> bool:
    value = data.get("type")
    return isinstance(value, str) and value == note_type


def note_status(data: dict[str, object]) -> str:
    value = data.get("status", "")
    return str(value) if value is not None else ""


def project_note_rows(
    project_root: Path,
    root: Path,
    note_type: str,
    status: str | None,
    include_archived: bool,
) -> list[tuple[str, str, str, str]]:
    rows = []
    for path in sorted(project_root.rglob("*.md")):
        _, _, body, data = read_note(path)
        if not note_type_matches(data, note_type):
            continue
        current_status = note_status(data)
        if status and current_status != status:
            continue
        if not status and current_status == "archived" and not include_archived:
            continue
        note_id = data.get("id", "")
        title = title_from_body(path, body)
        rows.append((str(note_id), current_status, title, path.relative_to(root).as_posix()))
    return rows


def list_project(
    project: str,
    note_type: str,
    status: str | None,
    include_archived: bool,
) -> None:
    root = vault_root()
    project_root = root / "Projects" / project
    if not project_root.exists():
        raise OawError(f"project not found: {project_root}")
    if note_type == "task":
        tasks = project_root / "Tasks"
        if not tasks.exists():
            raise OawError(f"project tasks folder not found: {tasks}")
        rows = project_note_rows(tasks, root, note_type, status, True)
    else:
        rows = project_note_rows(project_root, root, note_type, status, include_archived)
    for row in rows:
        print("\t".join(row))


def default_ingestion_root() -> Path:
    return Path(os.environ.get("OAW_INGESTION_ROOT", "~/obsidian-ingestion")).expanduser()


def safe_export_destination(args: argparse.Namespace) -> Path:
    raw = Path(args.destination)
    if raw.is_absolute():
        raise OawError("--destination must be vault-relative")
    if ".." in raw.parts:
        raise OawError("--destination must not contain '..'")
    return raw


def frontmatter_tags(data: dict[str, object]) -> set[str]:
    value = data.get("tags", [])
    tags: set[str] = set()
    if isinstance(value, list):
        tags.update(str(item).lstrip("#") for item in value)
    elif isinstance(value, str):
        tags.update(part.strip().lstrip("#") for part in re.split(r"[,\s]+", value) if part.strip())
    return tags


def trueish(value: object) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1", "personal"}


def safe_export_marker(data: dict[str, object]) -> tuple[bool, str, str]:
    scope = data.get("export-scope")
    if isinstance(scope, str) and scope.strip().lower() == SAFE_EXPORT_SCOPE:
        return True, "export-scope: personal", "accepted"
    approved = data.get("export-approved")
    if isinstance(approved, str) and approved.strip().lower() == SAFE_EXPORT_SCOPE:
        return True, "export-approved: personal", "accepted"
    legacy_property = data.get(SAFE_EXPORT_TAG)
    if legacy_property is not None and trueish(legacy_property):
        return True, f"{SAFE_EXPORT_TAG}: true", "accepted compatibility property"
    if SAFE_EXPORT_TAG in frontmatter_tags(data):
        return True, f"tag: {SAFE_EXPORT_TAG}", "accepted compatibility tag"
    return False, "", "missing safe export marker"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OawError(f"could not find unique destination for {path}")


def iter_ingestion_markdown(root: Path) -> list[Path]:
    if not root.exists():
        return []
    quarantine = root / SAFE_EXPORT_QUARANTINE
    paths: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        try:
            path.relative_to(quarantine)
            continue
        except ValueError:
            pass
        if path.is_file():
            paths.append(path)
    return paths


def classify_export_candidate(
    path: Path, ingestion_root: Path, destination_root: Path
) -> ExportCandidate:
    relative = path.relative_to(ingestion_root)
    try:
        _, data = read_frontmatter_only(path)
    except UnicodeDecodeError as exc:
        return ExportCandidate(path, relative, False, "", f"frontmatter is not UTF-8: {exc}")
    except OawError as exc:
        return ExportCandidate(path, relative, False, "", str(exc))
    safe, marker, reason = safe_export_marker(data)
    destination = destination_root / relative if safe else None
    return ExportCandidate(path, relative, safe, marker, reason, destination)


def classify_export_candidates(
    ingestion_root: Path,
    destination_root: Path,
) -> list[ExportCandidate]:
    return [
        classify_export_candidate(path, ingestion_root, destination_root)
        for path in iter_ingestion_markdown(ingestion_root)
    ]


def move_to_quarantine(candidate: ExportCandidate, ingestion_root: Path) -> Path:
    quarantine = ingestion_root / SAFE_EXPORT_QUARANTINE / candidate.relative_source
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(quarantine)
    shutil.move(str(candidate.source), str(destination))
    return destination


def ingest_candidate(candidate: ExportCandidate) -> Path:
    if candidate.destination is None:
        raise OawError("safe candidate is missing a destination")
    destination = unique_destination(candidate.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate.source, destination)
    candidate.source.unlink()
    return destination


def safe_export_ingest(args: argparse.Namespace) -> None:
    ingestion_root = args.ingestion_root.expanduser().resolve()
    root = vault_root()
    destination_root = (root / safe_export_destination(args)).resolve()
    if not destination_root.is_relative_to(root):
        raise OawError("safe export destination must remain inside the vault")
    if root.is_relative_to(ingestion_root):
        raise OawError("ingestion root must not be or contain the vault")
    if destination_root.is_relative_to(ingestion_root):
        raise OawError("safe export destination must not be inside the ingestion root")
    candidates = classify_export_candidates(ingestion_root, destination_root)
    accepted = [candidate for candidate in candidates if candidate.safe]
    rejected = [candidate for candidate in candidates if not candidate.safe]
    mode = args.mode
    print(f"Mode: {mode}")
    print(f"Ingestion: {ingestion_root}")
    print(f"Destination: {destination_root.relative_to(root).as_posix()}")
    print(f"Candidates: {len(candidates)}")
    for candidate in candidates:
        rel = candidate.relative_source.as_posix()
        if candidate.safe:
            marker = candidate.marker
            destination = candidate.destination
            if destination is None:
                raise OawError(f"safe candidate has no destination: {rel}")
            if mode == "write":
                written = ingest_candidate(candidate)
                print(
                    f"ACCEPT {rel} [{marker}] -> {written.relative_to(root).as_posix()}; removed source"
                )
            else:
                target = unique_destination(destination).relative_to(root).as_posix()
                print(f"ACCEPT {rel} [{marker}] -> {target}; dry-run")
        else:
            if mode == "write":
                quarantined = move_to_quarantine(candidate, ingestion_root)
                print(
                    f"REJECT {rel} [{candidate.reason}] -> quarantine "
                    f"{quarantined.relative_to(ingestion_root).as_posix()}"
                )
            else:
                print(f"REJECT {rel} [{candidate.reason}] -> quarantine; dry-run")
    print(f"Accepted: {len(accepted)}")
    print(f"Rejected: {len(rejected)}")


def notes_containing_literal(root: Path, literal: str) -> list[NoteMatch]:
    matches: list[NoteMatch] = []
    for path in iter_markdown(root):
        try:
            text, fm, body, data = read_note(path)
        except UnicodeDecodeError:
            continue
        if literal not in text:
            continue
        note_id = data.get("id")
        rel = path.relative_to(root).as_posix()
        matches.append(
            NoteMatch(
                path=path,
                relpath=rel,
                note_id=note_id if isinstance(note_id, str) else None,
                matched_by="content",
                title=title_from_body(path, body),
                frontmatter_text=fm.rstrip(),
                frontmatter=data,
            )
        )
    return sorted(matches, key=lambda item: item.relpath)


def find_session_artifacts(
    session_id: str,
    codex_root: Path,
    claude_root: Path,
) -> list[SessionArtifact]:
    artifacts: list[SessionArtifact] = []
    escaped_id = glob.escape(session_id)
    if codex_root.exists():
        for path in sorted(codex_root.rglob(f"rollout-*-{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("codex-rollout", path))
    if claude_root.exists():
        for path in sorted(claude_root.rglob(f"{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("claude-transcript", path))
        for path in sorted(claude_root.rglob(f"subagents/agent-{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("claude-subagent", path))
    return artifacts


def text_from_json(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [part for item in value if (part := text_from_json(item))]
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"].strip() or None
        if isinstance(value.get("content"), str):
            return value["content"].strip() or None
        if isinstance(value.get("message"), str):
            return value["message"].strip() or None
        for key in ("content", "message", "messages", "parts"):
            if key in value and (text := text_from_json(value[key])):
                return text
    return None


def iter_json_values(value: object):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_json_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_values(item)


def cwd_from_record(record: object) -> str | None:
    for value in iter_json_values(record):
        if isinstance(value, dict) and isinstance(value.get("cwd"), str):
            return value["cwd"]
    return None


def user_message_from_record(record: object) -> str | None:
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if isinstance(payload, dict) and (text := user_message_from_record(payload)):
        return text
    role = str(record.get("role", "")).lower()
    record_type = str(record.get("type", "")).lower()
    if role != "user" and "user" not in record_type:
        nested_message = record.get("message")
        if (
            not isinstance(nested_message, dict)
            or str(nested_message.get("role", "")).lower() != "user"
        ):
            return None
        text = text_from_json(nested_message)
    else:
        text = text_from_json(record)
    if not text:
        return None
    first_line = " ".join(text.split())
    if first_line.startswith("# AGENTS.md instructions") or first_line.startswith(
        "<environment_context>"
    ):
        return None
    return first_line[:240]


def vault_paths_from_text(text: str) -> list[str]:
    pattern = re.compile(
        r"\b(?:Projects|Agents|References|Captures|Research)/"
        r"[^\n\r\t`\"'<>|]+?\.md\b"
    )
    seen: set[str] = set()
    paths: list[str] = []
    for match in pattern.finditer(text):
        path = match.group(0).strip()
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= 10:
            break
    return paths


def summarize_artifact(path: Path) -> tuple[str | None, str | None, list[str]]:
    cwd: str | None = None
    first_user: str | None = None
    raw_text_parts: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                raw_text_parts.append(line)
                if cwd and first_user:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None:
                    cwd = cwd_from_record(record)
                if first_user is None:
                    first_user = user_message_from_record(record)
    except OSError:
        return None, None, []
    return cwd, first_user, vault_paths_from_text("".join(raw_text_parts))


def session_lookup(args: argparse.Namespace) -> None:
    session_id = args.session_id.strip()
    if not session_id:
        raise OawError("empty session ID")
    root = vault_root()
    note_hits = notes_containing_literal(root, session_id)
    if note_hits:
        print(f"Session: {session_id}")
        print("Vault matches:")
        for hit in note_hits:
            note_id = hit.note_id or "(no id)"
            print(f"- {hit.relpath} | id: {note_id}")
        return

    artifacts = find_session_artifacts(
        session_id,
        args.codex_root.expanduser(),
        args.claude_root.expanduser(),
    )
    if not artifacts:
        print(f"Session: {session_id}")
        print("Status: not logged")
        print("No vault note or harness artifact found.")
        return

    print(f"Session: {session_id}")
    print("Harness artifacts:")
    for artifact in artifacts:
        cwd, first_user, vault_paths = summarize_artifact(artifact.path)
        print(f"- {artifact.kind}: {artifact.path}")
        print(f"  cwd: {cwd or '(unknown)'}")
        print(f"  first user: {first_user or '(unknown)'}")
        if vault_paths:
            print("  vault paths:")
            for vault_path in vault_paths:
                print(f"    - {vault_path}")
        else:
            print("  vault paths: (none)")
        if args.verbose:
            from .session_metrics import (
                SessionMetrics,
                codex_rollout_metrics,
                format_duration,
                format_timestamp,
                format_tokens,
            )

            metrics = (
                codex_rollout_metrics(artifact.path)
                if artifact.kind == "codex-rollout"
                else SessionMetrics()
            )
            user_turns = (
                str(metrics.user_turns) if metrics.user_turns is not None else "unavailable"
            )
            assistant_turns = (
                str(metrics.assistant_turns)
                if metrics.assistant_turns is not None
                else "unavailable"
            )
            print(f"  Started: {format_timestamp(metrics.started)}")
            print(f"  Ended: {format_timestamp(metrics.ended)}")
            print(f"  Duration: {format_duration(metrics.duration)}")
            print(f"  Turns: user={user_turns}, assistant={assistant_turns}")
            print(f"  Tokens: {format_tokens(metrics)}")


def note_from_path(path: Path, root: Path, matched_by: str = "path") -> NoteMatch:
    try:
        _, fm, body, data = read_note(path)
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
            elif active_fence == delimiter:
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


def link_check(args: argparse.Namespace) -> None:
    root = vault_root()
    left = resolve_note_arg(args.left, root)
    right = resolve_note_arg(args.right, root)
    print(f"Left: {left.relpath} | id: {left.note_id or '(none)'}")
    print(f"Right: {right.relpath} | id: {right.note_id or '(none)'}")
    print(f"Left links right: {'yes' if note_has_link_to(left, right) else 'no'}")
    print(f"Right links left: {'yes' if note_has_link_to(right, left) else 'no'}")


def link_list(args: argparse.Namespace) -> None:
    root = vault_root()
    source = resolve_note_arg(args.note, root)
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


def link_ensure(args: argparse.Namespace) -> None:
    root = vault_root()
    source = resolve_note_arg(args.source, root)
    target = resolve_note_arg(args.target, root)
    changed = ensure_link(source, target, args.section, args.label, args.write)
    if not changed:
        print(f"Source: {source.relpath}")
        print(f"Target: {target.relpath}")
        print("Link: present")


def link_ensure_bidirectional(args: argparse.Namespace) -> None:
    root = vault_root()
    left = resolve_note_arg(args.left, root)
    right = resolve_note_arg(args.right, root)
    changed_left = ensure_link(left, right, args.section, right.note_id, args.write)
    changed_right = ensure_link(right, left, args.section, left.note_id, args.write)
    if not changed_left and not changed_right:
        print("Links: present")


OPAQUE_LINK_RE = re.compile(r"^(?:OAW|AGT|SR|CDX|FAB|PMX)-[A-Za-z0-9][A-Za-z0-9-]*$")


def link_lint(args: argparse.Namespace) -> None:
    root = vault_root()
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


def default_claude_projects_root() -> Path:
    return Path(os.environ.get("OAW_CLAUDE_PROJECTS_ROOT", "~/.claude/projects")).expanduser()


def default_codex_sessions_root() -> Path:
    return Path(os.environ.get("OAW_CODEX_SESSIONS_ROOT", "~/.codex/sessions")).expanduser()


def session_lookup_codex_root() -> Path:
    return default_codex_sessions_root()


def session_lookup_claude_root() -> Path:
    return default_claude_projects_root()


def default_plugin_data_root() -> Path:
    return Path("~/.claude/plugins/data").expanduser()


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "session"


def validate_date(value: str) -> str:
    try:
        _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise OawError("date must use YYYY-MM-DD") from exc
    return value


def create_retrospective(args: argparse.Namespace) -> None:
    root = vault_root()
    title = args.title.strip()
    if not title:
        raise OawError("retro create requires a non-empty --title")
    date = validate_date(args.date or _dt.date.today().isoformat())
    provider, session_ref = detect_session(args.allow_missing_session_id)
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else session_ref
    slug = slugify(title)
    note_id = args.id.strip() if args.id else f"AGT-RETRO-{date}-{slug}"
    if not note_id:
        raise OawError("retro create requires a non-empty --id")
    relpath = Path("Agents/Retrospectives") / f"{date} {slug.replace('-', ' ')}.md"
    path = root / relpath
    conflicts = [
        match
        for candidate in iter_markdown(root)
        if (match := note_match(candidate, root, note_id)) and match.path != path
    ]
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    if path.exists() and not args.force:
        raise OawError(f"retrospective already exists: {relpath.as_posix()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = args.summary.strip() if args.summary else ""
    summary_block = summary or "_Draft summary._"
    provider_value = provider.lower().replace(" ", "-")
    text = f"""---
type: retrospective
status: draft
date: {date}
created: {date}
provider: {provider_value}
session-ids:
  - {session_id}
id: {note_id}
aliases:
  - {note_id}
tags:
  - agents
  - retrospective
---

# {date} - {title}

## Summary

{summary_block}

## Observations

## Decisions

## Follow-ups

## Artifacts

## Agent sessions

- {date} - {provider} - `{session_ref}` - Created retrospective draft.
"""
    path.write_text(text, encoding="utf-8")
    print(f"Created: {relpath.as_posix()}")
    print(f"ID: {note_id}")


def frontmatter_bool(data: dict[str, object], key: str) -> bool:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def frontmatter_strings(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def require_safe_export(match: NoteMatch, target: str) -> None:
    scope = match.frontmatter.get("export-scope")
    if isinstance(scope, str) and scope.strip().lower() == target.lower():
        return
    export_target = match.frontmatter.get("export_target")
    if (
        frontmatter_bool(match.frontmatter, "safe_for_export")
        and isinstance(export_target, str)
        and export_target == target
    ):
        return
    raise OawError(
        f"export requires export-scope: {target} in note frontmatter "
        f"(legacy safe_for_export: true plus export_target: {target} is also accepted)"
    )


def resolve_export_artifact(root: Path, note_path: Path, raw_value: str) -> Path:
    raw_path = Path(raw_value)
    if raw_path.is_absolute():
        raise OawError(f"export artifact must be vault-relative or note-relative: {raw_value}")
    note_relative = (note_path.parent / raw_path).resolve()
    vault_relative = (root / raw_path).resolve()
    candidates = [note_relative, vault_relative]
    for candidate in candidates:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise OawError(f"export artifact escapes vault: {raw_value}") from exc
        if candidate.is_file():
            return candidate
    raise OawError(f"export artifact not found: {raw_value}")


def export_note_text(match: NoteMatch, target: str) -> str:
    text, _, body, _ = read_note(match.path)
    frontmatter_block, _, _ = split_note(text)
    relpath = match.relpath
    banner = (
        "> [!IMPORTANT]\n"
        f"> This note was intentionally exported for `{target}` with "
        f"`export-scope: {target}`. Return edits or results through the export bundle, "
        "not by pasting private vault paths.\n"
        f"> Source: `{relpath}`" + (f" (`{match.note_id}`)" if match.note_id else "") + "\n\n"
    )
    if frontmatter_block:
        return f"{frontmatter_block}\n{banner}{body.lstrip()}"
    return f"{banner}{text.lstrip()}"


def export_bundle_name(match: NoteMatch) -> str:
    raw = match.note_id or match.title
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-")
    if not name:
        raise OawError("could not derive a safe export bundle name")
    return name


def write_export_bundle(args: argparse.Namespace) -> None:
    root = vault_root()
    target = args.target.strip()
    if not target:
        raise OawError("export target must not be empty")
    match = resolve_id(args.id, root)
    require_safe_export(match, target)
    output_root = (
        args.output_root.expanduser() if args.output_root else DEFAULT_EXPORT_ROOT.expanduser()
    )
    output_root.mkdir(parents=True, exist_ok=True)
    bundle_name = export_bundle_name(match)
    bundle = output_root / bundle_name
    if bundle.exists():
        if not args.force:
            raise OawError(f"export bundle already exists: {bundle}")
        if not bundle.is_dir():
            raise OawError(f"export bundle path is not a directory: {bundle}")
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_name}.tmp-", dir=output_root))
    try:
        note_path = staging / "note.md"
        note_path.write_text(export_note_text(match, target), encoding="utf-8")

        artifact_entries = []
        for raw_artifact in frontmatter_strings(match.frontmatter, "export_artifacts"):
            source = resolve_export_artifact(root, match.path, raw_artifact)
            rel_source = source.relative_to(root).as_posix()
            destination = staging / "artifacts" / rel_source
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            artifact_entries.append(
                {
                    "source_path": rel_source,
                    "path": destination.relative_to(staging).as_posix(),
                    "sha256": sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                }
            )

        manifest = {
            "schema": "oaw-safe-export-v1",
            "target": target,
            "exported_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
            "source": {
                "id": match.note_id,
                "path": match.relpath,
                "title": match.title,
            },
            "note": {
                "path": note_path.relative_to(staging).as_posix(),
                "sha256": sha256_file(note_path),
                "size_bytes": note_path.stat().st_size,
            },
            "return_ingest": frontmatter_bool(match.frontmatter, "return_ingest"),
            "artifacts": artifact_entries,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if bundle.exists():
            shutil.rmtree(bundle)
        staging.rename(bundle)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Export: {bundle}")
    print(f"Manifest: {bundle / 'manifest.json'}")
    print(f"Artifacts: {len(artifact_entries)}")


def manifest_bundle_path(bundle: Path, raw_path: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute():
        raise OawError(f"manifest path must be bundle-relative: {raw_path}")
    candidate = (bundle / relative).resolve()
    if not candidate.is_relative_to(bundle):
        raise OawError(f"manifest path escapes bundle: {raw_path}")
    return candidate


def validate_export_bundle(args: argparse.Namespace) -> None:
    bundle = args.bundle.expanduser().resolve()
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        raise OawError(f"manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OawError(f"manifest is not valid JSON: {manifest_path}") from exc
    if manifest.get("schema") != "oaw-safe-export-v1":
        raise OawError("manifest schema is not oaw-safe-export-v1")
    target = args.target or manifest.get("target")
    if not isinstance(target, str) or not target:
        raise OawError("manifest target is missing")
    if manifest.get("target") != target:
        raise OawError(f"manifest target does not match {target}")
    source = manifest.get("source")
    source_path = Path(str(source.get("path", ""))) if isinstance(source, dict) else Path()
    if not isinstance(source, dict) or source_path.is_absolute() or ".." in source_path.parts:
        raise OawError("manifest source path must be vault-relative")

    note = manifest.get("note")
    if not isinstance(note, dict) or not isinstance(note.get("path"), str):
        raise OawError("manifest note entry is missing")
    note_path = manifest_bundle_path(bundle, note["path"])
    if not note_path.is_file():
        raise OawError(f"exported note not found: {note_path}")
    if note.get("sha256") != sha256_file(note_path):
        raise OawError("exported note checksum mismatch")
    _, _, _, data = read_note(note_path)
    temp_match = NoteMatch(
        path=note_path,
        relpath=note["path"],
        note_id=str(source.get("id")) if source.get("id") else None,
        matched_by="export",
        title=str(source.get("title", note_path.stem)),
        frontmatter_text="",
        frontmatter=data,
    )
    require_safe_export(temp_match, target)

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise OawError("manifest artifacts must be a list")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise OawError("manifest artifact entry is invalid")
        artifact_path = manifest_bundle_path(bundle, artifact["path"])
        if not artifact_path.is_file():
            raise OawError(f"artifact not found: {artifact_path}")
        if artifact.get("sha256") != sha256_file(artifact_path):
            raise OawError(f"artifact checksum mismatch: {artifact['path']}")

    print("Export: valid")
    print(f"Target: {target}")
    print(f"Artifacts: {len(artifacts)}")


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes)


def iter_all_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def snapshot_tree_copies(root: Path, destination: Path, category: str) -> list[SnapshotCopy]:
    copies: list[SnapshotCopy] = []
    for path in iter_all_files(root):
        copies.append(SnapshotCopy(path, destination / path.relative_to(root), category))
    return copies


def read_text_lossy(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def first_timestamp_date(path: Path) -> str | None:
    timestamp = re.compile(r'"timestamp"\s*:\s*"(\d{4}-\d{2}-\d{2})T')
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if match := timestamp.search(line):
                    return match.group(1)
    except OSError:
        return None
    return None


def find_claude_parent(session_id: str, claude_root: Path) -> Path:
    if not claude_root.exists():
        raise OawError(f"Claude projects root not found: {claude_root}")
    matches = sorted(claude_root.rglob(f"{session_id}.jsonl"))
    if not matches:
        raise OawError(f"Claude parent transcript not found for session {session_id}")
    if len(matches) > 1:
        paths = "\n".join(f"  {path}" for path in matches)
        raise OawError(f"session {session_id} is not unique under {claude_root}:\n{paths}")
    return matches[0]


def snapshot_date(parent: Path, override: str | None) -> str:
    if override:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", override):
            raise OawError("--date must use YYYY-MM-DD")
        return override
    return first_timestamp_date(parent) or _dt.date.today().isoformat()


def detect_parent_completeness(session_id: str, args: argparse.Namespace) -> str:
    if args.partial:
        return "partial"
    if args.complete:
        return "complete"
    env_values = {value for _, env_name in SESSION_ENV if (value := os.environ.get(env_name))}
    return "partial" if session_id in env_values else "complete"


def transcript_text(paths: list[Path]) -> str:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(read_text_lossy(path))
        except OSError:
            continue
    return "\n".join(chunks)


def referenced_codex_threads(text: str, explicit: list[str]) -> set[str]:
    thread_ref = re.compile(
        r"\b(?:CODEX_THREAD_ID|codex[_ -]?thread|codex[_ -]?session)"
        r'["`:\s=]+'
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    threads = {thread.strip() for thread in explicit if thread.strip()}
    for match in thread_ref.finditer(text):
        threads.add(match.group(1))
    return threads


def referenced_claude_sessions(
    text: str,
    explicit: list[str],
    current_session_id: str,
) -> set[str]:
    session_ref = re.compile(
        r"\b(?:CLAUDE_SESSION_ID|CLAUDE_CODE_SESSION_ID|claude[_ -]?session|"
        r"fork(?:ed)?[_ -]?(?:claude[_ -]?)?session|btw[_ -]?session)"
        r'["`:\s=]+'
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    sessions = {session.strip() for session in explicit if session.strip()}
    for match in session_ref.finditer(text):
        sessions.add(match.group(1))
    sessions.discard(current_session_id)
    return sessions


def find_extra_claude_parents(
    claude_root: Path,
    transcript: str,
    explicit_sessions: list[str],
    current_session_id: str,
) -> list[Path]:
    explicit = {session.strip() for session in explicit_sessions if session.strip()}
    sessions = referenced_claude_sessions(transcript, list(explicit), current_session_id)
    parents: list[Path] = []
    seen: set[Path] = set()
    for session_id in sorted(sessions):
        try:
            parent = find_claude_parent(session_id, claude_root)
        except OawError:
            if session_id in explicit:
                raise
            continue
        if parent not in seen:
            seen.add(parent)
            parents.append(parent)
    return parents


def find_codex_rollouts(
    codex_root: Path,
    transcript: str,
    explicit_threads: list[str],
    explicit_rollouts: list[str],
    grep_patterns: list[str],
) -> list[Path]:
    if not codex_root.exists():
        return []
    matches: set[Path] = set()
    for rollout in explicit_rollouts:
        value = rollout.strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        rollout_matches = [candidate] if candidate.is_file() else sorted(codex_root.rglob(value))
        if not rollout_matches:
            raise OawError(f"Codex rollout not found: {value}")
        if len(rollout_matches) > 1:
            paths = "\n".join(f"  {path}" for path in rollout_matches)
            raise OawError(f"Codex rollout '{value}' is not unique:\n{paths}")
        matches.add(rollout_matches[0])
    for thread_id in referenced_codex_threads(transcript, explicit_threads):
        matches.update(codex_root.rglob(f"*{thread_id}*.jsonl"))
    for pattern in grep_patterns:
        if not pattern:
            continue
        pattern_matches: list[Path] = []
        for path in iter_files(codex_root, (".jsonl",)):
            try:
                if pattern in read_text_lossy(path):
                    pattern_matches.append(path)
            except OSError:
                continue
        if len(pattern_matches) > 1:
            paths = "\n".join(f"  {path}" for path in pattern_matches)
            raise OawError(
                f"--grep {pattern!r} matched multiple Codex rollouts; "
                f"rerun with --codex-thread or an exact rollout filename:\n{paths}"
            )
        matches.update(pattern_matches)
    return sorted(matches)


def discover_codex_rollouts(
    codex_root: Path,
    scan_paths: list[Path],
    seed_rollouts: list[Path],
    explicit_threads: list[str],
    explicit_rollouts: list[str],
    grep_patterns: list[str],
) -> tuple[list[Path], str]:
    """Expand referenced Codex rollouts until no new lineage is discovered."""
    discovered = set(seed_rollouts)
    while True:
        text = transcript_text([*scan_paths, *sorted(discovered)])
        matches = set(
            find_codex_rollouts(
                codex_root,
                text,
                explicit_threads,
                explicit_rollouts,
                grep_patterns,
            )
        )
        new_matches = matches - discovered
        if not new_matches:
            return sorted(discovered), text
        discovered.update(new_matches)


def referenced_plugin_jobs(text: str) -> set[str]:
    return set(re.findall(r"\btask-[a-z0-9]+-[a-z0-9]+\b", text))


def find_plugin_job_files(plugin_root: Path, transcript: str) -> list[Path]:
    if not plugin_root.exists():
        return []
    jobs = referenced_plugin_jobs(transcript)
    if not jobs:
        return []
    matches: set[Path] = set()
    for path in iter_files(plugin_root, (".json", ".log")):
        if path.stem in jobs:
            matches.add(path)
    return sorted(matches)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_snapshot_files(dest_root: Path, copies: list[SnapshotCopy]) -> list[dict[str, object]]:
    copied_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    entries: list[dict[str, object]] = []
    for item in copies:
        destination = dest_root / item.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, destination)
        entries.append(
            {
                "category": item.category,
                "source": str(item.source),
                "destination": item.destination.as_posix(),
                "copied_at": copied_at,
                "completeness": item.completeness,
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    return entries


def remove_stale_snapshot_files(dest_root: Path, current_entries: list[dict[str, object]]) -> None:
    manifest_path = dest_root / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    current = {
        entry["destination"]
        for entry in current_entries
        if isinstance(entry.get("destination"), str)
    }
    for entry in previous.get("files", []):
        destination = entry.get("destination") if isinstance(entry, dict) else None
        if not isinstance(destination, str) or destination in current:
            continue
        stale_path = (dest_root / destination).resolve()
        try:
            stale_path.relative_to(dest_root.resolve())
        except ValueError:
            continue
        if stale_path.is_file():
            stale_path.unlink()


def write_snapshot_manifest(
    dest_root: Path,
    session_id: str,
    parent: Path | None,
    date: str,
    slug: str,
    parent_completeness: str,
    files: list[dict[str, object]],
    mode: str = "claude-parent",
) -> Path:
    manifest = {
        "schema": "oaw-session-snapshot-v1",
        "session_id": session_id,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "snapshot": {
            "date": date,
            "slug": slug,
            "path": str(dest_root),
            "mode": mode,
            "parent_transcript": str(parent) if parent else None,
            "parent_completeness": parent_completeness,
        },
        "files": files,
    }
    path = dest_root / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def session_snapshot(args: argparse.Namespace) -> None:
    session_id = args.session_id.strip()
    if not session_id:
        raise OawError("empty session ID")
    parent = (
        None if args.codex_only else find_claude_parent(session_id, args.claude_root.expanduser())
    )
    explicit_threads = args.codex_thread or []
    primary_codex_rollouts: list[Path] = []
    if args.codex_only:
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            session_id,
            re.IGNORECASE,
        ):
            raise OawError("--codex-only requires a full Codex thread UUID")
        session_id = session_id.lower()
        primary_codex_rollouts = find_codex_rollouts(
            args.codex_root.expanduser(), "", [session_id], [], []
        )
        if not primary_codex_rollouts:
            raise OawError(f"Codex rollout not found for thread {session_id}")
        explicit_threads = [session_id, *explicit_threads]
    codex_rollouts = find_codex_rollouts(
        args.codex_root.expanduser(),
        "",
        explicit_threads,
        args.codex_rollout or [],
        args.grep or [],
    )
    date = snapshot_date(parent or primary_codex_rollouts[0], args.date)
    slug = slugify(args.slug or session_id[:8])
    output_root = (
        args.output_root.expanduser() if args.output_root else vault_root() / RETRO_ATTACHMENTS
    )
    destination_root = output_root / f"{date}-{slug}"
    destination_root.mkdir(parents=True, exist_ok=True)

    subagents_dir: Path | None = None
    subagents: list[Path] = []
    task_outputs: list[SnapshotCopy] = []
    workflow_artifacts: list[SnapshotCopy] = []
    workflow_scripts: list[SnapshotCopy] = []
    if parent:
        session_dir = parent.with_suffix("")
        subagents_dir = session_dir / "subagents"
        workflow_root = subagents_dir / "workflows"
        subagents = [
            path
            for path in iter_files(subagents_dir, (".jsonl",))
            if not path.is_relative_to(workflow_root)
        ]
        task_outputs = snapshot_tree_copies(
            session_dir / "tasks",
            Path("claude") / "tasks",
            "claude-task-output",
        )
        workflow_artifacts = snapshot_tree_copies(
            subagents_dir / "workflows",
            Path("claude") / "workflows",
            "claude-workflow-artifact",
        )
        workflow_scripts = snapshot_tree_copies(
            session_dir / "workflows" / "scripts",
            Path("claude") / "workflow-scripts",
            "claude-workflow-script",
        )
    scan_paths = [
        *([parent] if parent else []),
        *subagents,
        *(copy.source for copy in task_outputs),
        *(copy.source for copy in workflow_artifacts),
        *(copy.source for copy in workflow_scripts),
    ]
    codex_rollouts, text = discover_codex_rollouts(
        args.codex_root.expanduser(),
        scan_paths,
        codex_rollouts,
        explicit_threads,
        args.codex_rollout or [],
        args.grep or [],
    )
    parent_completeness = detect_parent_completeness(session_id, args)
    extra_claude_parents = find_extra_claude_parents(
        args.claude_root.expanduser(),
        text,
        args.claude_session or [],
        session_id,
    )
    if extra_claude_parents:
        scan_paths.extend(extra_claude_parents)
        codex_rollouts, text = discover_codex_rollouts(
            args.codex_root.expanduser(),
            scan_paths,
            codex_rollouts,
            explicit_threads,
            args.codex_rollout or [],
            args.grep or [],
        )

    copies: list[SnapshotCopy] = []
    if parent:
        parent_name = f"parent-{session_id[:8]}"
        if parent_completeness == "partial":
            parent_name += "-PARTIAL"
        copies.append(
            SnapshotCopy(
                parent,
                Path("claude") / f"{parent_name}.jsonl",
                "claude-parent",
                parent_completeness,
            )
        )
    for path in subagents:
        assert subagents_dir is not None
        relative = path.relative_to(subagents_dir)
        destination = (
            Path("claude") / path.name
            if len(relative.parts) == 1
            else Path("claude") / "subagents" / relative
        )
        copies.append(SnapshotCopy(path, destination, "claude-subagent"))
    copies.extend(task_outputs)
    copies.extend(workflow_artifacts)
    copies.extend(workflow_scripts)
    copies.extend(
        SnapshotCopy(
            path,
            Path("claude") / "forks" / f"parent-{path.stem[:8]}.jsonl",
            "claude-fork-parent",
        )
        for path in extra_claude_parents
    )
    copies.extend(
        SnapshotCopy(
            path,
            Path("codex") / path.name,
            "codex-rollout",
            parent_completeness if args.codex_only else "complete",
        )
        for path in codex_rollouts
    )
    copies.extend(
        SnapshotCopy(path, Path("plugin-logs") / path.name, "plugin-job")
        for path in find_plugin_job_files(args.plugin_data_root.expanduser(), text)
    )

    seen_destinations: set[Path] = set()
    unique_copies: list[SnapshotCopy] = []
    for item in copies:
        if item.destination in seen_destinations:
            continue
        seen_destinations.add(item.destination)
        unique_copies.append(item)

    entries = copy_snapshot_files(destination_root, unique_copies)
    remove_stale_snapshot_files(destination_root, entries)
    manifest_path = write_snapshot_manifest(
        destination_root,
        session_id,
        parent,
        date,
        slug,
        parent_completeness,
        entries,
        "codex-only" if args.codex_only else "claude-parent",
    )

    print(f"Snapshot: {destination_root}")
    print(f"Manifest: {manifest_path}")
    print(f"Copied: {len(entries)}")
    print(f"Transcript: {parent_completeness}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oaw")
    sub = parser.add_subparsers(dest="command", required=True)

    resolve = sub.add_parser("resolve", help="resolve obs:<ID> or <ID>")
    resolve.add_argument("id")
    resolve.add_argument("--full", action="store_true")
    resolve.add_argument("--path", action="store_true")
    resolve.add_argument("--meta", action="store_true")
    resolve.add_argument("--outline", action="store_true")
    resolve.add_argument("--json", action="store_true")

    list_cmd = sub.add_parser("list", help="list project notes")
    list_cmd.add_argument("--project", required=True)
    list_cmd.add_argument("--type", default="task", help="frontmatter type to list, default: task")
    list_cmd.add_argument("--status", help="optional frontmatter status filter")
    list_cmd.add_argument(
        "--include-archived",
        action="store_true",
        help="include status: archived notes when no --status is set",
    )

    project = sub.add_parser("project", help="project workspace lifecycle")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_create = project_sub.add_parser(
        "create", help="create a project Index.md from the vault template"
    )
    project_create.add_argument("--name", required=True, help="safe project folder name")
    project_create.add_argument("--alias", required=True, help="uppercase 2-8 character alias")
    project_create.add_argument("--goal", required=True, help="single-line project outcome")
    project_create.add_argument("--repo", help="optional single-line repository path or URL")
    project_create.add_argument("--tag", action="append", help="extra project tag; repeatable")
    project_create.add_argument(
        "--template",
        default=PROJECT_INDEX_TEMPLATE.as_posix(),
        help="vault-relative template path, default: Templates/Small project index.md",
    )
    project_create.add_argument("--allow-missing-session-id", action="store_true")

    research = sub.add_parser("research", help="research packet utilities")
    research_sub = research.add_subparsers(dest="research_command", required=True)
    research_scaffold = research_sub.add_parser(
        "scaffold", help="create Prompt.md and Synthesis.md from the vault research template"
    )
    research_scaffold.add_argument(
        "--project", required=True, help="project alias (obs:OAW) or folder name under Projects/"
    )
    research_scaffold.add_argument(
        "--track", required=True, help="path below the project's Research/ folder"
    )
    research_scaffold.add_argument("--title", required=True, help="provider-facing topic title")
    research_scaffold.add_argument("--date", help="creation date, default: today (YYYY-MM-DD)")
    research_scaffold.add_argument(
        "--template",
        default=RESEARCH_PACKET_TEMPLATE.as_posix(),
        help="vault-relative template path, default: Templates/Research packet.md",
    )
    research_scaffold.add_argument(
        "--force", action="store_true", help="replace Prompt.md; never replace Synthesis.md"
    )
    research_start = research_sub.add_parser(
        "start", help="register one launched provider run in an existing research packet"
    )
    research_start.add_argument(
        "--project", required=True, help="project alias (obs:OAW) or folder name under Projects/"
    )
    research_start.add_argument(
        "--track", required=True, help="path below the project's Research/ folder"
    )
    research_start.add_argument("--source", required=True, help="safe human source label")
    research_start.add_argument("--url", required=True, help="launched run's HTTP(S) URL")

    task = sub.add_parser("task", help="project task lifecycle")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    for name, status in (
        ("backlog", "backlog"),
        ("promote", "todo"),
        ("start", "active"),
        ("complete", "done"),
    ):
        cmd = task_sub.add_parser(name)
        cmd.set_defaults(status=status)
        cmd.add_argument("id")
        cmd.add_argument("--note", required=True)
        cmd.add_argument("--checks")
        cmd.add_argument("--allow-missing-session-id", action="store_true")
    task_note = task_sub.add_parser(
        "note", help="append an agent session note without changing status"
    )
    task_note.add_argument("id")
    task_note.add_argument("--note", required=True)
    task_note.add_argument("--checks")
    task_note.add_argument("--allow-missing-session-id", action="store_true")

    task_create = task_sub.add_parser("create", help="create a new project task note")
    task_create.add_argument(
        "--project", help="project alias (obs:OAW) or folder name under Projects/"
    )
    task_create.add_argument("--title", help="task title; defaults to capture title")
    task_create.add_argument("--from-capture", help="CAP note ID to promote atomically")
    create_intent = task_create.add_mutually_exclusive_group()
    create_intent.add_argument(
        "--start", action="store_true", help="create promoted task directly as active"
    )
    task_create.add_argument("--id", help="task ID; derived as <ALIAS>-TSK-<slug> when omitted")
    create_intent.add_argument("--status", choices=("backlog", "todo"), default="backlog")
    task_create.add_argument("--priority", type=int, choices=(1, 2, 3))
    task_create.add_argument("--effort", choices=("S", "M", "L"))
    task_create.add_argument("--note", help="initial problem statement")
    task_create.add_argument("--tag", action="append", help="extra tag; repeatable")
    task_create.add_argument("--allow-missing-session-id", action="store_true")

    note = sub.add_parser("note", help="append session traces or observations to resolved notes")
    note_sub = note.add_subparsers(dest="note_command", required=True)

    note_session = note_sub.add_parser("session", help="append an Agent sessions entry")
    note_session.add_argument("id")
    note_session.add_argument("--note", required=True)
    note_session.add_argument("--checks")
    note_session.add_argument("--allow-missing-session-id", action="store_true")

    observe = note_sub.add_parser("observe", help="append a dated observation block")
    observe.add_argument("id")
    observe.add_argument(
        "--section", default="Observations", help="target heading, default: Observations"
    )
    observe.add_argument("--title", required=True)
    observe.add_argument("--body", required=True)

    board = sub.add_parser("board", help="update the cross-project Next steps board")
    board_sub = board.add_subparsers(dest="board_command", required=True)

    add = board_sub.add_parser("add", help="add a linked card to Projects/Next steps.md")
    add.add_argument("--column", required=True)
    add.add_argument("--link", required=True, help="vault-relative note path, with or without .md")
    add.add_argument("--title", required=True)
    add.add_argument("--why", required=True, help="one-line reason or routing note")
    add.add_argument("--id", required=True, help="stable reference ID shown at the end of the card")

    move = board_sub.add_parser("move", help="move a matching card to another column")
    move.add_argument("token", help="stable ID or unique text contained in the card")
    move.add_argument("--column", required=True)

    done = board_sub.add_parser("done", help="move a matching card to Done and check it")
    done.add_argument("token", help="stable ID or unique text contained in the card")

    ensure_backlog = board_sub.add_parser(
        "ensure-backlog",
        help="add a Backlog column to a project board if missing",
    )
    ensure_backlog.add_argument("--project", required=True)

    ingest = sub.add_parser("ingest", help="ingest approved handoff files")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    safe_export = ingest_sub.add_parser(
        "safe-export",
        help="ingest frontmatter-approved Markdown files",
    )
    safe_export.add_argument(
        "--ingestion-root",
        type=Path,
        default=default_ingestion_root(),
        help="handoff folder to scan, default: OAW_INGESTION_ROOT or ~/obsidian-ingestion",
    )
    safe_export.add_argument(
        "--destination",
        default=SAFE_EXPORT_DESTINATION.as_posix(),
        help="vault-relative destination folder",
    )
    safe_export_mode = safe_export.add_mutually_exclusive_group()
    safe_export_mode.add_argument(
        "--dry-run",
        action="store_const",
        const="dry-run",
        dest="mode",
        help="preview actions without moving files",
    )
    safe_export_mode.add_argument(
        "--write",
        action="store_const",
        const="write",
        dest="mode",
        help="ingest safe files and quarantine rejected files",
    )
    safe_export.set_defaults(mode="dry-run")

    link = sub.add_parser("link", help="inspect and maintain durable wikilinks")
    link_sub = link.add_subparsers(dest="link_command", required=True)

    link_check_cmd = link_sub.add_parser("check", help="check whether two notes link to each other")
    link_check_cmd.add_argument("left")
    link_check_cmd.add_argument("right")

    link_list_cmd = link_sub.add_parser("list", help="list explicit wikilinks from a note")
    link_list_cmd.add_argument("note")

    link_ensure_cmd = link_sub.add_parser("ensure", help="ensure one durable wikilink exists")
    link_ensure_cmd.add_argument("source")
    link_ensure_cmd.add_argument("target")
    link_ensure_cmd.add_argument("--section", default="Related")
    link_ensure_cmd.add_argument("--label")
    link_ensure_mode = link_ensure_cmd.add_mutually_exclusive_group()
    link_ensure_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only (default)",
    )
    link_ensure_mode.add_argument("--write", action="store_true", help="write the edit")

    link_bidir = link_sub.add_parser(
        "ensure-bidirectional",
        help="ensure durable links in both directions",
    )
    link_bidir.add_argument("left")
    link_bidir.add_argument("right")
    link_bidir.add_argument("--section", default="Related")
    link_bidir_mode = link_bidir.add_mutually_exclusive_group()
    link_bidir_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="preview only (default)",
    )
    link_bidir_mode.add_argument("--write", action="store_true", help="write the edits")

    link_sub.add_parser("lint", help="suggest durable replacements for opaque ID links")

    export = sub.add_parser("export", help="safe outbound note export utilities")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    export_note = export_sub.add_parser("note", help="export a marked-safe note bundle")
    export_note.add_argument("id")
    export_note.add_argument("--target", default="work", help="required export_target value")
    export_note.add_argument("--output-root", type=Path, help="default: ~/obsidian-export")
    export_note.add_argument("--force", action="store_true", help="replace an existing bundle")

    export_validate = export_sub.add_parser("validate", help="validate an exported bundle")
    export_validate.add_argument("bundle", type=Path)
    export_validate.add_argument("--target", help="expected target, default: manifest target")

    session = sub.add_parser("session", help="session artifact utilities")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    lookup = session_sub.add_parser("lookup", help="find notes or artifacts for a session ID")
    lookup.add_argument("session_id")
    lookup.add_argument(
        "--verbose",
        action="store_true",
        help="show timestamps, duration, message turn counts, and cumulative token totals",
    )
    lookup.add_argument(
        "--codex-root",
        type=Path,
        default=session_lookup_codex_root(),
        help="override Codex sessions root",
    )
    lookup.add_argument(
        "--claude-root",
        type=Path,
        default=session_lookup_claude_root(),
        help="override Claude projects root",
    )
    snapshot = session_sub.add_parser("snapshot", help="copy session artifacts for retrospectives")
    snapshot.add_argument("session_id")
    snapshot.add_argument("--slug", help="snapshot folder suffix, default: session ID prefix")
    snapshot.add_argument("--date", help="snapshot date prefix, default: first transcript date")
    snapshot.add_argument(
        "--partial",
        action="store_true",
        help="mark the session transcript as partial even if it is not current",
    )
    snapshot.add_argument(
        "--complete",
        action="store_true",
        help="mark the session transcript as complete even if it is current",
    )
    snapshot.add_argument(
        "--codex-only",
        action="store_true",
        help="snapshot a Codex-only thread without requiring a Claude parent transcript",
    )
    snapshot.add_argument(
        "--codex-thread",
        action="append",
        help="Codex thread ID to copy from ~/.codex/sessions; may be repeated",
    )
    snapshot.add_argument(
        "--codex-rollout",
        action="append",
        help="exact Codex rollout filename or path to copy; may be repeated",
    )
    snapshot.add_argument(
        "--claude-session",
        action="append",
        help="extra Claude session ID, such as a fork parent, to copy; may be repeated",
    )
    snapshot.add_argument(
        "--grep",
        action="append",
        help="literal text to search in Codex rollouts when thread IDs are unavailable",
    )
    snapshot.add_argument("--output-root", type=Path, help="override attachments output root")
    snapshot.add_argument(
        "--claude-root",
        type=Path,
        default=default_claude_projects_root(),
        help="override Claude projects root",
    )
    snapshot.add_argument(
        "--codex-root",
        type=Path,
        default=default_codex_sessions_root(),
        help="override Codex sessions root",
    )
    snapshot.add_argument(
        "--plugin-data-root",
        type=Path,
        default=default_plugin_data_root(),
        help="override Claude plugin data root",
    )

    retro = sub.add_parser("retro", help="retrospective note utilities")
    retro_sub = retro.add_subparsers(dest="retro_command", required=True)
    retro_create = retro_sub.add_parser("create", help="create a dated retrospective draft")
    retro_create.add_argument("--title", required=True)
    retro_create.add_argument("--summary", default="")
    retro_create.add_argument("--date", help="date prefix, default: today")
    retro_create.add_argument("--id", help="override generated AGT-RETRO-* id")
    retro_create.add_argument("--force", action="store_true", help="overwrite an existing note")
    retro_create.add_argument("--allow-missing-session-id", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            output_resolve(resolve_id(args.id, vault_root()), args)
        elif args.command == "list":
            list_project(args.project, args.type, args.status, args.include_archived)
        elif args.command == "project":
            if args.project_command == "create":
                create_project(args)
            else:
                parser.error("unknown project command")
        elif args.command == "research":
            if args.research_command == "scaffold":
                create_research_packet(args)
            elif args.research_command == "start":
                start_research_run(args)
            else:
                parser.error("unknown research command")
        elif args.command == "task":
            if args.task_command == "note":
                append_task_note(
                    args.id,
                    args.note,
                    args.checks,
                    args.allow_missing_session_id,
                )
            elif args.task_command == "create":
                create_task(args)
            else:
                update_task(
                    args.id,
                    args.status,
                    args.note,
                    args.checks,
                    args.allow_missing_session_id,
                )
        elif args.command == "note":
            if args.note_command == "session":
                update_note_session(
                    args.id,
                    args.note,
                    args.checks,
                    args.allow_missing_session_id,
                )
            elif args.note_command == "observe":
                update_note_observation(args.id, args.section, args.title, args.body)
            else:
                parser.error("unknown note command")
        elif args.command == "board":
            if args.board_command == "add":
                update_next_board(
                    args.column,
                    None,
                    board_card(args.link, args.title, args.why, args.id),
                    False,
                )
            elif args.board_command == "move":
                update_next_board(args.column, args.token, None, False)
            elif args.board_command == "done":
                update_next_board("Done", args.token, None, True)
            elif args.board_command == "ensure-backlog":
                ensure_project_backlog_column(args.project)
        elif args.command == "ingest":
            if args.ingest_command == "safe-export":
                safe_export_ingest(args)
            else:
                parser.error("unknown ingest command")
        elif args.command == "link":
            if args.link_command == "check":
                link_check(args)
            elif args.link_command == "list":
                link_list(args)
            elif args.link_command == "ensure":
                link_ensure(args)
            elif args.link_command == "ensure-bidirectional":
                link_ensure_bidirectional(args)
            elif args.link_command == "lint":
                link_lint(args)
        elif args.command == "export":
            if args.export_command == "note":
                write_export_bundle(args)
            elif args.export_command == "validate":
                validate_export_bundle(args)
            else:
                parser.error("unknown export command")
        elif args.command == "session":
            if args.session_command == "lookup":
                session_lookup(args)
            elif args.session_command == "snapshot":
                if args.partial and args.complete:
                    raise OawError("--partial and --complete are mutually exclusive")
                session_snapshot(args)
            else:
                parser.error("unknown session command")
        elif args.command == "retro":
            if args.retro_command == "create":
                create_retrospective(args)
            else:
                parser.error("unknown retro command")
        else:
            parser.error("unknown command")
    except OawError as exc:
        print(f"oaw: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
