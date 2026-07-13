"""Task lifecycle writes and agent-session note traces."""

from __future__ import annotations

import datetime as dt
import os
import re
import unicodedata
from pathlib import Path

from .boards import move_project_board_card, updated_project_board_text
from .errors import OawError
from .frontmatter import append_frontmatter_list_value, set_frontmatter_scalar
from .notes import VaultTransaction, append_markdown_block_to_section
from .resolver import NoteMatch, iter_markdown, note_match, resolve_project_root

SESSION_ENV = (
    ("Codex", "CODEX_THREAD_ID"),
    ("Claude Code", "CLAUDE_SESSION_ID"),
    ("Claude Code", "CLAUDE_CODE_SESSION_ID"),
    ("OpenCode", "OPENCODE_SESSION_ID"),
    ("Gemini", "GEMINI_SESSION_ID"),
)


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
    capture: NoteMatch | None,
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
    project_root, alias = resolve_project_root(raw_project, root)
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
