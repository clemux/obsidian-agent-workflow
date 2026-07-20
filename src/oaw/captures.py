"""Create, list, show, and triage capture notes under the canonical store."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import (
    append_frontmatter_list_value,
    parse_frontmatter,
    read_frontmatter_text,
    set_frontmatter_scalar,
)
from .lifecycle import append_session_id_frontmatter
from .links import (
    append_to_section,
    durable_wikilink,
    link_matches_note,
    materialize_obs_references,
    parse_wikilinks,
)
from .notes import (
    VaultTransaction,
    append_markdown_block_to_section,
    read_note,
    split_note,
    write_new_note_atomic,
)
from .resolver import (
    NoteMatch,
    iter_markdown,
    matches_from_references,
    note_status,
    note_type_matches,
    resolve_id,
    resolve_id_from_references,
    resolve_project_root,
    resolve_project_root_from_references,
    scan_note_references,
    title_from_body,
)
from .runs import yaml_quote
from .sessions import detect_session
from .tags import creation_tag_block

CAPTURE_DIRECTORY = Path("Captures/Entries")
CAPTURE_STATUSES = ("inbox", "incubating", "parked", "reference", "triaged", "discarded")

_TYPE_CAPTURE_RE = re.compile(r"(?m)^type:[ \t]*capture[ \t]*$")


def slugify(value: str) -> str:
    """Slugify identically to ``lifecycle._slugify`` (shared task-ID semantics)."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "session"


def _scalar(value: str) -> str:
    """Serialize a single-line scalar as an unambiguous JSON/YAML string."""
    return json.dumps(value, ensure_ascii=False)


def _single_line(raw: str | None, label: str) -> str | None:
    if raw is None:
        return None
    if "\n" in raw or "\r" in raw:
        raise OawError(f"capture {label} must be a single-line value")
    value = raw.strip()
    return value or None


def _relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _index_link_label(index_path: Path) -> str | None:
    """Link label for a project index whose id lacks the <ALIAS>-index convention."""
    try:
        frontmatter = parse_frontmatter(read_frontmatter_text(index_path))
    except OawError:
        return None
    raw = frontmatter.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def _build_capture_text(
    note_id: str,
    created: str,
    project_slug: str | None,
    area: str | None,
    context: str | None,
    outcome: str | None,
    urls: list[str],
    session_id: str | None,
    tags: list[str] | None,
    title: str,
    rendered_body: str | None,
    related_link: str | None,
) -> str:
    lines = [
        "---",
        f"id: {note_id}",
        "aliases:",
        f"  - {note_id}",
        "type: capture",
        f"created: {created}",
        "status: inbox",
        f"project: {project_slug}" if project_slug else "project:",
        f"area: {_scalar(area)}" if area else "area:",
        f"context: {_scalar(context)}" if context else "context:",
        f"outcome: {_scalar(outcome)}" if outcome else "outcome:",
        "review_after:",
        "destinations:",
    ]
    if urls:
        lines.append("urls:")
        lines.extend(f"  - {yaml_quote(url)}" for url in urls)
    if session_id:
        lines += ["session-ids:", f"  - {yaml_quote(session_id)}"]
    try:
        lines.extend(creation_tag_block(("capture",), tags))
    except OawError as exc:
        raise OawError(f"capture create --tag: {exc}") from exc
    lines.append("---")

    text = "\n".join(lines) + "\n\n" + f"# {title}\n"
    if rendered_body is not None:
        body = rendered_body if rendered_body.endswith("\n") else rendered_body + "\n"
        text += "\n" + body
    if related_link is not None:
        text += f"\n## Related\n\n- {related_link}\n"
    return text


