#!/usr/bin/env python3
"""Argparse assembly and dispatch for the OAW CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
        root = vault_root()
        if args.command == "resolve":
            output_resolve(
                resolve_id(args.id, root), args.full, args.path, args.meta, args.outline, args.json
            )
        elif args.command == "list":
            list_project(root, args.project, args.type, args.status, args.include_archived)
        elif args.command == "project":
            if args.project_command == "create":
                create_project(
                    root,
                    args.name,
                    args.goal,
                    args.alias,
                    args.repo,
                    args.tag,
                    args.template,
                    args.allow_missing_session_id,
                )
            else:
                parser.error("unknown project command")
        elif args.command == "research":
            if args.research_command == "scaffold":
                create_research_packet(
                    root, args.project, args.track, args.title, args.date, args.template, args.force
                )
            elif args.research_command == "start":
                start_research_run(root, args.project, args.track, args.source, args.url)
            else:
                parser.error("unknown research command")
        elif args.command == "task":
            if args.task_command == "note":
                append_task_note(
                    resolve_id(args.id, root),
                    root,
                    args.note,
                    args.checks,
                    args.allow_missing_session_id,
                )
            elif args.task_command == "create":
                create_task(
                    root,
                    args.project,
                    args.title,
                    args.from_capture,
                    args.start,
                    args.id,
                    args.status,
                    args.priority,
                    args.effort,
                    args.note,
                    args.tag,
                    args.allow_missing_session_id,
                )
            else:
                update_task(
                    resolve_id(args.id, root),
                    root,
                    args.status,
                    args.note,
                    args.checks,
                    args.allow_missing_session_id,
                )
        elif args.command == "note":
            if args.note_command == "session":
                append_note_session(
                    resolve_id(args.id, root), args.note, args.checks, args.allow_missing_session_id
                )
            elif args.note_command == "observe":
                update_note_observation(root, args.id, args.section, args.title, args.body)
            else:
                parser.error("unknown note command")
        elif args.command == "board":
            if args.board_command == "add":
                update_next_steps_board(
                    root,
                    args.column,
                    None,
                    next_steps_card(args.link, args.title, args.why, args.id),
                    False,
                )
            elif args.board_command == "move":
                update_next_steps_board(root, args.column, args.token, None, False)
            elif args.board_command == "done":
                update_next_steps_board(root, "Done", args.token, None, True)
            elif args.board_command == "ensure-backlog":
                ensure_project_backlog_column(root, args.project)
        elif args.command == "ingest":
            if args.ingest_command == "safe-export":
                safe_export_ingest(root, args.ingestion_root, args.destination, args.mode)
            else:
                parser.error("unknown ingest command")
        elif args.command == "link":
            if args.link_command == "check":
                link_check(root, args.left, args.right)
            elif args.link_command == "list":
                link_list(root, args.note)
            elif args.link_command == "ensure":
                link_ensure(root, args.source, args.target, args.section, args.label, args.write)
            elif args.link_command == "ensure-bidirectional":
                link_ensure_bidirectional(root, args.left, args.right, args.section, args.write)
            elif args.link_command == "lint":
                link_lint(root)
        elif args.command == "export":
            if args.export_command == "note":
                write_export_bundle(root, args.id, args.target, args.output_root, args.force)
            elif args.export_command == "validate":
                validate_export_bundle(args.bundle, args.target)
            else:
                parser.error("unknown export command")
        elif args.command == "session":
            if args.session_command == "lookup":
                session_lookup(
                    [
                        (hit.relpath, hit.note_id)
                        for hit in notes_containing_literal(root, args.session_id)
                    ],
                    args.session_id,
                    args.verbose,
                    args.codex_root,
                    args.claude_root,
                )
            elif args.session_command == "snapshot":
                if args.partial and args.complete:
                    raise OawError("--partial and --complete are mutually exclusive")
                session_snapshot(
                    root,
                    args.session_id,
                    args.slug,
                    args.date,
                    args.partial,
                    args.complete,
                    args.codex_only,
                    args.codex_thread,
                    args.codex_rollout,
                    args.claude_session,
                    args.grep,
                    args.output_root,
                    args.claude_root,
                    args.codex_root,
                    args.plugin_data_root,
                )
            else:
                parser.error("unknown session command")
        elif args.command == "retro":
            if args.retro_command == "create":
                create_retrospective(
                    root,
                    args.title,
                    args.summary,
                    args.date,
                    args.id,
                    args.force,
                    args.allow_missing_session_id,
                )
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
