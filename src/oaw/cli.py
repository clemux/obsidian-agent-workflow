"""Typer composition root and dispatch for the OAW CLI."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from typer._click import exceptions as click_exceptions
from typer._click import globals as click_globals
from typer._click import types as click_types
from typer.core import TyperGroup
from typer.main import get_command

from .boards import ensure_project_backlog_column, next_steps_card, update_next_steps_board
from .errors import OawError
from .exports import validate_export_bundle, write_export_bundle
from .feedback import FEEDBACK_TYPES, FeedbackType, create_feedback, read_feedback_body
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

USAGE_BY_COMMAND = {
    "oaw": "usage: oaw [-h]\n"
    "           {resolve,list,project,research,task,note,board,ingest,link,export,session,retro,feedback} ...\n",
    "oaw resolve": "usage: oaw resolve [-h] [--full] [--path] [--meta] [--outline] [--json] id\n",
    "oaw list": "usage: oaw list [-h] --project PROJECT [--type TYPE] [--status STATUS]\n"
    "                [--include-archived]\n",
    "oaw project": "usage: oaw project [-h] {create} ...\n",
    "oaw project create": "usage: oaw project create [-h] --name NAME --alias ALIAS --goal GOAL\n"
    "                          [--repo REPO] [--tag TAG] [--template TEMPLATE]\n"
    "                          [--allow-missing-session-id]\n",
    "oaw research": "usage: oaw research [-h] {scaffold,start} ...\n",
    "oaw research scaffold": "usage: oaw research scaffold [-h] --project PROJECT --track TRACK\n"
    "                             --title TITLE [--date DATE] [--template TEMPLATE]\n"
    "                             [--force]\n",
    "oaw research start": "usage: oaw research start [-h] --project PROJECT --track TRACK --source SOURCE\n"
    "                          --url URL\n",
    "oaw task": "usage: oaw task [-h] {backlog,promote,start,complete,note,create} ...\n",
    "oaw task backlog": "usage: oaw task backlog [-h] --note NOTE [--checks CHECKS]\n"
    "                        [--allow-missing-session-id]\n"
    "                        id\n",
    "oaw task promote": "usage: oaw task promote [-h] --note NOTE [--checks CHECKS]\n"
    "                        [--allow-missing-session-id]\n"
    "                        id\n",
    "oaw task start": "usage: oaw task start [-h] --note NOTE [--checks CHECKS]\n"
    "                      [--allow-missing-session-id]\n"
    "                      id\n",
    "oaw task complete": "usage: oaw task complete [-h] --note NOTE [--checks CHECKS]\n"
    "                         [--allow-missing-session-id]\n"
    "                         id\n",
    "oaw task note": "usage: oaw task note [-h] --note NOTE [--checks CHECKS]\n"
    "                     [--allow-missing-session-id]\n"
    "                     id\n",
    "oaw task create": "usage: oaw task create [-h] [--project PROJECT] [--title TITLE]\n"
    "                       [--from-capture FROM_CAPTURE] [--start] [--id ID]\n"
    "                       [--status {backlog,todo}] [--priority {1,2,3}]\n"
    "                       [--effort {S,M,L}] [--note NOTE] [--tag TAG]\n"
    "                       [--allow-missing-session-id]\n",
    "oaw note": "usage: oaw note [-h] {session,observe} ...\n",
    "oaw note session": "usage: oaw note session [-h] --note NOTE [--checks CHECKS]\n"
    "                        [--allow-missing-session-id]\n"
    "                        id\n",
    "oaw note observe": "usage: oaw note observe [-h] [--section SECTION] --title TITLE --body BODY id\n",
    "oaw board": "usage: oaw board [-h] {add,move,done,ensure-backlog} ...\n",
    "oaw board add": "usage: oaw board add [-h] --column COLUMN --link LINK --title TITLE --why WHY\n"
    "                     --id ID\n",
    "oaw board move": "usage: oaw board move [-h] --column COLUMN token\n",
    "oaw board done": "usage: oaw board done [-h] token\n",
    "oaw board ensure-backlog": "usage: oaw board ensure-backlog [-h] --project PROJECT\n",
    "oaw ingest": "usage: oaw ingest [-h] {safe-export} ...\n",
    "oaw ingest safe-export": "usage: oaw ingest safe-export [-h] [--ingestion-root INGESTION_ROOT]\n"
    "                              [--destination DESTINATION] [--dry-run |\n"
    "                              --write]\n",
    "oaw link": "usage: oaw link [-h] {check,list,ensure,ensure-bidirectional,lint} ...\n",
    "oaw link check": "usage: oaw link check [-h] left right\n",
    "oaw link list": "usage: oaw link list [-h] note\n",
    "oaw link ensure": "usage: oaw link ensure [-h] [--section SECTION] [--label LABEL] [--dry-run |\n"
    "                       --write]\n"
    "                       source target\n",
    "oaw link ensure-bidirectional": "usage: oaw link ensure-bidirectional [-h] [--section SECTION] [--dry-run |\n"
    "                                     --write]\n"
    "                                     left right\n",
    "oaw link lint": "usage: oaw link lint [-h]\n",
    "oaw export": "usage: oaw export [-h] {note,validate} ...\n",
    "oaw export note": "usage: oaw export note [-h] [--target TARGET] [--output-root OUTPUT_ROOT]\n"
    "                       [--force]\n"
    "                       id\n",
    "oaw export validate": "usage: oaw export validate [-h] [--target TARGET] bundle\n",
    "oaw session": "usage: oaw session [-h] {lookup,snapshot} ...\n",
    "oaw session lookup": "usage: oaw session lookup [-h] [--verbose] [--codex-root CODEX_ROOT]\n"
    "                          [--claude-root CLAUDE_ROOT]\n"
    "                          session_id\n",
    "oaw session snapshot": "usage: oaw session snapshot [-h] [--slug SLUG] [--date DATE] [--partial]\n"
    "                            [--complete] [--codex-only]\n"
    "                            [--codex-thread CODEX_THREAD]\n"
    "                            [--codex-rollout CODEX_ROLLOUT]\n"
    "                            [--claude-session CLAUDE_SESSION] [--grep GREP]\n"
    "                            [--output-root OUTPUT_ROOT]\n"
    "                            [--claude-root CLAUDE_ROOT]\n"
    "                            [--codex-root CODEX_ROOT]\n"
    "                            [--plugin-data-root PLUGIN_DATA_ROOT]\n"
    "                            session_id\n",
    "oaw retro": "usage: oaw retro [-h] {create} ...\n",
    "oaw retro create": "usage: oaw retro create [-h] --title TITLE [--summary SUMMARY] [--date DATE]\n"
    "                        [--id ID] [--force] [--allow-missing-session-id]\n",
    "oaw feedback": "usage: oaw feedback [-h] {create} ...\n",
    "oaw feedback create": "usage: oaw feedback create [-h] --title TITLE --type {pain,verified,idea,bug}\n"
    "                           --scope SCOPE [--body BODY | --body-file BODY_FILE]\n"
    "                           [--command COMMAND] [--tag TAG] [--id ID] [--date DATE]\n"
    "                           [--allow-missing-session-id]\n",
}

SUBCOMMAND_DESTINATIONS = {
    "oaw": "command",
    "oaw project": "project_command",
    "oaw research": "research_command",
    "oaw task": "task_command",
    "oaw note": "note_command",
    "oaw board": "board_command",
    "oaw ingest": "ingest_command",
    "oaw link": "link_command",
    "oaw export": "export_command",
    "oaw session": "session_command",
    "oaw retro": "retro_command",
    "oaw feedback": "feedback_command",
}

ARGUMENT_NAMES = {
    "note_id": "id",
}

ARGPARSE_CHOICES = {
    "status": ("backlog", "todo"),
    "priority": ("1", "2", "3"),
    "effort": ("S", "M", "L"),
    "feedback_type": FEEDBACK_TYPES,
}

NEGATIVE_NUMBER = re.compile(r"-(?:\d+(?:\.\d*)?|\.\d+)$")


class StableTyperGroup(TyperGroup):
    """Run Click parsing while retaining the established usage-error contract."""

    @staticmethod
    def _option_expectation(command: Any, value: str) -> tuple[int, bool] | None:
        """Return a known option's arity and whether it accepts numeric values."""
        option_name, separator, _ = value.partition("=")
        for param in command.params:
            if option_name not in param.opts:
                continue
            if getattr(param, "is_flag", False) or getattr(param, "count", False):
                return (0, False)
            accepts_negative = isinstance(
                param.type, (click_types.IntParamType, click_types.FloatParamType)
            )
            return (max(param.nargs - int(bool(separator)), 0), accepts_negative)
        return None

    def _help_args(self, raw_args: list[str]) -> list[str] | None:
        """Route eager help while honoring known options' value arity."""
        command: Any = self
        path: list[str] = []
        pending_values = 0
        pending_accepts_negative = False
        options_enabled = True
        for index, value in enumerate(raw_args):
            if pending_values:
                is_negative_value = pending_accepts_negative and NEGATIVE_NUMBER.fullmatch(value)
                if value == "--" or (value.startswith("-") and not is_negative_value):
                    return raw_args[:index]
                pending_values -= 1
                continue
            if options_enabled and value in {"-h", "--help"}:
                return [*path, "--help"]
            if options_enabled and value == "--":
                options_enabled = False
                continue
            if options_enabled and value.startswith("-"):
                option_expectation = self._option_expectation(command, value)
                if option_expectation is not None:
                    pending_values, pending_accepts_negative = option_expectation
                continue
            if not isinstance(command, TyperGroup):
                continue
            next_command = command.commands.get(value)
            if next_command is None:
                return None
            path.append(value)
            command = next_command
        return None

    @staticmethod
    def _error_message(exc: click_exceptions.UsageError) -> str:
        ctx = exc.ctx
        command_path = ctx.command_path if ctx is not None else "oaw"
        message = exc.format_message()
        if isinstance(exc, click_exceptions.MissingParameter) and ctx is not None:
            missing: list[str] = []
            for param in ctx.command.params:
                if param.name is None:
                    continue
                if not param.required or ctx.params.get(param.name) is not None:
                    continue
                missing.append(
                    param.opts[0]
                    if param.opts[0].startswith("--")
                    else ARGUMENT_NAMES.get(param.name, param.name)
                )
            if missing:
                return f"the following arguments are required: {', '.join(missing)}"
        if isinstance(exc, click_exceptions.BadParameter) and exc.param is not None:
            param = exc.param
            if param.name in ARGPARSE_CHOICES:
                choices = ARGPARSE_CHOICES[param.name]
                if exc.message.startswith("'"):
                    invalid = exc.message.split("'", maxsplit=2)[1]
                else:
                    invalid = exc.message.split(maxsplit=1)[0]
                return (
                    f"argument {param.opts[0]}: invalid choice: '{invalid}' "
                    f"(choose from {', '.join(choices)})"
                )
        if message == "Missing command.":
            return f"the following arguments are required: {SUBCOMMAND_DESTINATIONS[command_path]}"
        if (
            message.startswith("No such command ")
            and ctx is not None
            and isinstance(ctx.command, TyperGroup)
        ):
            invalid = message.removeprefix("No such command ").removesuffix(".")
            choices = ", ".join(ctx.command.commands)
            return f"argument {SUBCOMMAND_DESTINATIONS[command_path]}: invalid choice: {invalid} (choose from {choices})"
        return message

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_args = list(args) if args is not None else sys.argv[1:]
        prog_name = "oaw"
        if any(value in {"-h", "--help"} for value in raw_args):
            help_args = self._help_args(raw_args)
            if help_args is not None:
                raw_args = help_args
        try:
            result = super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except click_exceptions.UsageError as exc:
            command_path = exc.ctx.command_path if exc.ctx is not None else (prog_name or "oaw")
            typer.echo(USAGE_BY_COMMAND[command_path], err=True, nl=False)
            typer.echo(f"{command_path}: error: {self._error_message(exc)}", err=True)
            if standalone_mode:
                raise SystemExit(2) from exc
            return 2
        if standalone_mode and isinstance(result, int):
            raise SystemExit(result)
        return result


