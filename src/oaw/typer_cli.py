"""Temporary Typer frontend used to validate the CLI dependency boundary.

This module is intentionally not the installed entry point. The argparse adapter
in :mod:`oaw.cli` remains authoritative until the Typer migration is complete.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from .boards import ensure_project_backlog_column, next_steps_card, update_next_steps_board
from .errors import OawError
from .exports import validate_export_bundle, write_export_bundle
from .ingest import SAFE_EXPORT_DESTINATION, default_ingestion_root, safe_export_ingest
from .lifecycle import (
    PROJECT_INDEX_TEMPLATE,
    RESEARCH_PACKET_TEMPLATE,
    append_note_session,
    append_task_note,
    create_project,
    create_research_packet,
    create_task,
    start_research_run,
    update_task,
)
from .links import link_check, link_ensure, link_ensure_bidirectional, link_lint, link_list
from .resolver import list_project, notes_containing_literal, output_resolve, resolve_id, vault_root
from .retro import create_retrospective, update_note_observation
from .sessions import (
    default_claude_projects_root,
    default_codex_sessions_root,
    default_plugin_data_root,
    session_lookup,
    session_lookup_claude_root,
    session_lookup_codex_root,
)
from .snapshot import session_snapshot


def _app(help_text: str) -> typer.Typer:
    return typer.Typer(add_completion=False, no_args_is_help=False, help=help_text)


app = _app("Temporary Typer frontend for migration tests.")
project_app = _app("Project workspace lifecycle")
research_app = _app("Research packet utilities")
task_app = _app("Project task lifecycle")
note_app = _app("Append session traces or observations to resolved notes")
board_app = _app("Update the cross-project Next steps board")
ingest_app = _app("Ingest approved handoff files")
link_app = _app("Inspect and maintain durable wikilinks")
export_app = _app("Safe outbound note export utilities")
session_app = _app("Session artifact utilities")
retro_app = _app("Retrospective note utilities")

ARGPARSE_CONFLICT_USAGE = {
    "task create": (
        "usage: oaw task create [-h] [--project PROJECT] [--title TITLE]",
        "                       [--from-capture FROM_CAPTURE] [--start] [--id ID]",
        "                       [--status {backlog,todo}] [--priority {1,2,3}]",
        "                       [--effort {S,M,L}] [--note NOTE] [--tag TAG]",
        "                       [--allow-missing-session-id]",
    ),
    "ingest safe-export": (
        "usage: oaw ingest safe-export [-h] [--ingestion-root INGESTION_ROOT]",
        "                              [--destination DESTINATION]",
        "                              [--dry-run | --write]",
    ),
    "link ensure": (
        "usage: oaw link ensure [-h] [--section SECTION] [--label LABEL]",
        "                       [--dry-run | --write]",
        "                       source target",
    ),
    "link ensure-bidirectional": (
        "usage: oaw link ensure-bidirectional [-h] [--section SECTION]",
        "                                     [--dry-run | --write]",
        "                                     left right",
    ),
}


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"


class TaskEffort(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


app.add_typer(project_app, name="project")
app.add_typer(research_app, name="research")
app.add_typer(task_app, name="task")
app.add_typer(note_app, name="note")
app.add_typer(board_app, name="board")
app.add_typer(ingest_app, name="ingest")
app.add_typer(link_app, name="link")
app.add_typer(export_app, name="export")
app.add_typer(session_app, name="session")
app.add_typer(retro_app, name="retro")


def _run(action: Callable[[], None]) -> None:
    """Keep domain errors on argparse's stable stderr and exit-code contract."""
    try:
        action()
    except OawError as exc:
        typer.echo(f"oaw: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _argument_conflict(
    context: typer.Context,
    command: str,
    left: tuple[str, str],
    right: tuple[str, str],
) -> None:
    """Emit argparse's stable mutual-exclusion diagnostic and usage exit."""
    positions = {name: index for index, name in enumerate(context.params)}
    later, earlier = (left, right) if positions[left[0]] > positions[right[0]] else (right, left)
    for line in ARGPARSE_CONFLICT_USAGE[command]:
        typer.echo(line, err=True)
    typer.echo(
        f"oaw {command}: error: argument {later[1]}: not allowed with argument {earlier[1]}",
        err=True,
    )
    raise typer.Exit(code=2)


