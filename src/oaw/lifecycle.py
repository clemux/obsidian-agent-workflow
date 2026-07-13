"""Task lifecycle writes and agent-session note traces."""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path

from .boards import move_project_board_card, updated_project_board_text
from .errors import OawError
from .frontmatter import append_frontmatter_list_value, parse_frontmatter, set_frontmatter_scalar
from .notes import VaultTransaction, append_markdown_block_to_section, split_note
from .resolver import (
    NoteMatch,
    iter_markdown,
    matches_from_references,
    note_match,
    resolve_id_from_references,
    resolve_project_root,
    resolve_project_root_from_references,
    scan_note_references,
)
from .sessions import detect_session

RESEARCH_PACKET_TEMPLATE = Path("Templates/Research packet.md")
PROJECT_INDEX_TEMPLATE = Path("Templates/Small project index.md")
DEEP_RESEARCH_HEADING = "## Deep research prompt"
RUNNING_RESEARCH_HEADING = "## Running research sessions"
RESEARCH_PACKET_BASE = Path("Bases/Research packet.base")


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


def append_session_id_frontmatter(text: str, session_ref: str) -> str:
    name, separator, value = session_ref.partition("=")
    if not separator or (name == "session_id" and value == "unavailable"):
        return text
    return append_frontmatter_list_value(text, "session-ids", value)


def append_session_entry(
    text: str, provider: str, session_ref: str, note: str, checks: str | None
) -> str:
    today = dt.date.today().isoformat()
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


def append_note_session(
    match: NoteMatch, note: str, checks: str | None, allow_missing: bool
) -> None:
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    match.path.write_text(text, encoding="utf-8")
    print(f"Updated: {match.relpath}")
    print("Section: Agent sessions")


def update_task(
    match: NoteMatch,
    root: Path,
    status: str,
    note: str,
    checks: str | None,
    allow_missing: bool,
) -> None:
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
    moved = move_project_board_card(
        project_root_for_task(match.path, root),
        match.path,
        match.title,
        match.note_id,
        status,
    )
    print(f"Updated: {match.relpath}")
    print(f"Status: {status}")
    print(f"Board: {'updated' if moved else 'not found'}")


def append_task_note(
    match: NoteMatch, root: Path, note: str, checks: str | None, allow_missing: bool
) -> None:
    if not is_project_task(match.path, root):
        raise OawError("lifecycle writes are only supported for Projects/*/Tasks notes in v1")
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    match.path.write_text(text, encoding="utf-8")
    status = match.frontmatter.get("status", "")
    print(f"Updated: {match.relpath}")
    print(f"Status: {status}")
    print("Board: unchanged")


def _slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "session"


def _durable_wikilink(match: NoteMatch, label: str | None = None) -> str:
    display = (label or match.note_id or match.title).strip()
    target = Path(match.relpath).with_suffix("").as_posix()
    return f"[[{target}|{display}]]"


def _append_to_section(text: str, section: str, line: str) -> str:
    return append_markdown_block_to_section(text, section, f"- {line}")


