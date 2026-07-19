"""Task lifecycle writes and agent-session note traces."""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path

from .errors import OawError
from .frontmatter import (
    append_frontmatter_list_value,
    parse_frontmatter,
    set_frontmatter_scalar,
    split_inline_comment,
)
from .notes import VaultTransaction, append_markdown_block_to_section, locate_section, split_note
from .relations import blocker_problems, prepare_relation_add, prepare_relation_remove
from .resolver import (
    NoteMatch,
    iter_markdown,
    matches_from_references,
    note_match,
    resolve_id,
    resolve_id_from_references,
    resolve_project_root,
    resolve_project_root_from_references,
    scan_note_references,
    strip_obs_prefix,
)
from .runs import (
    Run,
    audit_runs,
    detect_identity,
    find_run,
    is_stale,
    iter_runs,
    matching_run,
    new_run_text,
    run_id,
    run_path,
    running_others,
    runs_for_task,
    transition_run_text,
    utc_now,
    yaml_quote,
)
from .sessions import detect_session
from .tags import creation_tag_block, creation_tags

RESEARCH_PACKET_TEMPLATE = Path("Templates/Research packet.md")
PROJECT_INDEX_TEMPLATE = Path("Templates/Small project index.md")
DEEP_RESEARCH_HEADING = "## Deep research prompt"
RUNNING_RESEARCH_HEADING = "## Running research sessions"
RESEARCH_PACKET_BASE = Path("Bases/Research packet.base")
TASK_PREPAREDNESS_STATES = ("needs-triage", "needs-design", "prepared")