@app.callback()
def root() -> None:
    """Temporary Typer frontend for migration tests."""


@app.command(help="resolve obs:<ID> or <ID>")
def resolve(
    note_id: Annotated[str, typer.Argument(help="resolve obs:<ID> or <ID>")],
    full: Annotated[bool, typer.Option("--full")] = False,
    path: Annotated[bool, typer.Option("--path")] = False,
    meta: Annotated[bool, typer.Option("--meta")] = False,
    outline: Annotated[bool, typer.Option("--outline")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resolve an Obsidian note ID through the shared resolver service."""
    _run(
        lambda: output_resolve(
            resolve_id(note_id, vault_root()), full, path, meta, outline, json_output
        )
    )


@app.command("list", help="list project notes")
def list_notes(
    project: Annotated[str, typer.Option("--project", help="project name under Projects/.")],
    note_type: Annotated[
        str, typer.Option("--type", help="frontmatter type to list, default: task")
    ] = "task",
    status: Annotated[str | None, typer.Option("--status", help="optional status filter")] = None,
    include_archived: Annotated[
        bool, typer.Option("--include-archived", help="include archived notes without --status")
    ] = False,
) -> None:
    _run(lambda: list_project(vault_root(), project, note_type, status, include_archived))


@project_app.command("create", help="create a project Index.md from the vault template")
def project_create(
    name: Annotated[str, typer.Option("--name", help="safe project folder name")],
    alias: Annotated[str, typer.Option("--alias", help="uppercase 2-8 character alias")],
    goal: Annotated[str, typer.Option("--goal", help="single-line project outcome")],
    repo: Annotated[
        str | None, typer.Option("--repo", help="optional repository path or URL")
    ] = None,
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="extra project tag; repeatable")
    ] = None,
    template: Annotated[
        str, typer.Option("--template", help="vault-relative project template path")
    ] = PROJECT_INDEX_TEMPLATE.as_posix(),
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _run(
        lambda: create_project(
            vault_root(), name, goal, alias, repo, tag, template, allow_missing_session_id
        )
    )


@research_app.command(
    "scaffold", help="create Prompt.md and Synthesis.md from the research template"
)
def research_scaffold(
    project: Annotated[str, typer.Option("--project", help="project alias or Projects/ folder")],
    track: Annotated[
        str, typer.Option("--track", help="path below the project's Research/ folder")
    ],
    title: Annotated[str, typer.Option("--title", help="provider-facing topic title")],
    date: Annotated[str | None, typer.Option("--date", help="creation date (YYYY-MM-DD)")] = None,
    template: Annotated[
        str, typer.Option("--template", help="vault-relative research packet template path")
    ] = RESEARCH_PACKET_TEMPLATE.as_posix(),
    force: Annotated[
        bool, typer.Option("--force", help="replace Prompt.md, never Synthesis.md")
    ] = False,
) -> None:
    _run(lambda: create_research_packet(vault_root(), project, track, title, date, template, force))


@research_app.command(
    "start", help="register one launched provider run in an existing research packet"
)
def research_start(
    project: Annotated[str, typer.Option("--project", help="project alias or Projects/ folder")],
    track: Annotated[
        str, typer.Option("--track", help="path below the project's Research/ folder")
    ],
    source: Annotated[str, typer.Option("--source", help="safe human source label")],
    url: Annotated[str, typer.Option("--url", help="launched run's HTTP(S) URL")],
) -> None:
    _run(lambda: start_research_run(vault_root(), project, track, source, url))


def _task_transition(
    note_id: str, note: str, checks: str | None, allow_missing_session_id: bool, status: str
) -> None:
    root_path = vault_root()
    _run(
        lambda: update_task(
            resolve_id(note_id, root_path),
            root_path,
            status,
            note,
            checks,
            allow_missing_session_id,
        )
    )


@task_app.command("backlog")
def task_backlog(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _task_transition(note_id, note, checks, allow_missing_session_id, "backlog")


@task_app.command("promote")
def task_promote(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _task_transition(note_id, note, checks, allow_missing_session_id, "todo")


@task_app.command("start")
def task_start(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _task_transition(note_id, note, checks, allow_missing_session_id, "active")


@task_app.command("complete")
def task_complete(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _task_transition(note_id, note, checks, allow_missing_session_id, "done")


@task_app.command("note", help="append an agent session note without changing status")
def task_note(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    root_path = vault_root()
    _run(
        lambda: append_task_note(
            resolve_id(note_id, root_path), root_path, note, checks, allow_missing_session_id
        )
    )


@task_app.command("create", help="create a new project task note")
def task_create(
    context: typer.Context,
    project: Annotated[
        str | None, typer.Option("--project", help="project alias or folder name")
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="task title; defaults to capture title")
    ] = None,
    from_capture: Annotated[
        str | None, typer.Option("--from-capture", help="capture ID to promote")
    ] = None,
    start: Annotated[
        bool, typer.Option("--start", help="create promoted task directly as active")
    ] = False,
    requested_id: Annotated[str | None, typer.Option("--id", help="override task ID")] = None,
    status_values: Annotated[
        list[TaskStatus] | None,
        typer.Option("--status", help="backlog or todo"),
    ] = None,
    priority_values: Annotated[list[int] | None, typer.Option("--priority", min=1, max=3)] = None,
    effort_values: Annotated[
        list[TaskEffort] | None,
        typer.Option("--effort", help="S, M, or L"),
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="initial problem statement")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="extra tag; repeatable")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    if start and status_values:
        _argument_conflict(
            context, "task create", ("start", "--start"), ("status_values", "--status")
        )
    status = status_values[-1].value if status_values else "backlog"
    priority = priority_values[-1] if priority_values else None
    effort = effort_values[-1].value if effort_values else None
    _run(
        lambda: create_task(
            vault_root(),
            project,
            title,
            from_capture,
            start,
            requested_id,
            status,
            priority,
            effort,
            note,
            tag,
            allow_missing_session_id,
        )
    )


@note_app.command("session", help="append an Agent sessions entry")
def note_session(
    note_id: Annotated[str, typer.Argument()],
    note: Annotated[str, typer.Option("--note")],
    checks: Annotated[str | None, typer.Option("--checks")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    root_path = vault_root()
    _run(
        lambda: append_note_session(
            resolve_id(note_id, root_path), note, checks, allow_missing_session_id
        )
    )


@note_app.command("observe", help="append a dated observation block")
def note_observe(
    note_id: Annotated[str, typer.Argument()],
    title: Annotated[str, typer.Option("--title")],
    body: Annotated[str, typer.Option("--body")],
    section: Annotated[str, typer.Option("--section", help="target heading")] = "Observations",
) -> None:
    _run(lambda: update_note_observation(vault_root(), note_id, section, title, body))


@board_app.command("add", help="add a linked card to Projects/Next steps.md")
def board_add(
    column: Annotated[str, typer.Option("--column")],
    link: Annotated[str, typer.Option("--link", help="vault-relative note path")],
    title: Annotated[str, typer.Option("--title")],
    why: Annotated[str, typer.Option("--why", help="one-line routing note")],
    card_id: Annotated[str, typer.Option("--id", help="stable reference ID")],
) -> None:
    _run(
        lambda: update_next_steps_board(
            vault_root(), column, None, next_steps_card(link, title, why, card_id), False
        )
    )


@board_app.command("move", help="move a matching card to another column")
def board_move(
    token: Annotated[str, typer.Argument(help="stable ID or unique card text")],
    column: Annotated[str, typer.Option("--column")],
) -> None:
    _run(lambda: update_next_steps_board(vault_root(), column, token, None, False))


@board_app.command("done", help="move a matching card to Done and check it")
def board_done(token: Annotated[str, typer.Argument(help="stable ID or unique card text")]) -> None:
    _run(lambda: update_next_steps_board(vault_root(), "Done", token, None, True))


@board_app.command("ensure-backlog", help="add a Backlog column to a project board if missing")
def board_ensure_backlog(project: Annotated[str, typer.Option("--project")]) -> None:
    _run(lambda: ensure_project_backlog_column(vault_root(), project))


@ingest_app.command("safe-export", help="ingest frontmatter-approved Markdown files")
def ingest_safe_export(
    context: typer.Context,
    ingestion_root: Annotated[
        Path | None, typer.Option("--ingestion-root", help="handoff folder to scan")
    ] = None,
    destination: Annotated[
        str, typer.Option("--destination", help="vault-relative destination folder")
    ] = SAFE_EXPORT_DESTINATION.as_posix(),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="preview without moving files")
    ] = False,
    write: Annotated[
        bool, typer.Option("--write", help="ingest safe files and quarantine rejects")
    ] = False,
) -> None:
    if dry_run and write:
        _argument_conflict(
            context, "ingest safe-export", ("dry_run", "--dry-run"), ("write", "--write")
        )
    _run(
        lambda: safe_export_ingest(
            vault_root(),
            ingestion_root if ingestion_root is not None else default_ingestion_root(),
            destination,
            "write" if write else "dry-run",
        )
    )


@link_app.command("check", help="check whether two notes link to each other")
def link_check_command(
    left: Annotated[str, typer.Argument()], right: Annotated[str, typer.Argument()]
) -> None:
    _run(lambda: link_check(vault_root(), left, right))


@link_app.command("list", help="list explicit wikilinks from a note")
def link_list_command(note: Annotated[str, typer.Argument()]) -> None:
    _run(lambda: link_list(vault_root(), note))


@link_app.command("ensure", help="ensure one durable wikilink exists")
def link_ensure_command(
    context: typer.Context,
    source: Annotated[str, typer.Argument()],
    target: Annotated[str, typer.Argument()],
    section: Annotated[str, typer.Option("--section")] = "Related",
    label: Annotated[str | None, typer.Option("--label")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="preview only")] = False,
    write: Annotated[bool, typer.Option("--write", help="write the edit")] = False,
) -> None:
    if dry_run and write:
        _argument_conflict(context, "link ensure", ("dry_run", "--dry-run"), ("write", "--write"))
    _run(lambda: link_ensure(vault_root(), source, target, section, label, write))


@link_app.command("ensure-bidirectional", help="ensure durable links in both directions")
def link_ensure_bidirectional_command(
    context: typer.Context,
    left: Annotated[str, typer.Argument()],
    right: Annotated[str, typer.Argument()],
    section: Annotated[str, typer.Option("--section")] = "Related",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="preview only")] = False,
    write: Annotated[bool, typer.Option("--write", help="write the edits")] = False,
) -> None:
    if dry_run and write:
        _argument_conflict(
            context,
            "link ensure-bidirectional",
            ("dry_run", "--dry-run"),
            ("write", "--write"),
        )
    _run(lambda: link_ensure_bidirectional(vault_root(), left, right, section, write))


@link_app.command("lint", help="suggest durable replacements for opaque ID links")
def link_lint_command() -> None:
    _run(lambda: link_lint(vault_root()))


@export_app.command("note", help="export a marked-safe note bundle")
def export_note(
    note_id: Annotated[str, typer.Argument()],
    target: Annotated[str, typer.Option("--target", help="required export target")] = "work",
    output_root: Annotated[Path | None, typer.Option("--output-root")] = None,
    force: Annotated[bool, typer.Option("--force", help="replace an existing bundle")] = False,
) -> None:
    _run(lambda: write_export_bundle(vault_root(), note_id, target, output_root, force))


@export_app.command("validate", help="validate an exported bundle")
def export_validate(
    bundle: Annotated[Path, typer.Argument()],
    target: Annotated[str | None, typer.Option("--target", help="expected export target")] = None,
) -> None:
    _run(lambda: validate_export_bundle(bundle, target))


@session_app.command("lookup", help="find notes or artifacts for a session ID")
def session_lookup_command(
    session_id: Annotated[str, typer.Argument()],
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
    codex_root: Annotated[Path | None, typer.Option("--codex-root")] = None,
    claude_root: Annotated[Path | None, typer.Option("--claude-root")] = None,
) -> None:
    def action() -> None:
        clean_session_id = session_id.strip()
        if not clean_session_id:
            raise OawError("empty session ID")
        root_path = vault_root()
        session_lookup(
            [
                (hit.relpath, hit.note_id)
                for hit in notes_containing_literal(root_path, clean_session_id)
            ],
            clean_session_id,
            verbose,
            codex_root if codex_root is not None else session_lookup_codex_root(),
            claude_root if claude_root is not None else session_lookup_claude_root(),
        )

    _run(action)


@session_app.command("snapshot", help="copy session artifacts for retrospectives")
def session_snapshot_command(
    session_id: Annotated[str, typer.Argument()],
    slug: Annotated[str | None, typer.Option("--slug")] = None,
    date: Annotated[str | None, typer.Option("--date")] = None,
    partial: Annotated[bool, typer.Option("--partial")] = False,
    complete: Annotated[bool, typer.Option("--complete")] = False,
    codex_only: Annotated[bool, typer.Option("--codex-only")] = False,
    codex_thread: Annotated[list[str] | None, typer.Option("--codex-thread")] = None,
    codex_rollout: Annotated[list[str] | None, typer.Option("--codex-rollout")] = None,
    claude_session: Annotated[list[str] | None, typer.Option("--claude-session")] = None,
    grep: Annotated[list[str] | None, typer.Option("--grep")] = None,
    output_root: Annotated[Path | None, typer.Option("--output-root")] = None,
    claude_root: Annotated[Path | None, typer.Option("--claude-root")] = None,
    codex_root: Annotated[Path | None, typer.Option("--codex-root")] = None,
    plugin_data_root: Annotated[Path | None, typer.Option("--plugin-data-root")] = None,
) -> None:
    def action() -> None:
        if partial and complete:
            raise OawError("--partial and --complete are mutually exclusive")
        session_snapshot(
            vault_root(),
            session_id,
            slug,
            date,
            partial,
            complete,
            codex_only,
            codex_thread,
            codex_rollout,
            claude_session,
            grep,
            output_root,
            claude_root if claude_root is not None else default_claude_projects_root(),
            codex_root if codex_root is not None else default_codex_sessions_root(),
            plugin_data_root if plugin_data_root is not None else default_plugin_data_root(),
        )

    _run(action)


@retro_app.command("create", help="create a dated retrospective draft")
def retro_create(
    title: Annotated[str, typer.Option("--title")],
    summary: Annotated[str, typer.Option("--summary")] = "",
    date: Annotated[str | None, typer.Option("--date")] = None,
    requested_id: Annotated[str | None, typer.Option("--id")] = None,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing note")] = False,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    _run(
        lambda: create_retrospective(
            vault_root(), title, summary, date, requested_id, force, allow_missing_session_id
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Invoke the temporary Click command with the stable in-process contract."""
    try:
        app(args=argv, prog_name="oaw")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