def _app(help_text: str) -> typer.Typer:
    return typer.Typer(
        add_completion=True,
        no_args_is_help=False,
        help=help_text,
        cls=StableTyperGroup,
        context_settings={"help_option_names": ["-h", "--help"]},
        rich_markup_mode=None,
        suggest_commands=False,
    )


app = _app("OAW command-line interface.")
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
feedback_app = _app("Agent feedback note utilities")


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
app.add_typer(feedback_app, name="feedback")


def _run(action: Callable[[], None]) -> None:
    """Keep domain errors on the stable stderr and exit-code contract."""
    try:
        action()
    except OawError as exc:
        typer.echo(f"oaw: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _usage_error(message: str) -> None:
    """Raise a Click usage error attached to the active Typer command."""
    raise click_exceptions.UsageError(message, click_globals.get_current_context())


@app.callback()
def root() -> None:
    """OAW command-line interface."""


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
    status: Annotated[
        list[TaskStatus] | None, typer.Option("--status", help="backlog or todo")
    ] = None,
    priority: Annotated[list[int] | None, typer.Option("--priority", min=1, max=3)] = None,
    effort: Annotated[list[TaskEffort] | None, typer.Option("--effort", help="S, M, or L")] = None,
    note: Annotated[str | None, typer.Option("--note", help="initial problem statement")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="extra tag; repeatable")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    if start and status is not None:
        _usage_error("argument --status: not allowed with argument --start")
    selected_status = status[-1] if status else TaskStatus.BACKLOG
    selected_priority = priority[-1] if priority else None
    selected_effort = effort[-1] if effort else None
    _run(
        lambda: create_task(
            vault_root(),
            project,
            title,
            from_capture,
            start,
            requested_id,
            selected_status.value,
            selected_priority,
            selected_effort.value if selected_effort is not None else None,
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
        _usage_error("argument --write: not allowed with argument --dry-run")
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
    source: Annotated[str, typer.Argument()],
    target: Annotated[str, typer.Argument()],
    section: Annotated[str, typer.Option("--section")] = "Related",
    label: Annotated[str | None, typer.Option("--label")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="preview only")] = False,
    write: Annotated[bool, typer.Option("--write", help="write the edit")] = False,
) -> None:
    if dry_run and write:
        _usage_error("argument --write: not allowed with argument --dry-run")
    _run(lambda: link_ensure(vault_root(), source, target, section, label, write))


@link_app.command("ensure-bidirectional", help="ensure durable links in both directions")
def link_ensure_bidirectional_command(
    left: Annotated[str, typer.Argument()],
    right: Annotated[str, typer.Argument()],
    section: Annotated[str, typer.Option("--section")] = "Related",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="preview only")] = False,
    write: Annotated[bool, typer.Option("--write", help="write the edits")] = False,
) -> None:
    if dry_run and write:
        _usage_error("argument --write: not allowed with argument --dry-run")
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