def is_project_task(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    return len(parts) == 4 and parts[0] == "Projects" and parts[2] == "Tasks"


def is_lifecycle_task(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    return (
        is_project_task(path, root)
        or (len(parts) == 3 and parts[:2] == ("Agents", "Tasks"))
        or (len(parts) == 2 and parts[0] == "Tasks")
    )


def lifecycle_task_error() -> OawError:
    return OawError(
        "lifecycle writes are supported for Projects/*/Tasks, Agents/Tasks, and root Tasks"
    )


def append_session_id_frontmatter(text: str, session_ref: str) -> str:
    name, separator, value = session_ref.partition("=")
    if not separator or (name == "session_id" and value == "unavailable"):
        return text
    return append_frontmatter_list_value(text, "session-ids", value)


_SESSION_ENTRY_LINE = re.compile(r"^- \d{4}-\d{2}-\d{2} - .+ - `[^`]+` - .+$")


def _is_session_entry_line(line: str) -> bool:
    return bool(_SESSION_ENTRY_LINE.fullmatch(line))


def append_session_entry(
    text: str, provider: str, session_ref: str, note: str, checks: str | None
) -> str:
    """Append one dated entry under a task note's ``## Agent sessions`` section.

    Reuses ``notes.locate_section`` for fence-aware, heading-exact section
    lookup (so headings inside fenced or indented code, or an inline
    occurrence of the heading text, are never mistaken for the real
    section). When the section already ends in a line that itself looks
    like a rendered session entry, the new entry is appended directly below
    it with no blank line, keeping the log contiguous; otherwise it is
    separated from whatever precedes it by exactly one blank line, matching
    ``append_markdown_block_to_section``'s general block-append behavior.
    """
    today = dt.date.today().isoformat()
    detail = note.strip()
    if checks:
        detail = f"{detail}; checks: {checks.strip()}"
    entry = f"- {today} - {provider} - `{session_ref}` - {detail}"
    heading = "## Agent sessions"

    located = locate_section(text, heading)
    if located is None:
        return append_markdown_block_to_section(text, heading, entry)

    lines, heading_idx, section_end = located
    section_lines = lines[heading_idx + 1 : section_end]
    last_idx = len(section_lines) - 1
    while last_idx >= 0 and section_lines[last_idx] == "":
        last_idx -= 1
    if last_idx < 0:
        return append_markdown_block_to_section(text, heading, entry)

    trimmed_section = section_lines[: last_idx + 1]
    if _is_session_entry_line(trimmed_section[-1]):
        new_section = [*trimmed_section, entry]
    else:
        new_section = [*trimmed_section, "", entry]

    before = lines[: heading_idx + 1]
    after = lines[section_end:]
    new_lines = [*before, *new_section, ""]
    if after:
        new_lines.extend(after)
    return "\n".join(new_lines).rstrip() + "\n"


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
    if not is_lifecycle_task(match.path, root):
        raise lifecycle_task_error()
    action = {
        "backlog": "backlog",
        "todo": "promote",
        "active": "start",
        "review": "review",
        "done": "complete",
    }[status]
    if not note.strip():
        raise OawError(f"task {action} requires non-empty --note")
    if status in {"review", "done"} and checks is None:
        raise OawError(f"task {action} requires --checks")
    if status in {"review", "done"} and checks is not None and not checks.strip():
        raise OawError(f"task {action} requires non-empty --checks")
    if checks is not None and not checks.strip():
        raise OawError(f"task {action} requires non-empty --checks when provided")
    task_id = str(match.note_id or "")
    if not task_id:
        raise OawError("lifecycle task requires a stable frontmatter id")
    execution = match.frontmatter.get("execution")
    if execution == "human":
        raise OawError("task execution is human; lifecycle is managed in Obsidian UI")
    if execution not in (None, "agent", "hybrid"):
        raise OawError("task execution must be human, agent, or hybrid")

    dependency_problems = (
        blocker_problems(root, match) if status in {"active", "review", "done"} else []
    )
    if status in {"review", "done"} and dependency_problems:
        details = "; ".join(problem.message for problem in dependency_problems)
        raise OawError(f"task {action} refused by blocked-by relationships: {details}")

    run_change: tuple[Path, str] | None = None
    run_identifier: str | None = None

    def resolve_task(candidate: str) -> NoteMatch:
        return resolve_id(candidate, root)

    if status in {"backlog", "todo"}:
        if any(run.state == "running" for run in runs_for_task(root, task_id, resolve_task)):
            raise OawError(f"task {status} refused while an agent run is running")
        provider, session_ref = detect_session(allow_missing)
    else:
        identity = detect_identity()
        provider = identity.provider_label
        session_ref = f"{identity.env}={identity.session_id}"
        now = utc_now()
        current = matching_run(root, task_id, identity, match.path)
        expected_id = run_id(task_id, identity)
        if status == "active":
            if current and current.state not in {"running", "paused", "completed", "closed"}:
                raise OawError(f"run {current.id} has invalid state {current.state}")
            if current and current.state == "completed":
                raise OawError(f"run {current.id} is completed and cannot be reopened")
            if current:
                event = "refresh" if current.state == "running" else "resume"
                run_text = transition_run_text(current, "running", event, now, note)
                run_identifier = current.id
            else:
                run_identifier, run_text = new_run_text(
                    root, match.path, match.frontmatter, identity, now, note=note
                )
            run_change = (run_path(root, run_identifier), run_text)
        elif status in {"review", "done"}:
            if current is None:
                if status == "review":
                    raise OawError("review requires the caller's existing run")
                others = running_others(root, task_id, expected_id, resolve_task)
                if others:
                    raise OawError(
                        "transition refused while another session remains running: "
                        + ", ".join(run.id for run in others)
                    )
                run_identifier, run_text = new_run_text(
                    root,
                    match.path,
                    match.frontmatter,
                    identity,
                    now,
                    state="completed",
                    event="completion",
                    note=note,
                    checks=checks,
                )
            else:
                run_identifier = current.id
                if current.state != "running":
                    raise OawError(f"run {run_identifier} is {current.state}; expected running")
                others = running_others(root, task_id, run_identifier, resolve_task)
                if others:
                    raise OawError(
                        "transition refused while another session remains running: "
                        + ", ".join(run.id for run in others)
                    )
                run_text = transition_run_text(
                    current,
                    "closed" if status == "review" else "completed",
                    "review handoff" if status == "review" else "completion",
                    now,
                    note,
                    checks,
                    "review" if status == "review" else "completed",
                )
            run_change = (run_path(root, run_identifier), run_text)
        else:
            raise OawError(f"unsupported lifecycle status: {status}")

    text = match.path.read_text(encoding="utf-8")
    if status == "active" and execution is None:
        text = set_frontmatter_scalar(text, "execution", "agent")
    text = set_frontmatter_scalar(text, "status", status)
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    transaction = VaultTransaction()
    transaction.stage(match.path, text)
    if run_change:
        transaction.stage(*run_change)
    transaction.commit()
    print(f"Updated: {match.relpath}")
    print(f"Status: {status}")
    if run_identifier:
        print(f"Run: {run_identifier}")
    if status == "active" and dependency_problems:
        state = (
            "invalid" if any(item.state == "invalid" for item in dependency_problems) else "blocked"
        )
        print(f"Dependency state: {state}")
        for problem in dependency_problems:
            print(f"Blocked by: {problem.message}")


def pause_task(match: NoteMatch, root: Path, note: str) -> None:
    if not is_lifecycle_task(match.path, root):
        raise lifecycle_task_error()
    if not note.strip():
        raise OawError("task pause requires non-empty --note")
    execution = match.frontmatter.get("execution")
    if execution not in {"agent", "hybrid"}:
        if execution == "human":
            raise OawError("task execution is human; lifecycle is managed in Obsidian UI")
        raise OawError("task pause requires execution: agent or hybrid")
    task_id = str(match.note_id or "")
    if not task_id:
        raise OawError("lifecycle task requires a stable frontmatter id")
    identity = detect_identity()
    current = matching_run(root, task_id, identity, match.path)
    if current is None or current.state != "running":
        raise OawError("pause requires the caller's running record")
    now = utc_now()
    session_ref = f"{identity.env}={identity.session_id}"
    task_text = append_session_id_frontmatter(match.path.read_text(encoding="utf-8"), session_ref)
    task_text = append_session_entry(task_text, identity.provider_label, session_ref, note, None)
    transaction = VaultTransaction()
    transaction.stage(current.path, transition_run_text(current, "paused", "pause", now, note))
    transaction.stage(match.path, task_text)
    transaction.commit()
    print(f"Updated: {match.relpath}")
    print(f"Status: {match.frontmatter.get('status', '')}")
    print(f"Run: {current.id}")
    print("Run state: paused")


def append_task_note(
    match: NoteMatch, root: Path, note: str, checks: str | None, allow_missing: bool
) -> None:
    if not is_lifecycle_task(match.path, root):
        raise lifecycle_task_error()
    provider, session_ref = detect_session(allow_missing)
    text = match.path.read_text(encoding="utf-8")
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, checks)
    transaction = VaultTransaction()
    transaction.stage(match.path, text)
    try:
        identity = detect_identity()
    except OawError:
        identity = None
    if identity and match.note_id:
        current = matching_run(root, match.note_id, identity, match.path)
        if current and current.state == "running":
            transaction.stage(
                current.path,
                transition_run_text(current, "running", "note", utc_now(), note, checks),
            )
    transaction.commit()
    status = match.frontmatter.get("status", "")
    print(f"Updated: {match.relpath}")
    print(f"Status: {status}")


def _validate_frontmatter_value(key: str, value: str) -> None:
    if not value:
        raise OawError(f"task frontmatter list {key} contains an empty item")
    if value.startswith(("|", ">")):
        raise OawError("task frontmatter multiline scalar fields are not supported")
    if value[0] in "[{":
        pairs = {"[": "]", "{": "}"}
        if not value.endswith(pairs[value[0]]):
            raise OawError(f"task frontmatter field {key} has an unclosed flow value")
        stack: list[str] = []
        quote: str | None = None
        escaped = False
        for character in value:
            if escaped:
                escaped = False
                continue
            if quote == '"' and character == "\\":
                escaped = True
                continue
            if character in {"'", '"'}:
                quote = None if quote == character else character if quote is None else quote
                continue
            if quote is not None:
                continue
            if character in pairs:
                stack.append(pairs[character])
            elif character in "]}" and (not stack or stack.pop() != character):
                raise OawError(f"task frontmatter field {key} has an invalid flow value")
        if quote is not None or stack:
            raise OawError(f"task frontmatter field {key} has an unclosed flow value")
    elif value[0] in "]}":
        raise OawError(f"task frontmatter field {key} has an invalid flow value")
    elif value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OawError(f"task frontmatter field {key} has an invalid quoted value") from exc
        if not isinstance(parsed, str):
            raise OawError(f"task frontmatter field {key} must not use structured JSON")
    elif value.startswith("'") and not re.fullmatch(r"'(?:[^']|'')*'", value):
        raise OawError(f"task frontmatter field {key} has an invalid quoted value")
    elif re.search(r":\s", value) or value.startswith(("&", "*", "!", "@", "`", "? ", "- ")):
        raise OawError(f"task frontmatter field {key} contains an unsupported YAML value")


def _validate_priority_update_frontmatter(lines: list[str], end: int) -> None:
    seen_keys: set[str] = set()
    block_list_key: str | None = None
    priority_value: str | None = None
    for raw_line in lines[1:end]:
        line = raw_line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if line.startswith("\t"):
                raise OawError("task frontmatter indentation must use spaces")
            item = re.fullmatch(r"\s+-\s+(\S.*)", line)
            if block_list_key is None or item is None:
                raise OawError("task frontmatter must use flat scalar fields and flat block lists")
            item_value, _ = split_inline_comment(item.group(1))
            _validate_frontmatter_value(block_list_key, item_value)
            continue
        field = re.fullmatch(r"([A-Za-z0-9_-]+)\s*:\s*(.*)", line)
        if field is None:
            raise OawError("task frontmatter contains an unsupported or malformed field")
        key, raw_value = field.groups()
        if key in seen_keys:
            raise OawError(f"task frontmatter contains duplicate field: {key}")
        seen_keys.add(key)
        value, _ = split_inline_comment(raw_value.strip())
        if not value:
            block_list_key = key
            if key == "priority":
                priority_value = ""
            continue
        block_list_key = None
        _validate_frontmatter_value(key, value)
        if key == "priority":
            priority_value = value
    if priority_value is not None and priority_value not in {"1", "2", "3"}:
        raise OawError(
            "task priority frontmatter must be a scalar 1, 2, or 3 before OAW can update it"
        )


def update_task_priority(
    match: NoteMatch,
    root: Path,
    priority: int,
    note: str,
    allow_missing: bool,
) -> None:
    if not is_lifecycle_task(match.path, root):
        raise lifecycle_task_error()
    if match.frontmatter.get("type") != "task":
        raise OawError("task priority requires frontmatter type: task")
    if priority not in {1, 2, 3}:
        raise OawError("task priority must be 1, 2, or 3")
    if not note.strip():
        raise OawError("task priority requires non-empty --note")

    text = match.path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise OawError("task note has no YAML frontmatter")
    end = next((idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"), None)
    if end is None:
        raise OawError("task note frontmatter is not closed")
    _validate_priority_update_frontmatter(lines, end)

    provider, session_ref = detect_session(allow_missing)
    text = set_frontmatter_scalar(text, "priority", str(priority))
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, None)
    transaction = VaultTransaction()
    transaction.stage(match.path, text)
    transaction.commit()
    print(f"Updated: {match.relpath}")
    print(f"Priority: {priority}")
    print(f"Status: {match.frontmatter.get('status', '')}")


def update_task_preparedness(
    match: NoteMatch,
    root: Path,
    preparedness: str,
    note: str,
    allow_missing: bool,
) -> None:
    if not is_lifecycle_task(match.path, root):
        raise lifecycle_task_error()
    if match.frontmatter.get("type") != "task":
        raise OawError("task preparedness requires frontmatter type: task")
    if preparedness not in TASK_PREPAREDNESS_STATES:
        raise OawError("task preparedness must be needs-triage, needs-design, or prepared")
    if not note.strip():
        raise OawError("task preparedness requires non-empty --note")

    text = match.path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise OawError("task note has no YAML frontmatter")
    end = next((idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"), None)
    if end is None:
        raise OawError("task note frontmatter is not closed")
    _validate_priority_update_frontmatter(lines, end)
    current = match.frontmatter.get("preparedness")
    if current is not None and current not in TASK_PREPAREDNESS_STATES:
        raise OawError(
            "task preparedness frontmatter must be a scalar needs-triage, "
            "needs-design, or prepared before OAW can update it"
        )

    provider, session_ref = detect_session(allow_missing)
    text = set_frontmatter_scalar(text, "preparedness", preparedness)
    text = append_session_id_frontmatter(text, session_ref)
    text = append_session_entry(text, provider, session_ref, note, None)
    transaction = VaultTransaction()
    transaction.stage(match.path, text)
    transaction.commit()
    print(f"Updated: {match.relpath}")
    print(f"Preparedness: {preparedness}")
    print(f"Status: {match.frontmatter.get('status', '')}")


def update_task_relation(
    root: Path,
    source_value: str,
    relation_type: str,
    target_value: str,
    note: str,
    allow_missing: bool,
    remove: bool,
) -> None:
    action = "remove" if remove else "add"
    if not note.strip():
        raise OawError(f"task relation {action} requires non-empty --note")
    mutation = (
        prepare_relation_remove(root, source_value, relation_type, target_value)
        if remove
        else prepare_relation_add(root, source_value, relation_type, target_value)
    )
    if not mutation.changed:
        print(f"Source: {mutation.source.note_id}")
        print(f"Relation: {relation_type}")
        print(f"Target: {mutation.target.note_id}")
        print("State: present")
        return

    provider, session_ref = detect_session(allow_missing)
    verb = "Removed" if remove else "Added"
    detail = f"{verb} {relation_type} relationship to {mutation.target.note_id}. {note.strip()}"
    text = append_session_id_frontmatter(mutation.updated_text, session_ref)
    text = append_session_entry(text, provider, session_ref, detail, None)
    transaction = VaultTransaction()
    transaction.stage(mutation.source.path, text)
    transaction.commit()
    print(f"Updated: {mutation.source.relpath}")
    print(f"Relation: {relation_type}")
    print(f"Target: {mutation.target.note_id}")
    print(f"Action: {action}")
    print(f"Status: {mutation.source.frontmatter.get('status', '')}")


def run_belongs_to_session(run: Run, session_id: str) -> bool:
    values = run.data.get("session-ids")
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return session_id in values
    return run.data.get("agent_session_id") == session_id


def list_runs(
    task_id: str | None,
    state: str | None,
    session_id: str | None,
    current_session: bool,
    as_json: bool,
    root: Path,
) -> None:
    now = utc_now()
    if current_session:
        session_id = detect_identity().session_id
    selected_task = strip_obs_prefix(task_id) if task_id else None
    rows = [
        run
        for run in iter_runs(root)
        if (not selected_task or run.data.get("task_id") == selected_task)
        and (not state or run.state == state)
        and (session_id is None or run_belongs_to_session(run, session_id))
    ]
    if as_json:
        print(
            json.dumps(
                [
                    {
                        **run.data,
                        "path": run.path.relative_to(root).as_posix(),
                        "stale": is_stale(run, now),
                    }
                    for run in rows
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    for run in rows:
        print(
            "\t".join(
                (
                    run.id,
                    str(run.data.get("task_id", "")),
                    run.state,
                    str(run.data.get("provider", "")),
                    "stale" if is_stale(run, now) else "current",
                    str(run.data.get("last_event_at", "")),
                )
            )
        )


def close_run(identifier: str, reason: str, root: Path) -> None:
    if not reason.strip():
        raise OawError("run close requires a non-empty reason")
    identity = detect_identity()
    run = find_run(root, identifier, lambda task_id: resolve_id(task_id, root))
    if run.state in {"completed", "closed"}:
        raise OawError(f"run {identifier} is already {run.state}")
    text = transition_run_text(
        run,
        "closed",
        "administrative closure",
        utc_now(),
        reason,
        ended_reason=reason,
        closer=identity,
    )
    transaction = VaultTransaction()
    transaction.stage(run.path, text)
    transaction.commit()
    print(f"Run: {identifier}")
    print("Run state: closed")
    print(f"Reason: {reason}")


def audit_run_registry(root: Path) -> None:
    findings = audit_runs(root, lambda task_id: resolve_id(task_id, root), utc_now())
    if findings:
        for finding in findings:
            print(finding)
        raise OawError(f"run audit found {len(findings)} issue(s)")
    print("Run audit: clean")


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
    preparedness: str,
    note: str | None,
    tags: list[str] | None,
    execution: str | None,
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
    if execution not in {None, "human", "agent", "hybrid"}:
        raise OawError("task execution must be human, agent, or hybrid")
    if preparedness not in TASK_PREPAREDNESS_STATES:
        raise OawError("task preparedness must be needs-triage, needs-design, or prepared")
    if start and execution == "human":
        raise OawError("cannot --start a task with human execution")
    identity = detect_identity() if start else None
    if identity:
        provider = identity.provider_label
        session_ref = f"{identity.env}={identity.session_id}"
    else:
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
    created = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    project_slug = _slugify(project_root.name)
    task_status = "active" if start else status
    task_execution = execution or ("agent" if start else None)
    lines = [
        "---",
        "type: task",
        f"project: {project_slug}",
        f"status: {task_status}",
        f"preparedness: {preparedness}",
        f"created: {created}",
    ]
    if priority is not None:
        lines.append(f"priority: {priority}")
    if effort:
        lines.append(f"effort: {effort}")
    if task_execution:
        lines.append(f"execution: {task_execution}")
    lines += [
        f"id: {note_id}",
        "aliases:",
        f"  - {note_id}",
    ]
    try:
        lines.extend(creation_tag_block(("projects", project_slug, "task"), tags))
    except OawError as exc:
        raise OawError(f"task create --tag: {exc}") from exc
    if capture:
        lines.append(f"source-capture: {capture.note_id}")
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else ""
    if session_id and session_id != "unavailable":
        lines += ["session-ids:", f"  - {yaml_quote(session_id)}"]
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
    transaction = VaultTransaction()
    transaction.stage(path, task_text)
    if capture:
        task_link = f"[[{relpath.with_suffix('').as_posix()}|{note_id}]]"
        capture_text = capture.path.read_text(encoding="utf-8")
        capture_text = _append_to_section(capture_text, "Related", task_link)
        capture_text = append_frontmatter_list_value(capture_text, "destinations", task_link)
        capture_text = set_frontmatter_scalar(capture_text, "status", "triaged")
        transaction.stage(capture.path, capture_text)
    run_identifier: str | None = None
    if identity:
        run_identifier, run_text = new_run_text(
            root,
            path,
            {"id": note_id, "project": project_slug, "execution": task_execution},
            identity,
            utc_now(),
            note="Atomic task creation and start.",
        )
        transaction.stage(run_path(root, run_identifier), run_text)
    transaction.commit()
    print(f"Created: {relpath.as_posix()}")
    print(f"ID: {note_id}")
    print(f"Status: {task_status}")
    if run_identifier:
        print(f"Run: {run_identifier}")
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
    ]
    lines.extend(creation_tag_block((), tags))
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
    project_slug = _slugify(name)
    try:
        tags = creation_tags(("projects", project_slug), tags_value)
    except OawError as exc:
        raise OawError(f"project create --tag: {exc}") from exc
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
        "oaw-research helper.\n"
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
