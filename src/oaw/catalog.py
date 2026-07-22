"""Deterministic feature-catalog rendering from the live Typer command tree."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from typer.core import TyperArgument, TyperOption
from typer.main import get_command


@dataclass(frozen=True)
class CommandSemantics:
    owner: str
    mutation_scope: str


SEMANTICS: dict[tuple[str, ...], CommandSemantics] = {
    ("resolve",): CommandSemantics("oaw.resolver", "Read-only vault resolution."),
    ("list",): CommandSemantics("oaw.resolver", "Read-only vault listing."),
    ("doctor",): CommandSemantics(
        "oaw.doctor", "Read-only vault, parser, and Obsidian-version compatibility diagnostics."
    ),
    ("project", "create"): CommandSemantics(
        "oaw.lifecycle", "Creates one project Index.md in the vault."
    ),
    ("research", "scaffold"): CommandSemantics(
        "oaw.lifecycle", "Creates or refreshes research packet files in the vault."
    ),
    ("research", "start"): CommandSemantics(
        "oaw.lifecycle",
        "Creates a provider result note, updates its prompt note, and conditionally "
        "creates Synthesis.md and Bases/Research packet.base.",
    ),
    ("task", "backlog"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note's status and session provenance; refuses while a run is running.",
    ),
    ("task", "promote"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note's status and session provenance; refuses while a run is running.",
    ),
    ("task", "start"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note and creates or resumes the caller's run record (state: running).",
    ),
    ("task", "pause"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note and transitions the caller's running run record to paused.",
    ),
    ("task", "review"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note and transitions the caller's run record to closed (review handoff).",
    ),
    ("task", "complete"): CommandSemantics(
        "oaw.lifecycle",
        "Updates a task note and creates or transitions the caller's run record to completed.",
    ),
    ("task", "note"): CommandSemantics(
        "oaw.lifecycle",
        "Appends session provenance to one project task note; also updates the caller's "
        "running run record when one exists.",
    ),
    ("task", "rename"): CommandSemantics(
        "oaw.task_rename",
        "Dry-run by default; --write renames one task note and migrates active Markdown "
        "wikilinks across the vault under a reviewed plan token.",
    ),
    ("task", "priority"): CommandSemantics(
        "oaw.lifecycle", "Updates task priority frontmatter and appends session provenance."
    ),
    ("task", "preparedness"): CommandSemantics(
        "oaw.lifecycle", "Updates task preparedness frontmatter and appends session provenance."
    ),
    ("task", "relation", "add"): CommandSemantics(
        "oaw.lifecycle", "Adds a canonical relationship frontmatter entry to one task note."
    ),
    ("task", "relation", "remove"): CommandSemantics(
        "oaw.lifecycle", "Removes a relationship frontmatter entry from one task note."
    ),
    ("task", "relation", "list"): CommandSemantics(
        "oaw.relations", "Read-only listing of one task's outgoing or derived incoming relations."
    ),
    ("task", "relation", "validate"): CommandSemantics(
        "oaw.relations", "Read-only validation of one reachable graph or the whole vault."
    ),
    ("task", "create"): CommandSemantics(
        "oaw.lifecycle",
        "Creates a task note; promoting a capture also updates the source capture's status "
        "and links, and --start also creates the caller's run record.",
    ),
    ("run", "list"): CommandSemantics("oaw.lifecycle", "Read-only listing of run records."),
    ("run", "close"): CommandSemantics(
        "oaw.lifecycle", "Administratively transitions one run record to closed."
    ),
    ("run", "audit"): CommandSemantics(
        "oaw.lifecycle", "Read-only consistency audit of the run registry."
    ),
    ("note", "session"): CommandSemantics(
        "oaw.lifecycle", "Updates session frontmatter and body on one resolved note."
    ),
    ("note", "observe"): CommandSemantics(
        "oaw.retro", "Appends an observation block to one resolved note."
    ),
    ("ingest", "safe-export"): CommandSemantics(
        "oaw.ingest", "Dry-run by default; --write moves approved and rejected handoff files."
    ),
    ("link", "check"): CommandSemantics("oaw.links", "Read-only link inspection."),
    ("link", "list"): CommandSemantics("oaw.links", "Read-only link listing."),
    ("link", "ensure"): CommandSemantics(
        "oaw.links", "Dry-run by default; --write updates one source note."
    ),
    ("link", "ensure-bidirectional"): CommandSemantics(
        "oaw.links", "Dry-run by default; --write updates both resolved notes."
    ),
    ("link", "lint"): CommandSemantics("oaw.links", "Read-only link diagnostics."),
    ("link", "materialize"): CommandSemantics(
        "oaw.links", "Dry-run by default; --write updates one source note."
    ),
    ("export", "note"): CommandSemantics(
        "oaw.exports", "Writes an approved export bundle outside the vault."
    ),
    ("export", "validate"): CommandSemantics(
        "oaw.exports", "Read-only validation of an existing export bundle."
    ),
    ("session", "lookup"): CommandSemantics(
        "oaw.sessions", "Read-only vault and harness-artifact lookup."
    ),
    ("session", "snapshot"): CommandSemantics(
        "oaw.snapshot", "Writes a session snapshot tree to the configured output root."
    ),
    ("retro", "create"): CommandSemantics(
        "oaw.retro", "Creates or explicitly replaces one retrospective note."
    ),
    ("feedback", "create"): CommandSemantics(
        "oaw.feedback", "Creates one durable feedback note in the vault."
    ),
    ("capture", "create"): CommandSemantics(
        "oaw.captures",
        "Creates one capture note under Captures/Entries/; with --project also sets project "
        "frontmatter and links the capture and project Index.",
    ),
    ("capture", "list"): CommandSemantics(
        "oaw.captures", "Read-only vault-wide capture listing across all statuses."
    ),
    ("capture", "show"): CommandSemantics(
        "oaw.captures", "Read-only display of one capture note from any location."
    ),
    ("capture", "triage"): CommandSemantics(
        "oaw.captures",
        "Updates a canonical capture's status, review-after, destinations, reciprocal links, "
        "session provenance, and triage audit in one transaction.",
    ),
}


def command_nodes(root: Any) -> list[tuple[tuple[str, ...], Any]]:
    nodes: list[tuple[tuple[str, ...], Any]] = []

    def visit(command: Any, parent: tuple[str, ...]) -> None:
        for name, child in getattr(command, "commands", {}).items():
            path = (*parent, name)
            nodes.append((path, child))
            visit(child, path)

    visit(root, ())
    return nodes


def leaf_commands(root: Any) -> list[tuple[tuple[str, ...], Any]]:
    return [
        (path, command)
        for path, command in command_nodes(root)
        if not getattr(command, "commands", {})
    ]


def stable_id(path: tuple[str, ...]) -> str:
    return "oaw-cli-" + "-".join(path)


def _escape_cell_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _code_span(value: str) -> str:
    value = (
        value.replace("|", "\\|").replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
    )
    backtick_runs = re.findall(r"`+", value)
    delimiter = "`" * (max((len(run) for run in backtick_runs), default=0) + 1)
    if value.startswith("`") or value.endswith("`"):
        value = f" {value} "
    return f"{delimiter}{value}{delimiter}"


def _default_label(value: Any) -> str | None:
    if value is None or value is False or value == "" or value == () or value == []:
        return None
    if hasattr(value, "value"):
        value = value.value
    return str(value)


def _value_label(parameter: TyperOption) -> str:
    choices = getattr(parameter.type, "choices", None)
    if choices:
        return "{" + "|".join(str(choice) for choice in choices) + "}"
    minimum = getattr(parameter.type, "min", None)
    maximum = getattr(parameter.type, "max", None)
    if (
        isinstance(minimum, int)
        and isinstance(maximum, int)
        and not getattr(parameter.type, "min_open", False)
        and not getattr(parameter.type, "max_open", False)
        and maximum - minimum <= 20
    ):
        return "{" + "|".join(str(value) for value in range(minimum, maximum + 1)) + "}"
    return (parameter.name or "").upper()


def parameter_label(parameter: TyperArgument | TyperOption) -> str:
    annotations: list[str] = []
    if isinstance(parameter, TyperArgument):
        label = f"<{(parameter.name or '').replace('_', '-')}>"
    else:
        label = ", ".join(parameter.opts)
        if not parameter.is_flag:
            label = f"{label} {_value_label(parameter)}"
    if parameter.required:
        annotations.append("required")
    if getattr(parameter, "multiple", False):
        annotations.append("repeatable")
    default = _default_label(parameter.default)
    if default is not None and not parameter.required:
        annotations.append(f"default: {_escape_cell_text(default)}")
    suffix = f" ({', '.join(annotations)})" if annotations else ""
    return f"{_code_span(label)}{suffix}"


def deprecation_state(command: Any) -> str:
    deprecated = command.deprecated
    if not deprecated:
        return "Active (not deprecated)"
    if isinstance(deprecated, str):
        return f"Deprecated: {deprecated}"
    return "Deprecated"


def render_cli_catalog() -> str:
    from .cli import app

    root = get_command(app)
    nodes = command_nodes(root)
    leaves = [(path, command) for path, command in nodes if not getattr(command, "commands", {})]
    leaf_paths = {path for path, _ in leaves}
    if leaf_paths != set(SEMANTICS):
        missing = sorted(leaf_paths - set(SEMANTICS))
        stale = sorted(set(SEMANTICS) - leaf_paths)
        raise ValueError(f"catalog semantics mismatch; missing={missing}; stale={stale}")

    groups = [
        (path, command)
        for path, command in nodes
        if len(path) == 1 and getattr(command, "commands", {})
    ]
    top_level: list[tuple[tuple[str, ...], Any]] = [
        (path, command) for path, command in leaves if len(path) == 1
    ]
    lines = [
        "---",
        "type: reference",
        "id: OAW-REF-cli-feature-catalog",
        "aliases:",
        "  - OAW-REF-cli-feature-catalog",
        "generated: true",
        "---",
        "",
        "# OAW CLI feature catalog",
        "",
        "> [!warning] Generated file",
        "> Do not edit this matrix by hand. Run `uv run python scripts/generate_cli_catalog.py`.",
        "",
        "The command hierarchy, purposes, parameters, accepted choices, and deprecation state",
        "come from the live Typer/Click tree. Implementation ownership and mutation scope are",
        "semantic annotations checked for complete coverage of every leaf command.",
        "",
        "## Command groups",
        "",
        "| Group | Purpose |",
        "| --- | --- |",
    ]
    for path, command in groups:
        lines.append(
            f"| `oaw {_escape_cell_text(path[0])}` | {_escape_cell_text(command.help or '')} |"
        )

    sections: list[tuple[str, str, list[tuple[tuple[str, ...], Any]]]] = [
        ("top-level", "Top-level commands", top_level)
    ]
    for path, _command in groups:
        members = [(leaf_path, leaf) for leaf_path, leaf in leaves if leaf_path[0] == path[0]]
        sections.append((path[0], f"`oaw {path[0]}`", members))

    for section_id, title, members in sections:
        lines += [
            "",
            f'<a id="oaw-cli-group-{section_id}"></a>',
            f"## {title}",
            "",
            "| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for path, command in members:
            semantics = SEMANTICS[path]
            parameters = "<br>".join(parameter_label(param) for param in command.params) or "—"
            lines.append(
                f'| <a id="{stable_id(path)}"></a>`{stable_id(path)}` '
                f"| `oaw {_escape_cell_text(' '.join(path))}` "
                f"| {_escape_cell_text(command.help or '')} "
                f"| {parameters} "
                f"| `{_escape_cell_text(semantics.owner)}` "
                f"| {_escape_cell_text(semantics.mutation_scope)} "
                f"| {_escape_cell_text(deprecation_state(command))} |"
            )
    return "\n".join(lines) + "\n"