@feedback_app.command("create", help="create a durable agent-feedback note")
def feedback_create(
    title: Annotated[str, typer.Option("--title", help="feedback title")],
    feedback_type: Annotated[
        FeedbackType, typer.Option("--type", help="pain, verified, idea, or bug")
    ],
    scope: Annotated[str, typer.Option("--scope", help="affected workflow or surface")],
    body: Annotated[str | None, typer.Option("--body", help="feedback Markdown body")] = None,
    body_file: Annotated[
        str | None, typer.Option("--body-file", help="UTF-8 body file; '-' reads stdin")
    ] = None,
    command: Annotated[str | None, typer.Option("--command", help="related command")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="extra tag; repeatable")] = None,
    requested_id: Annotated[str | None, typer.Option("--id", help="override feedback ID")] = None,
    date: Annotated[str | None, typer.Option("--date", help="creation date (YYYY-MM-DD)")] = None,
    allow_missing_session_id: Annotated[bool, typer.Option("--allow-missing-session-id")] = False,
) -> None:
    if body is not None and body_file is not None:
        _usage_error("argument --body-file: not allowed with argument --body")
    if body is None and body_file is None:
        _usage_error("the following arguments are required: one of --body, --body-file")
    _run(
        lambda: create_feedback(
            vault_root(),
            title,
            feedback_type.value,
            scope,
            read_feedback_body(body, body_file, sys.stdin),
            command,
            tag,
            requested_id,
            date,
            allow_missing_session_id,
        )
    )


def main(argv: list[str] | None = None) -> int:
    """Invoke the Typer app while preserving the stable integer-return contract."""
    try:
        get_command(app).main(args=argv, prog_name="oaw")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
