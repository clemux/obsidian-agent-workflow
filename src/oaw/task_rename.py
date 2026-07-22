"""Planning and transactional application for safe task-note renames."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import parse_frontmatter, read_frontmatter_text
from .links import (
    ContainerPart,
    _backtick_run_end,
    _fence_marker,
    _is_blank_line,
    _is_indented_code_line,
    _matching_backtick_run,
    _starts_inline_block_boundary,
    normalize_link_target,
)
from .notes import (
    FileSnapshot,
    VaultTransaction,
    capture_file_snapshot,
    fence_closes,
    fence_delimiter,
    split_note,
)
from .relations import RelationGraph, build_relation_graph
from .resolver import (
    NoteMatch,
    iter_markdown,
    note_match_from_reference,
    resolve_id,
    scan_note_references,
    strip_obs_prefix,
)
from .runs import runs_for_task

WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
FORBIDDEN_TITLE_CHARACTERS = set('/\\:*?"<>|')


@dataclass(frozen=True)
class RenameChange:
    original_path: Path
    proposed_path: Path
    original_relpath: str
    proposed_relpath: str
    original: FileSnapshot
    proposed_text: str
    link_count: int
    h1_changed: bool = False


@dataclass(frozen=True)
class RenamePlan:
    root: Path
    task_id: str
    title: str
    reason: str
    source: NoteMatch
    old_relpath: str
    new_relpath: str
    old_h1: str
    changes: tuple[RenameChange, ...]
    scanned_relpaths: tuple[str, ...]
    digest: str
    no_op: bool

    @property
    def total_links(self) -> int:
        return sum(change.link_count for change in self.changes)


def _is_supported_task_path(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return (
        (len(parts) == 4 and parts[0] == "Projects" and parts[2] == "Tasks")
        or (len(parts) == 3 and parts[:2] == ("Agents", "Tasks"))
        or (len(parts) == 2 and parts[0] == "Tasks")
    )


def _resolve_canonical_id(raw_id: str, root: Path) -> NoteMatch:
    task_id = strip_obs_prefix(raw_id)
    references = scan_note_references(root)
    exact = []
    aliases = []
    for reference in references:
        data = parse_frontmatter(reference.frontmatter_text)
        if data.get("id") == task_id:
            exact.append(reference)
        elif isinstance(data.get("aliases"), list) and task_id in data["aliases"]:
            aliases.append(reference)
    if not exact:
        if aliases:
            paths = ", ".join(reference.relpath for reference in aliases)
            raise OawError(
                f"task rename requires the canonical frontmatter id; alias-only match: {paths}"
            )
        raise OawError(f"no note with canonical frontmatter id '{task_id}' under {root}")
    if len(exact) > 1:
        paths = "\n".join(f"  {reference.relpath}" for reference in exact)
        raise OawError(f"canonical id '{task_id}' is not unique:\n{paths}")
    match = note_match_from_reference(exact[0], task_id)
    if match is None or match.matched_by != "id":
        raise OawError(f"could not resolve canonical task id: {task_id}")
    return match


def _frontmatter_field_count(frontmatter: str, field: str) -> int:
    pattern = re.compile(rf"^{re.escape(field)}\s*:")
    return sum(1 for line in frontmatter.splitlines() if pattern.match(line))


def _validate_source(match: NoteMatch, root: Path, task_id: str) -> tuple[FileSnapshot, str]:
    if not _is_supported_task_path(match.path, root):
        raise OawError(
            "task rename is supported for Projects/*/Tasks, Agents/Tasks, and root Tasks"
        )
    snapshot = capture_file_snapshot(match.path)
    try:
        text = snapshot.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OawError(f"task note is not valid UTF-8: {match.relpath}") from exc
    frontmatter = read_frontmatter_text(match.path, max_bytes=None, require_closed=True)
    data = parse_frontmatter(frontmatter)
    for field in ("type", "id", "aliases", "session-ids"):
        if _frontmatter_field_count(frontmatter, field) > 1:
            raise OawError(f"task note frontmatter contains duplicate field: {field}")
    if _frontmatter_field_count(frontmatter, "type") != 1 or data.get("type") != "task":
        raise OawError("task rename requires exactly one scalar 'type: task' field")
    if _frontmatter_field_count(frontmatter, "id") != 1 or data.get("id") != task_id:
        raise OawError("task rename requires exactly one stable scalar frontmatter id")
    for field in ("aliases", "session-ids"):
        value = data.get(field)
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise OawError(f"task rename requires {field} to be a string list")
    return snapshot, text


def normalize_task_title(raw_title: str) -> str:
    title = unicodedata.normalize("NFC", raw_title)
    if not title or title != title.strip() or "\n" in title or "\r" in title:
        raise OawError("task title must be one non-empty, trimmed line")
    if title.startswith(".") or title.endswith((".", " ")):
        raise OawError("task title must not start with a dot or end with a dot or space")
    if any(character in FORBIDDEN_TITLE_CHARACTERS for character in title):
        raise OawError("task title contains a character that is unsafe in filenames")
    if any(unicodedata.category(character) == "Cc" for character in title):
        raise OawError("task title contains a control character")
    if title.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
        raise OawError("task title uses a reserved device name")
    return title


def _replace_single_h1(text: str, title: str) -> tuple[str, str, bool]:
    frontmatter, _, body = split_note(text)
    if not frontmatter:
        raise OawError("task note has no closed YAML frontmatter")
    lines = body.splitlines(keepends=True)
    active_fence: str | None = None
    matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        delimiter = fence_delimiter(line)
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif fence_closes(active_fence, line):
                active_fence = None
            continue
        if active_fence is None:
            found = re.match(r"^#[ \t]+([^\r\n]*?)[ \t]*(?:\r?\n)?$", line)
            if found:
                matches.append((index, found.group(1)))
    if len(matches) != 1 or not matches[0][1]:
        raise OawError("task note must contain exactly one non-empty H1 outside fenced code")
    index, old_h1 = matches[0]
    ending = (
        "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
    )
    changed = old_h1 != title
    if changed:
        lines[index] = f"# {title}{ending}"
    return old_h1, f"{frontmatter}{''.join(lines)}", changed


def _is_escaped(text: str, index: int) -> bool:
    slashes = 0
    while index > 0 and text[index - 1] == "\\":
        slashes += 1
        index -= 1
    return slashes % 2 == 1


def _split_wikilink_inner_raw(inner: str) -> tuple[str, str]:
    index = 0
    while index < len(inner):
        if inner[index] == "\\" and index + 1 < len(inner):
            index += 2
            continue
        if inner[index] == "|":
            return inner[:index], inner[index:]
        index += 1
    return inner, ""


def _matching_inline_backtick_run(text: str, start: int, length: int) -> tuple[int, int] | None:
    """Find an exact closing run without crossing a Markdown block boundary."""
    search_at = start
    while True:
        closing = _matching_backtick_run(text, search_at, length)
        if closing is None:
            return None
        newline = text.find("\n", search_at, closing[0])
        while newline >= 0:
            if _starts_inline_block_boundary(text, newline + 1):
                return None
            newline = text.find("\n", newline + 1, closing[0])
        return closing


def _rewrite_wikilink_inner(inner: str, old_target: str, new_target: str) -> str | None:
    target_raw, alias_tail = _split_wikilink_inner_raw(inner)
    leading = target_raw[: len(target_raw) - len(target_raw.lstrip())]
    trailing = target_raw[len(target_raw.rstrip()) :]
    target = target_raw.strip()
    suffix_positions = [position for marker in ("#", "^") if (position := target.find(marker)) >= 0]
    suffix_at = min(suffix_positions) if suffix_positions else len(target)
    base = target[:suffix_at]
    suffix = target[suffix_at:]
    normalized = unicodedata.normalize("NFC", normalize_link_target(base))
    if normalized != unicodedata.normalize("NFC", old_target):
        return None
    return f"{leading}{new_target}{suffix}{trailing}{alias_tail}"


def rewrite_active_wikilink_targets(text: str, old_target: str, new_target: str) -> tuple[str, int]:
    """Rewrite active wikilink targets while protecting code and comments."""
    if old_target == new_target:
        return text, 0
    rendered: list[str] = []
    count = 0
    active_fence: tuple[str, int, tuple[ContainerPart, ...]] | None = None
    code_end: int | None = None
    obsidian_comment = False
    html_comment = False
    active_indented_code = False
    previous_blank = True
    line_offset = 0
    for line in text.splitlines(keepends=True):
        if active_indented_code:
            if _is_blank_line(line) or _is_indented_code_line(line):
                rendered.append(line)
                previous_blank = _is_blank_line(line)
                line_offset += len(line)
                continue
            active_indented_code = False
        if (
            code_end is None
            and not obsidian_comment
            and not html_comment
            and active_fence is None
            and previous_blank
            and _is_indented_code_line(line)
        ):
            rendered.append(line)
            active_indented_code = True
            previous_blank = False
            line_offset += len(line)
            continue

        marker = (
            _fence_marker(line, active_fence[2] if active_fence is not None else None)
            if code_end is None and not obsidian_comment and not html_comment
            else None
        )
        if active_fence is None and marker is not None:
            character, length, _, container_parts = marker
            active_fence = character, length, container_parts
            rendered.append(line)
            previous_blank = _is_blank_line(line)
            line_offset += len(line)
            continue
        if active_fence is not None:
            if (
                marker is not None
                and marker[0] == active_fence[0]
                and marker[1] >= active_fence[1]
                and not marker[2].strip()
            ):
                active_fence = None
            rendered.append(line)
            previous_blank = _is_blank_line(line)
            line_offset += len(line)
            continue

        output: list[str] = []
        index = 0
        while index < len(line):
            if html_comment:
                end = line.find("-->", index)
                if end < 0:
                    output.append(line[index:])
                    index = len(line)
                else:
                    output.append(line[index : end + 3])
                    index = end + 3
                    html_comment = False
                continue
            if obsidian_comment:
                end = line.find("%%", index)
                if end < 0:
                    output.append(line[index:])
                    index = len(line)
                else:
                    output.append(line[index : end + 2])
                    index = end + 2
                    obsidian_comment = False
                continue
            if code_end is not None:
                local_end = min(len(line), code_end - line_offset)
                output.append(line[index:local_end])
                index = local_end
                if line_offset + index >= code_end:
                    code_end = None
                continue
            if line.startswith("<!--", index):
                output.append("<!--")
                index += 4
                html_comment = True
                continue
            if line.startswith("%%", index):
                output.append("%%")
                index += 2
                obsidian_comment = True
                continue
            if line[index] == "`" and not _is_escaped(line, index):
                end = _backtick_run_end(line, index)
                length = end - index
                closing = _matching_inline_backtick_run(text, line_offset + end, length)
                output.append(line[index:end])
                index = end
                if closing is not None:
                    code_end = closing[1]
                continue
            prefix_length = (
                3 if line.startswith("![[", index) else 2 if line.startswith("[[", index) else 0
            )
            if prefix_length and not _is_escaped(line, index):
                inner_start = index + prefix_length
                end = line.find("]]", inner_start)
                if end >= 0:
                    inner = line[inner_start:end]
                    replacement = _rewrite_wikilink_inner(inner, old_target, new_target)
                    if replacement is not None:
                        output.append(line[index:inner_start])
                        output.append(replacement)
                        output.append("]]")
                        index = end + 2
                        count += 1
                        continue
            output.append(line[index])
            index += 1
        rendered.append("".join(output))
        previous_blank = _is_blank_line(line)
        line_offset += len(line)
    return "".join(rendered), count


def _validate_destination(source: Path, title: str) -> Path:
    destination = source.parent / f"{title}.md"
    wanted = unicodedata.normalize("NFC", destination.name).casefold()
    for sibling in source.parent.iterdir():
        if sibling == source:
            continue
        sibling_key = unicodedata.normalize("NFC", sibling.name).casefold()
        if sibling_key == wanted:
            raise OawError(
                f"task rename destination collides with existing sibling: {sibling.name}"
            )
    if (destination.exists() or destination.is_symlink()) and not VaultTransaction._same_entry(
        source, destination
    ):
        raise OawError(f"task rename destination already exists: {destination.name}")
    return destination


def _relation_issues_for_path(graph: RelationGraph, relpath: str) -> list[str]:
    key = Path(relpath).with_suffix("").as_posix()
    messages: list[str] = []
    for issue in graph.issues:
        source_key = Path(issue.source.relpath).with_suffix("").as_posix()
        target_key = Path(issue.target.relpath).with_suffix("").as_posix() if issue.target else None
        if source_key == key or target_key == key or key in issue.involved_paths:
            messages.append(f"{issue.source.relpath}: {issue.relation_type}: {issue.message}")
    return messages


def _plan_digest(
    task_id: str,
    title: str,
    reason: str,
    old_relpath: str,
    new_relpath: str,
    source_snapshot: FileSnapshot,
    changes: list[RenameChange],
) -> str:
    payload = {
        "version": 1,
        "task_id": task_id,
        "title": title,
        "reason": reason,
        "old_path": old_relpath,
        "new_path": new_relpath,
        "source_hash": hashlib.sha256(source_snapshot.data).hexdigest(),
        "changes": [
            {
                "old_path": change.original_relpath,
                "new_path": change.proposed_relpath,
                "original": hashlib.sha256(change.original.data).hexdigest(),
                "proposed": hashlib.sha256(change.proposed_text.encode("utf-8")).hexdigest(),
            }
            for change in sorted(changes, key=lambda item: item.original_relpath)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def prepare_task_rename(root: Path, raw_id: str, raw_title: str, reason: str) -> RenamePlan:
    if not reason.strip():
        raise OawError("task rename requires non-empty --note")
    title = normalize_task_title(raw_title)
    source = _resolve_canonical_id(raw_id, root)
    task_id = str(source.note_id)
    source_snapshot, source_text = _validate_source(source, root, task_id)
    old_h1, h1_text, h1_changed = _replace_single_h1(source_text, title)
    destination = _validate_destination(source.path, title)
    old_relpath = source.relpath
    new_relpath = destination.relative_to(root).as_posix()
    path_changed = source.path != destination

    if not path_changed and not h1_changed:
        digest = _plan_digest(task_id, title, reason, old_relpath, new_relpath, source_snapshot, [])
        return RenamePlan(
            root,
            task_id,
            title,
            reason,
            source,
            old_relpath,
            new_relpath,
            old_h1,
            (),
            (),
            digest,
            True,
        )

    def resolve_task(value: str) -> NoteMatch:
        return resolve_id(value, root)

    task_runs = runs_for_task(root, task_id, resolve_task)
    running = [run.id for run in task_runs if run.state == "running"]
    if running:
        raise OawError("task rename refused while an agent run is running: " + ", ".join(running))
    relation_graph = build_relation_graph(root)
    relation_issues = _relation_issues_for_path(relation_graph, old_relpath)
    if relation_issues:
        raise OawError(
            "task rename refused by malformed semantic relationships: " + "; ".join(relation_issues)
        )

    old_target = Path(old_relpath).with_suffix("").as_posix()
    new_target = Path(new_relpath).with_suffix("").as_posix()
    changes: list[RenameChange] = []
    scanned: list[str] = []
    for path in sorted(iter_markdown(root)):
        relative = path.relative_to(root).as_posix()
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise OawError(f"could not inspect Markdown note: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OawError(f"Markdown note must be a regular, non-symlink file: {relative}")
        snapshot = source_snapshot if path == source.path else capture_file_snapshot(path)
        try:
            original_text = snapshot.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OawError(f"Markdown note is not valid UTF-8: {relative}") from exc
        scanned.append(relative)
        structural = h1_text if path == source.path else original_text
        proposed, link_count = rewrite_active_wikilink_targets(structural, old_target, new_target)
        proposed_path = destination if path == source.path else path
        changed = proposed != original_text or proposed_path != path
        if changed:
            changes.append(
                RenameChange(
                    path,
                    proposed_path,
                    relative,
                    proposed_path.relative_to(root).as_posix(),
                    snapshot,
                    proposed,
                    link_count,
                    h1_changed if path == source.path else False,
                )
            )
    digest = _plan_digest(
        task_id, title, reason, old_relpath, new_relpath, source_snapshot, changes
    )
    return RenamePlan(
        root,
        task_id,
        title,
        reason,
        source,
        old_relpath,
        new_relpath,
        old_h1,
        tuple(sorted(changes, key=lambda item: item.original_relpath)),
        tuple(scanned),
        digest,
        False,
    )


def print_task_rename_plan(plan: RenamePlan) -> None:
    print(f"Task: {plan.task_id}")
    print(f"Old path: {plan.old_relpath}")
    print(f"New path: {plan.new_relpath}")
    print(f"Old H1: {plan.old_h1}")
    print(f"New H1: {plan.title}")
    print("Affected notes:")
    if not plan.changes:
        print("- (none)")
    for change in plan.changes:
        path = (
            f"{change.original_relpath} -> {change.proposed_relpath}"
            if change.original_relpath != change.proposed_relpath
            else change.original_relpath
        )
        print(f"- {path} | links: {change.link_count}")
    print(f"Totals: notes {len(plan.changes)} | links {plan.total_links}")
    print(f"Plan: {plan.digest}")
    if plan.no_op:
        print("No-op: task already has the requested path and H1")


def _assert_postconditions(plan: RenamePlan) -> None:
    resolved = resolve_id(plan.task_id, plan.root)
    if resolved.matched_by != "id" or resolved.relpath != plan.new_relpath:
        raise OawError("postcondition failed: canonical id did not resolve to the new path")
    old_path = plan.root / plan.old_relpath
    new_path = plan.root / plan.new_relpath
    if old_path != new_path and (old_path.exists() or old_path.is_symlink()):
        raise OawError("postcondition failed: old task path still exists")
    new_snapshot = capture_file_snapshot(new_path)
    try:
        new_text = new_snapshot.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OawError("postcondition failed: renamed task is not valid UTF-8") from exc
    current_h1, _, _ = _replace_single_h1(new_text, plan.title)
    if current_h1 != plan.title:
        raise OawError("postcondition failed: renamed task H1 does not match requested title")

    old_target = Path(plan.old_relpath).with_suffix("").as_posix()
    new_target = Path(plan.new_relpath).with_suffix("").as_posix()
    for relative in plan.scanned_relpaths:
        path = new_path if relative == plan.old_relpath else plan.root / relative
        if not path.exists():
            raise OawError(f"postcondition failed: scanned note disappeared: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise OawError(
                f"postcondition failed: could not read scanned note: {relative}"
            ) from exc
        _, remaining = rewrite_active_wikilink_targets(text, old_target, new_target)
        if remaining:
            raise OawError(f"postcondition failed: stale task wikilink remains in {relative}")

    def resolve_task(value: str) -> NoteMatch:
        return resolve_id(value, plan.root)

    runs_for_task(plan.root, plan.task_id, resolve_task)
    relation_issues = _relation_issues_for_path(build_relation_graph(plan.root), plan.new_relpath)
    if relation_issues:
        raise OawError(
            "postcondition failed: semantic relationships are invalid: "
            + "; ".join(relation_issues)
        )


def apply_task_rename(plan: RenamePlan, traced_task_text: str) -> None:
    if plan.no_op:
        return
    source_change = next(
        (change for change in plan.changes if change.original_path == plan.source.path), None
    )
    if source_change is None:
        raise OawError("task rename plan does not contain the source note")
    transaction = VaultTransaction()
    if source_change.original_path == source_change.proposed_path:
        transaction.stage(
            source_change.original_path,
            traced_task_text,
            expected=source_change.original,
        )
    else:
        transaction.stage_move(
            source_change.original_path,
            source_change.proposed_path,
            traced_task_text,
            source_change.original,
        )
    for change in plan.changes:
        if change is source_change:
            continue
        transaction.stage(change.original_path, change.proposed_text, expected=change.original)
    transaction.commit(postcondition=lambda: _assert_postconditions(plan))
