"""Creation of durable agent-feedback notes."""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from enum import Enum
from pathlib import Path
from typing import TextIO

from .errors import OawError
from .notes import write_new_note_atomic
from .resolver import matches_from_references, scan_note_references
from .sessions import detect_session
from .tags import creation_tag_block


class FeedbackType(str, Enum):
    PAIN = "pain"
    VERIFIED = "verified"
    IDEA = "idea"
    BUG = "bug"


FEEDBACK_TYPES = tuple(member.value for member in FeedbackType)
FEEDBACK_DIRECTORY = Path("Agents/Feedback")
EXPLICIT_FEEDBACK_ID = re.compile(r"^AGT-FDBK-[a-z0-9]+(?:-[a-z0-9]+)*$")
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "feedback"


def validate_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise OawError("date must use YYYY-MM-DD")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise OawError("date must use YYYY-MM-DD") from exc
    return value


def feedback_title(value: str) -> str:
    title = value.strip()
    title_stem = title.split(".", maxsplit=1)[0].casefold()
    if (
        not title
        or title != value
        or title in {".", ".."}
        or title.startswith(".")
        or title.endswith((".", " "))
        or title_stem in WINDOWS_RESERVED_NAMES
        or any(character in title for character in '/\\:*?"<>|')
        or any(unicodedata.category(character).startswith("C") for character in title)
    ):
        raise OawError("feedback title must be a non-empty safe filename title")
    return title


def scalar(value: str) -> str:
    """Use JSON strings, which are valid YAML strings without scalar ambiguity."""
    return json.dumps(value, ensure_ascii=False)


def read_feedback_body(body: str | None, body_file: str | None, stdin: TextIO) -> str:
    """Read exactly one non-empty feedback body from an option, file, or stdin."""
    if body is not None and body_file is not None:
        raise OawError("feedback create accepts exactly one of --body or --body-file")
    if body is None and body_file is None:
        raise OawError("feedback create requires exactly one of --body or --body-file")
    if body_file is not None:
        try:
            raw = stdin.read() if body_file == "-" else Path(body_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise OawError(f"could not read feedback body file: {body_file}") from exc
    else:
        assert body is not None
        raw = body
    if not raw.strip():
        raise OawError("feedback body must not be empty")
    return raw


def create_feedback(
    root: Path,
    title_value: str,
    feedback_type: str,
    scope_value: str,
    body: str,
    command: str | None,
    tags: list[str] | None,
    requested_id: str | None,
    date_value: str | None,
    allow_missing_session_id: bool,
) -> None:
    """Write one feedback note only after every collision and provenance check passes."""
    title = feedback_title(title_value)
    if feedback_type not in FEEDBACK_TYPES:
        raise OawError(f"feedback type must be one of: {', '.join(FEEDBACK_TYPES)}")
    scope = scope_value.strip()
    if not scope or "\n" in scope_value or "\r" in scope_value:
        raise OawError("feedback scope must be a non-empty single-line value")
    if command is not None and ("\n" in command or "\r" in command):
        raise OawError("feedback command must be a single-line value")
    clean_command = command.strip() if command is not None else None
    if command is not None and not clean_command:
        raise OawError("feedback command must be a non-empty single-line value")
    date = validate_date(date_value or dt.date.today().isoformat())
    note_id = requested_id if requested_id is not None else f"AGT-FDBK-{slugify(title)}"
    if not EXPLICIT_FEEDBACK_ID.fullmatch(note_id):
        raise OawError("feedback --id must match AGT-FDBK-<safe-slug>")
    relpath = FEEDBACK_DIRECTORY / f"{date} {title}.md"
    path = root / relpath
    references = scan_note_references(root)
    conflicts = matches_from_references(note_id, references)
    if conflicts:
        paths = "\n".join(f"  {match.relpath} ({match.matched_by})" for match in conflicts)
        raise OawError(f"id '{note_id}' is already in use:\n{paths}")
    if path.exists():
        raise OawError(f"feedback note already exists: {relpath.as_posix()}")
    provider, session_ref = detect_session(allow_missing_session_id)
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else ""
    lines = [
        "---",
        f"type: {feedback_type}",
        "status: backlog",
        f"scope: {scalar(scope)}",
        f"id: {scalar(note_id)}",
        "aliases:",
        f"  - {scalar(note_id)}",
        *creation_tag_block(("agent-feedback",), tags),
    ]
    if clean_command is not None:
        lines.append(f"command: {scalar(clean_command)}")
    if session_id and session_id != "unavailable":
        lines += ["session-ids:", f"  - {scalar(session_id)}"]
    frontmatter = "\n".join([*lines, "---"])
    separator = "" if body.endswith("\n\n") else "\n" if body.endswith("\n") else "\n\n"
    note_text = (
        f"{frontmatter}\n\n# {title}\n\n## Feedback\n\n{body}{separator}"
        "## Agent sessions\n\n"
        f"- {date} - {provider} - `{session_ref}` - Created feedback note.\n"
    )
    try:
        write_new_note_atomic(path, note_text)
    except FileExistsError as exc:
        raise OawError(f"feedback note already exists: {relpath.as_posix()}") from exc
    except OSError as exc:
        raise OawError(f"could not create feedback note: {relpath.as_posix()}: {exc}") from exc
    print(f"Created: {relpath.as_posix()}")
    print(f"ID: {note_id}")
    print("Status: backlog")
