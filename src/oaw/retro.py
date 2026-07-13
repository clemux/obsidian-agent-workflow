"""Retrospective and note-observation commands."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

from .errors import OawError
from .notes import append_markdown_block_to_section, normalize_heading
from .resolver import iter_markdown, note_match, resolve_id
from .sessions import detect_session


def append_observation_entry(text: str, section: str, title: str, body: str) -> str:
    clean_title = title.strip()
    clean_body = body.strip()
    if not clean_title:
        raise OawError("observation title must not be empty")
    if not clean_body:
        raise OawError("observation body must not be empty")
    today = dt.date.today().isoformat()
    return append_markdown_block_to_section(
        text,
        section,
        f"### {today} - {clean_title}\n\n{clean_body}",
    )


def update_note_observation(root: Path, raw_id: str, section: str, title: str, body: str) -> None:
    match = resolve_id(raw_id, root)
    text = match.path.read_text(encoding="utf-8")
    text = append_observation_entry(text, section, title, body)
    match.path.write_text(text, encoding="utf-8")
    print(f"Updated: {match.relpath}")
    print(f"Section: {normalize_heading(section)}")


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "session"


def validate_date(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise OawError("date must use YYYY-MM-DD") from exc
    return value


def create_retrospective(
    root: Path,
    title_value: str,
    summary: str,
    date_value: str | None,
    requested_id: str | None,
    force: bool,
    allow_missing_session_id: bool,
) -> None:
    title = title_value.strip()
    if not title:
        raise OawError("retro create requires a non-empty --title")
    date = validate_date(date_value or dt.date.today().isoformat())
    provider, session_ref = detect_session(allow_missing_session_id)
    session_id = session_ref.split("=", 1)[1] if "=" in session_ref else session_ref
    slug = slugify(title)
    note_id = requested_id.strip() if requested_id else f"AGT-RETRO-{date}-{slug}"
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
    if path.exists() and not force:
        raise OawError(f"retrospective already exists: {relpath.as_posix()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = summary.strip() if summary else ""
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