def create_task(
    root: Path,
    project: str | None,
    title: str | None,
    capture_id: str | None,
    start: bool,
    requested_id: str | None,
    status: str,
    priority: int | None,
    effort: str | None,
    note: str | None,
    tags: list[str] | None,
    allow_missing_session_id: bool,
) -> None:
    """Create a task from explicit CLI values, optionally promoting a capture."""
    references = scan_note_references(root)
    capture = resolve_id_from_references(capture_id, root, references) if capture_id else None
    if capture:
        if capture.frontmatter.get("type") != "capture":
            raise OawError(f"from-capture source is not a capture note: {capture.relpath}")
        if not capture.note_id:
            raise OawError("from-capture source must have a stable frontmatter id")
        if capture.frontmatter.get("status") == "triaged":
            raise OawError(f"capture is already triaged: {capture.note_id}")
    elif start:
        raise OawError("--start is only supported with --from-capture")
    clean_title = (title or (capture.title if capture else "")).strip()
    if not clean_title:
        raise OawError("task create requires a non-empty --title")
    if "/" in clean_title or clean_title.startswith("."):
        raise OawError("task title must not contain '/' or start with '.'")
    raw_project = project
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
    project_root, alias = resolve_project_root_from_references(raw_project, root, references)
    provider, session_ref = detect_session(allow_missing_session_id)
    if requested_id is not None:
        note_id = requested_id.strip()
        if not note_id:
            raise OawError("task create requires a non-empty --id")
    elif alias:
        note_id = f"{alias}-TSK-{_slugify(clean_title)}"
    else:
        raise OawError(
            "cannot derive a task ID: project index has no '<ALIAS>-index' id; pass --id"
        )
    path = project_root / "Tasks" / f"{clean_title}.md"
    relpath = path.relative_to(root)
    conflicts = matches_from_references(note_id, references)
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    if path.exists():
        raise OawError(f"task note already exists: {relpath.as_posix()}")
    today = dt.date.today().isoformat()
    project_slug = _slugify(project_root.name)
    task_status = "active" if start else status
    lines = [
        "---",
        "type: task",
        f"project: {project_slug}",
        f"status: {task_status}",
        f"created: {today}",
    ]
    if priority is not None:
        lines.append(f"priority: {priority}")
    if effort:
        lines.append(f"effort: {effort}")
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
    for tag in tags or []:
        cleaned = tag.strip()
        if cleaned:
            lines.append(f"  - {cleaned}")
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else ""
    if session_id and session_id != "unavailable":
        lines += ["session-ids:", f"  - {session_id}"]
    lines += ["---", "", f"# {clean_title}", "", "## Problem", ""]
    lines.append(note.strip() if note else "_To be defined._")
    lines += ["", "## Related", ""]
    index = project_root / "Index.md"
    if alias and index.exists():
        index_rel = index.relative_to(root).with_suffix("").as_posix()
        lines.append(f"- [[{index_rel}|{alias}-index]]")
        lines.append("")
    if capture:
        lines.append(f"- {_durable_wikilink(capture, capture.note_id)}")
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
        capture_text = _append_to_section(capture_text, "Related", task_link)
        capture_text = append_frontmatter_list_value(capture_text, "destinations", task_link)
        capture_text = set_frontmatter_scalar(capture_text, "status", "triaged")
        board, board_text = updated_project_board_text(
            project_root, path, clean_title, note_id, task_status
        )
        transaction = VaultTransaction()
        transaction.stage(path, task_text)
        if board and board_text:
            transaction.stage(board, board_text)
        transaction.stage(capture.path, capture_text)
        transaction.commit()
        moved = board is not None
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(task_text, encoding="utf-8")
        moved = move_project_board_card(project_root, path, clean_title, note_id, task_status)
    print(f"Created: {relpath.as_posix()}")
    print(f"ID: {note_id}")
    print(f"Status: {task_status}")
    print(f"Board: {'updated' if moved else 'not found'}")
    if capture:
        print(f"Capture: {capture.note_id} -> triaged")


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
        "{{date}}", dt.date.today().isoformat()
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
        _slugify(name), alias, repo, tags, session_ref, preserved
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


def create_project(
    root: Path,
    name: str,
    goal: str,
    alias: str,
    repo: str | None,
    tags_value: list[str] | None,
    template: str,
    allow_missing_session_id: bool,
) -> None:
    root = root
    name = safe_project_name(name)
    clean_goal = single_line_value(goal, "--goal")
    assert clean_goal is not None
    clean_alias = single_line_value(alias, "--alias")
    assert clean_alias is not None
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", clean_alias):
        raise OawError("project create --alias must match [A-Z][A-Z0-9]{1,7}")
    repo = single_line_value(repo, "--repo", required=False)
    extra_tags = [safe_project_tag(tag) for tag in tags_value or []]
    project_slug = _slugify(name)
    tags = list(dict.fromkeys(["projects", project_slug, *extra_tags]))
    template_rel = safe_relative_path(template, "template")
    template_path = root / template_rel
    if not template_path.is_file():
        raise OawError(f"project template not found: {template_rel.as_posix()}")

    project_root = root / "Projects" / name
    destination = project_root / "Index.md"
    if project_root.exists():
        raise OawError(f"project folder already exists: Projects/{name}")
    note_id = f"{clean_alias}-index"
    conflicts = [
        match
        for candidate in iter_markdown(root)
        if (match := note_match(candidate, root, note_id))
    ]
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    provider, session_ref = detect_session(allow_missing_session_id)
    del provider  # The project index records stable session provenance in frontmatter.
    template_text = template_path.read_text(encoding="utf-8")
    rendered = render_project_index(
        template_text, name, clean_alias, clean_goal, repo, tags, session_ref
    )

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


def create_research_packet(
    root: Path,
    project: str,
    track_value: str,
    title_value: str,
    date_value: str | None,
    template: str,
    force: bool,
) -> None:
    root = root
    project_root, _ = resolve_project_root(project, root)
    track = safe_relative_path(track_value, "track")
    title = title_value.strip()
    if not title or "\n" in title or "\r" in title:
        raise OawError("research scaffold requires a non-empty, single-line --title")
    try:
        date = dt.date.fromisoformat(date_value) if date_value else dt.date.today()
    except ValueError as exc:
        raise OawError("--date must use YYYY-MM-DD") from exc
    template_rel = safe_relative_path(template, "template")
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
        "{{project}}": _slugify(project_root.name),
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
    if destination.exists() and not force:
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
                _slugify(project_root.name), track.as_posix(), title, date.isoformat()
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


def start_research_run(
    root: Path, project: str, track_value: str, source_value: str, url_value: str
) -> None:
    root = root
    project_root, _ = resolve_project_root(project, root)
    track = safe_relative_path(track_value, "track")
    source = safe_research_source(source_value)
    url = http_url(url_value)
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
    project = _slugify(project_root.name)
    created = dt.date.today().isoformat()
    entry = f"{source}: [running]({url})"
    updated_prompt = _append_to_section(prompt, RUNNING_RESEARCH_HEADING[3:], entry)
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