def create_capture(
    root: Path,
    title_value: str,
    body: str | None,
    project: str | None,
    area_value: str | None,
    context_value: str | None,
    outcome_value: str | None,
    urls: list[str],
    tags: list[str] | None,
    json_output: bool,
    allow_missing_session_id: bool,
) -> None:
    """Write one canonical capture note after every provenance and collision check."""
    title = title_value.strip()
    if not title:
        raise OawError("capture create requires a non-empty --title")
    area = _single_line(area_value, "area")
    context = _single_line(context_value, "context")
    outcome = _single_line(outcome_value, "outcome")

    references = scan_note_references(root)

    project_slug: str | None = None
    related_link: str | None = None
    index_path: Path | None = None
    if project:
        project_root, alias = resolve_project_root_from_references(project, root, references)
        project_slug = slugify(project_root.name)
        index_path = project_root / "Index.md"
        if index_path.exists():
            index_rel = index_path.relative_to(root).with_suffix("").as_posix()
            label = f"{alias}-index" if alias else _index_link_label(index_path)
            related_link = f"[[{index_rel}|{label}]]" if label else f"[[{index_rel}]]"
        else:
            index_path = None

    _provider, session_ref = detect_session(allow_missing_session_id)
    raw_session_id = session_ref.split("=", 1)[1] if "=" in session_ref else ""
    session_id = raw_session_id if raw_session_id and raw_session_id != "unavailable" else None

    rendered_body = materialize_obs_references(body, root, references)[0] if body else None

    local_date = dt.datetime.now().astimezone().strftime("%Y%m%d")
    created = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    base_id = f"CAP-{local_date}-{slugify(title)}"

    for attempt in range(1, 11):
        note_id = base_id if attempt == 1 else f"{base_id}-{attempt}"
        rel = CAPTURE_DIRECTORY / f"{note_id}.md"
        path = root / rel
        if matches_from_references(note_id, references) or path.exists():
            continue
        text = _build_capture_text(
            note_id,
            created,
            project_slug,
            area,
            context,
            outcome,
            urls,
            session_id,
            tags,
            title,
            rendered_body,
            related_link,
        )
        index_original: str | None = None
        index_new: str | None = None
        if related_link is not None and index_path is not None:
            index_original = index_path.read_text(encoding="utf-8")
            capture_link = f"[[{rel.with_suffix('').as_posix()}|{note_id}]]"
            if capture_link not in index_original:
                index_new = append_markdown_block_to_section(
                    index_original, "Captures", f"- {capture_link}"
                )
        try:
            write_new_note_atomic(path, text)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OawError(f"could not create capture note: {rel.as_posix()}: {exc}") from exc
        if index_new is not None and index_path is not None:
            try:
                assert index_original is not None
                transaction = VaultTransaction()
                transaction.stage(index_path, index_new, expected=index_original)
                transaction.commit()
            except Exception as exc:
                path.unlink(missing_ok=True)
                raise OawError(
                    f"could not update project index for capture {note_id}: {exc}"
                ) from exc
        body_chars = len(split_note(text)[2])
        if json_output:
            print(
                json.dumps(
                    {
                        "id": note_id,
                        "path": rel.as_posix(),
                        "title": title,
                        "status": "inbox",
                        "created": created,
                        "project": project_slug,
                        "urls": urls,
                        "body_chars": body_chars,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Created: {rel.as_posix()}")
            print(f"ID: {note_id}")
            print("Status: inbox")
            print(f"Path: {rel.as_posix()}")
        return

    raise OawError(f"could not allocate a unique capture id after 10 attempts: {base_id}")


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _CaptureRow:
    note_id: str
    status: str
    created: str
    project: str
    title: str
    body_chars: int
    relpath: str
    sort_dt: dt.datetime | None


def _declares_capture(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            head = handle.read(4096)
    except (OSError, UnicodeError):
        return False
    return _TYPE_CAPTURE_RE.search(head) is not None


def _read_capture_meta(path: Path) -> dict[str, object] | None:
    """Return parsed frontmatter for a capture, or ``None`` for non-captures.

    Raises for a malformed ``type: capture`` candidate so the caller can warn.
    """
    try:
        raw = read_frontmatter_text(path, max_bytes=None, require_closed=True)
    except (OawError, OSError, UnicodeError):
        if _declares_capture(path):
            raise
        return None
    data = parse_frontmatter(raw)
    if not note_type_matches(data, "capture"):
        return None
    return data


def _read_capture_body(path: Path) -> str:
    """Body-read seam: only invoked for captures surviving the list filters."""
    return split_note(path.read_text(encoding="utf-8"))[2]


def _parse_created(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _created_str(data: dict[str, object]) -> str:
    value = data.get("created")
    return value.strip() if isinstance(value, str) else ""


def _project_str(data: dict[str, object]) -> str:
    value = data.get("project")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _project_folder_from_path(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root / "Projects").parts[0]
    except (ValueError, IndexError):
        return None


def _capture_matches_project(
    data: dict[str, object], path: Path, root: Path, requested_slug: str
) -> bool:
    metadata = _project_str(data)
    if metadata:
        return slugify(metadata) == requested_slug
    folder = _project_folder_from_path(path, root)
    return folder is not None and slugify(folder) == requested_slug


def _sort_rows(rows: list[_CaptureRow], sort: str) -> None:
    rows.sort(key=lambda row: (row.note_id, row.relpath))
    if sort == "older":
        rows.sort(
            key=lambda row: (row.sort_dt is None, row.sort_dt.timestamp() if row.sort_dt else 0.0)
        )
    else:
        rows.sort(
            key=lambda row: (row.sort_dt is None, -row.sort_dt.timestamp() if row.sort_dt else 0.0)
        )


def list_captures(
    root: Path,
    status_filter: str | None,
    project_filter: str | None,
    sort: str,
    json_output: bool,
) -> None:
    """List captures vault-wide by frontmatter ``type: capture`` with no status hiding."""
    requested_slug: str | None = None
    if project_filter:
        project_root, _ = resolve_project_root(project_filter, root)
        requested_slug = slugify(project_root.name)

    rows: list[_CaptureRow] = []
    for path in iter_markdown(root):
        try:
            data = _read_capture_meta(path)
        except (OawError, OSError, UnicodeError) as exc:
            print(f"oaw: warning: {_relpath(path, root)}: {exc}", file=sys.stderr)
            continue
        if data is None:
            continue
        status = note_status(data)
        if status_filter is not None and status != status_filter:
            continue
        if requested_slug is not None and not _capture_matches_project(
            data, path, root, requested_slug
        ):
            continue
        body = _read_capture_body(path)
        rows.append(
            _CaptureRow(
                note_id=str(data.get("id", "")),
                status=status,
                created=_created_str(data),
                project=_project_str(data),
                title=title_from_body(path, body),
                body_chars=len(body),
                relpath=_relpath(path, root),
                sort_dt=_parse_created(data.get("created")),
            )
        )

    _sort_rows(rows, sort)

    if json_output:
        print(
            json.dumps(
                [
                    {
                        "id": row.note_id,
                        "status": row.status,
                        "created": row.created,
                        "project": row.project or None,
                        "title": row.title,
                        "body_chars": row.body_chars,
                        "path": row.relpath,
                    }
                    for row in rows
                ],
                ensure_ascii=False,
            )
        )
        return
    for row in rows:
        print(
            "\t".join(
                [
                    row.note_id,
                    row.status,
                    row.created,
                    row.project or "-",
                    row.title,
                    str(row.body_chars),
                    row.relpath,
                ]
            )
        )


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #


def _scalar_or_none(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _list_values(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key)
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def show_capture(root: Path, note_id: str, json_output: bool) -> None:
    """Display one capture note from any vault location."""
    match = resolve_id(note_id, root)
    actual_type = match.frontmatter.get("type")
    if actual_type != "capture":
        raise OawError(f"note is not a capture (type: {actual_type}): {match.relpath}")
    _text, _fm, body = read_note(match.path)
    data = match.frontmatter

    if json_output:
        print(
            json.dumps(
                {
                    "id": data.get("id") if isinstance(data.get("id"), str) else None,
                    "path": match.relpath,
                    "title": match.title,
                    "status": _scalar_or_none(data, "status"),
                    "created": _scalar_or_none(data, "created"),
                    "project": _scalar_or_none(data, "project"),
                    "area": _scalar_or_none(data, "area"),
                    "context": _scalar_or_none(data, "context"),
                    "outcome": _scalar_or_none(data, "outcome"),
                    "review_after": _scalar_or_none(data, "review_after"),
                    "destinations": _list_values(data, "destinations"),
                    "urls": _list_values(data, "urls"),
                    "session_ids": _list_values(data, "session-ids"),
                    "tags": _list_values(data, "tags"),
                    "body": body,
                    "body_chars": len(body),
                },
                ensure_ascii=False,
            )
        )
        return

    def _text_scalar(key: str) -> str:
        return _scalar_or_none(data, key) or "-"

    def _text_list(key: str) -> str:
        values = _list_values(data, key)
        return ", ".join(values) if values else "-"

    print(f"ID: {data.get('id') if isinstance(data.get('id'), str) else '-'}")
    print(f"Path: {match.relpath}")
    print(f"Title: {match.title}")
    print(f"Status: {_text_scalar('status')}")
    print(f"Created: {_text_scalar('created')}")
    print(f"Project: {_text_scalar('project')}")
    print(f"Area: {_text_scalar('area')}")
    print(f"Context: {_text_scalar('context')}")
    print(f"Outcome: {_text_scalar('outcome')}")
    print(f"Review-after: {_text_scalar('review_after')}")
    print(f"Destinations: {_text_list('destinations')}")
    print(f"URLs: {_text_list('urls')}")
    print(f"Session-ids: {_text_list('session-ids')}")
    print(f"Tags: {_text_list('tags')}")
    print()
    print(body, end="" if body.endswith("\n") else "\n")


# --------------------------------------------------------------------------- #
# triage
# --------------------------------------------------------------------------- #


def _blank_scalar(text: str, key: str) -> str:
    updated = set_frontmatter_scalar(text, key, "")
    return updated.replace(f"{key}: \n", f"{key}:\n", 1)


def _append_reciprocal(dest_text: str, capture: NoteMatch) -> str:
    if any(
        link_matches_note(link, capture, include_id=False) for link in parse_wikilinks(dest_text)
    ):
        return dest_text
    link = durable_wikilink(capture, capture.note_id)
    return append_to_section(dest_text, "Related", link)


def triage_capture(
    root: Path,
    note_id: str,
    new_status: str,
    reason: str | None,
    no_reason: bool,
    review_after: str | None,
    destinations: list[str],
    json_output: bool,
    allow_missing_session_id: bool,
) -> None:
    """Transition one canonical capture, writing all touched notes atomically."""
    references = scan_note_references(root)
    capture = resolve_id_from_references(note_id, root, references)
    actual_type = capture.frontmatter.get("type")
    if actual_type != "capture":
        raise OawError(f"note is not a capture (type: {actual_type}): {capture.relpath}")
    rel = capture.relpath
    if not rel.startswith(f"{CAPTURE_DIRECTORY.as_posix()}/"):
        raise OawError(
            f"capture triage only writes captures under {CAPTURE_DIRECTORY.as_posix()}/: {rel}"
        )
    if not capture.note_id:
        raise OawError(f"capture has no stable frontmatter id: {rel}")

    old_status = note_status(capture.frontmatter)
    if old_status == new_status:
        raise OawError(f"capture is already {new_status}: {capture.note_id}")

    provider, session_ref = detect_session(allow_missing_session_id)
    escape = session_ref == "session_id=unavailable"

    dest_matches: list[NoteMatch] = []
    for dest in destinations:
        match = resolve_id_from_references(dest, root, references)
        if not match.note_id:
            raise OawError(f"destination has no stable frontmatter id: {match.relpath}")
        if match.path == capture.path:
            raise OawError(f"capture cannot be its own destination: {capture.note_id}")
        dest_matches.append(match)

    existing_destinations = _list_values(capture.frontmatter, "destinations")
    if new_status == "triaged" and not dest_matches and not existing_destinations:
        raise OawError(
            "capture triage --status triaged requires at least one destination "
            "(stored or via --destination)"
        )

    capture_original = capture.path.read_text(encoding="utf-8")
    capture_text = set_frontmatter_scalar(capture_original, "status", new_status)
    if new_status == "incubating":
        assert review_after is not None
        capture_text = set_frontmatter_scalar(capture_text, "review_after", review_after)
    elif old_status == "incubating":
        capture_text = _blank_scalar(capture_text, "review_after")
    capture_text = append_session_id_frontmatter(capture_text, session_ref)

    dest_updates: dict[Path, tuple[str, str]] = {}
    for match in dest_matches:
        link = durable_wikilink(match, match.note_id)
        capture_text = append_frontmatter_list_value(capture_text, "destinations", link)
        if match.path in dest_updates:
            original, current = dest_updates[match.path]
        else:
            original = match.path.read_text(encoding="utf-8")
            current = original
        dest_updates[match.path] = (original, _append_reciprocal(current, capture))

    today = dt.date.today().isoformat()
    reason_text = reason if reason is not None else "reason omitted via --no-reason"
    identity_segment = "session unavailable (accepted)" if escape else f"`{session_ref}`"
    audit_line = f"- {today} - {provider} - {identity_segment} - {old_status} -> {new_status} - {reason_text}"
    capture_text = append_markdown_block_to_section(capture_text, "Triage", audit_line)

    transaction = VaultTransaction()
    transaction.stage(capture.path, capture_text, expected=capture_original)
    for dest_path, (original, updated) in dest_updates.items():
        transaction.stage(dest_path, updated, expected=original)
    transaction.commit()

    final_destinations = _list_values(
        parse_frontmatter(split_note(capture_text)[1]), "destinations"
    )
    review_after_out = review_after if new_status == "incubating" else None
    session_id_ref = None if escape else session_ref

    if json_output:
        print(
            json.dumps(
                {
                    "id": capture.note_id,
                    "path": rel,
                    "old_status": old_status,
                    "new_status": new_status,
                    "reason": reason,
                    "reason_omitted": no_reason,
                    "review_after": review_after_out,
                    "destinations": final_destinations,
                    "session_id": session_id_ref,
                },
                ensure_ascii=False,
            )
        )
        return
    print(f"Updated: {rel}")
    print(f"ID: {capture.note_id}")
    print(f"Status: {old_status} -> {new_status}")
    print(f"Path: {rel}")
