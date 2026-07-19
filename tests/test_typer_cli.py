import ast
import inspect
from pathlib import Path

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from oaw import cli

EXPECTED_COMMAND_PATHS = {
    (),
    ("resolve",),
    ("list",),
    ("project",),
    ("project", "create"),
    ("research",),
    ("research", "scaffold"),
    ("research", "start"),
    ("task",),
    ("task", "backlog"),
    ("task", "promote"),
    ("task", "start"),
    ("task", "pause"),
    ("task", "review"),
    ("task", "complete"),
    ("task", "note"),
    ("task", "priority"),
    ("task", "preparedness"),
    ("task", "relation"),
    ("task", "relation", "add"),
    ("task", "relation", "remove"),
    ("task", "relation", "list"),
    ("task", "relation", "validate"),
    ("task", "create"),
    ("run",),
    ("run", "list"),
    ("run", "close"),
    ("run", "audit"),
    ("note",),
    ("note", "session"),
    ("note", "observe"),
    ("ingest",),
    ("ingest", "safe-export"),
    ("link",),
    ("link", "check"),
    ("link", "list"),
    ("link", "ensure"),
    ("link", "ensure-bidirectional"),
    ("link", "lint"),
    ("link", "materialize"),
    ("export",),
    ("export", "note"),
    ("export", "validate"),
    ("session",),
    ("session", "lookup"),
    ("session", "snapshot"),
    ("retro",),
    ("retro", "create"),
    ("feedback",),
    ("feedback", "create"),
    ("capture",),
    ("capture", "create"),
    ("capture", "list"),
    ("capture", "show"),
    ("capture", "triage"),
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def imported_targets(source: str) -> set[str]:
    tree = ast.parse(source)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            relative_prefix = "." * node.level
            module_prefix = f"{relative_prefix}{node.module or ''}"
            for alias in node.names:
                separator = "." if node.module else ""
                targets.add(f"{module_prefix}{separator}{alias.name}")
    return targets


def is_forbidden_cli_dependency(target: str) -> bool:
    normalized = target.lstrip(".")
    return normalized == "argparse" or normalized.startswith("argparse.")


def command_paths() -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = {()}

    def visit(command: object, parent: tuple[str, ...]) -> None:
        for name, child in getattr(command, "commands", {}).items():
            path = (*parent, name)
            paths.add(path)
            visit(child, path)

    visit(get_command(cli.app), ())
    return paths


def write_project_index(vault: Path) -> None:
    write(
        vault / "Projects/Parity/Index.md",
        """---
type: project
id: PRT-index
aliases:
  - PRT-index
---

# Parity
""",
    )


def vault_state(vault: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(vault.rglob("*"))
    }


def test_typer_command_tree_matches_declared_contract() -> None:
    assert command_paths() == EXPECTED_COMMAND_PATHS


@pytest.mark.parametrize(
    "command",
    [
        ["task", "start"],
        ["task", "pause"],
        ["task", "review"],
        ["task", "complete"],
        ["run", "close"],
    ],
)
def test_run_changing_commands_do_not_offer_missing_session_escape_hatch(command):
    result = CliRunner().invoke(cli.app, [*command, "--help"])

    assert result.exit_code == 0, result.stderr
    assert "--allow-missing-session-id" not in result.stdout


def test_typer_frontend_has_no_argparse_or_cli_dependency() -> None:
    targets = imported_targets(inspect.getsource(cli))

    assert not {target for target in targets if is_forbidden_cli_dependency(target)}


@pytest.mark.parametrize(
    "statement",
    [
        "import argparse",
        "from argparse import ArgumentParser",
    ],
)
def test_typer_dependency_guard_recognizes_forbidden_import_forms(statement: str) -> None:
    assert any(is_forbidden_cli_dependency(target) for target in imported_targets(statement))


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_typer_help_wins_over_an_unknown_option(help_flag: str) -> None:
    result = CliRunner().invoke(cli.app, ["resolve", "--bogus", help_flag])

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("Usage: oaw resolve ")


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_typer_help_in_a_known_option_value_slot_is_a_usage_error(help_flag: str) -> None:
    result = CliRunner().invoke(cli.app, ["list", "--project", help_flag])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "requires an argument" in result.stderr


def test_typer_option_token_does_not_fill_a_pending_option_value() -> None:
    result = CliRunner().invoke(cli.app, ["list", "--project", "--status", "--help"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "requires an argument" in result.stderr


def test_typer_separator_does_not_fill_a_pending_option_value() -> None:
    result = CliRunner().invoke(cli.app, ["list", "--project", "--", "--help"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "requires an argument" in result.stderr


def test_typer_help_after_separator_is_a_positional_token() -> None:
    result = CliRunner().invoke(cli.app, ["resolve", "note-id", "--", "--help"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--help" in result.stderr


@pytest.mark.parametrize(
    ("arguments", "exit_code", "output_prefix"),
    [
        (["task", "bogus", "--help"], 2, "usage: oaw task "),
        (["unknown-command", "--help"], 2, "usage: oaw "),
        (["--help", "task"], 0, "Usage: oaw "),
        (["task", "--help", "create"], 0, "Usage: oaw task "),
    ],
)
def test_typer_help_preserves_command_path_parsing_order(
    arguments: list[str], exit_code: int, output_prefix: str
) -> None:
    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == exit_code
    assert result.output.startswith(output_prefix)


def test_typer_feedback_help_and_type_validation_use_native_contract() -> None:
    help_result = CliRunner().invoke(cli.app, ["feedback", "create", "--help"])
    assert help_result.exit_code == 0, help_result.stderr
    assert "Usage: oaw feedback create" in help_result.stdout
    assert "--body-file" in help_result.stdout

    invalid_type = CliRunner().invoke(
        cli.app,
        [
            "feedback",
            "create",
            "--title",
            "Invalid type",
            "--type",
            "unknown",
            "--scope",
            "tests",
            "--body",
            "body",
        ],
    )
    assert invalid_type.exit_code == 2
    assert invalid_type.stdout == ""
    assert "argument --type: invalid choice: 'unknown'" in invalid_type.stderr


NOTE_SOURCE_COMMAND_PATHS = [
    ["task", "backlog"],
    ["task", "promote"],
    ["task", "start"],
    ["task", "pause"],
    ["task", "review"],
    ["task", "complete"],
    ["task", "note"],
    ["note", "session"],
]

NOTE_SOURCE_INVOCATIONS = [
    ["task", "backlog", "TSK-EXAMPLE"],
    ["task", "promote", "TSK-EXAMPLE"],
    ["task", "start", "TSK-EXAMPLE"],
    ["task", "pause", "TSK-EXAMPLE"],
    ["task", "review", "TSK-EXAMPLE", "--checks", "pytest"],
    ["task", "complete", "TSK-EXAMPLE", "--checks", "pytest"],
    ["task", "note", "TSK-EXAMPLE"],
    ["note", "session", "TSK-EXAMPLE"],
]


@pytest.mark.parametrize("command", NOTE_SOURCE_COMMAND_PATHS)
def test_typer_note_commands_declare_note_file_help(command: list[str]) -> None:
    result = CliRunner().invoke(cli.app, [*command, "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.stderr
    normalized_help = " ".join(result.stdout.split())
    assert "--note-file" in normalized_help
    assert "inline Markdown; exactly one" in normalized_help
    assert "UTF-8 Markdown file; '-' reads stdin; exactly one" in normalized_help
    assert "exactly one of --note or --note-file is required" in normalized_help


def test_typer_task_create_declares_optional_note_file_help() -> None:
    result = CliRunner().invoke(cli.app, ["task", "create", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.stderr
    normalized_help = " ".join(result.stdout.split())
    assert "--note-file" in normalized_help
    assert "optional inline initial problem; when supplied, use exactly one" in normalized_help
    assert (
        "optional UTF-8 initial-problem file; '-' reads stdin; when supplied, use exactly one"
        in normalized_help
    )
    assert "when supplied, use exactly one of --note or --note-file" in normalized_help


def test_typer_note_observe_declares_body_file_help() -> None:
    result = CliRunner().invoke(cli.app, ["note", "observe", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.stderr
    normalized_help = " ".join(result.stdout.split())
    assert "--body-file" in normalized_help
    assert "inline Markdown; exactly one" in normalized_help
    assert "UTF-8 Markdown file; '-' reads stdin; exactly one" in normalized_help
    assert "exactly one of --body or --body-file is required" in normalized_help


@pytest.mark.parametrize("base_arguments", NOTE_SOURCE_INVOCATIONS)
def test_typer_note_source_conflict_is_a_usage_error(base_arguments: list[str]) -> None:
    result = CliRunner().invoke(
        cli.app,
        [*base_arguments, "--note", "inline", "--note-file", "-"],
        input="stdin must not be read",
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "argument --note-file: not allowed with argument --note" in result.stderr


@pytest.mark.parametrize("base_arguments", NOTE_SOURCE_INVOCATIONS)
def test_typer_note_source_missing_is_a_usage_error(base_arguments: list[str]) -> None:
    result = CliRunner().invoke(cli.app, base_arguments)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "the following arguments are required: one of --note, --note-file" in result.stderr


def test_typer_task_create_note_source_conflict_is_a_usage_error() -> None:
    result = CliRunner().invoke(
        cli.app,
        ["task", "create", "--title", "Source check", "--note", "inline", "--note-file", "-"],
        input="stdin must not be read",
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "argument --note-file: not allowed with argument --note" in result.stderr


def test_typer_note_observe_body_source_conflict_is_a_usage_error() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "note",
            "observe",
            "TSK-EXAMPLE",
            "--title",
            "Source check",
            "--body",
            "inline",
            "--body-file",
            "-",
        ],
        input="stdin must not be read",
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "argument --body-file: not allowed with argument --body" in result.stderr


def test_typer_note_observe_body_source_missing_is_a_usage_error() -> None:
    result = CliRunner().invoke(
        cli.app, ["note", "observe", "TSK-EXAMPLE", "--title", "Source check"]
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "the following arguments are required: one of --body, --body-file" in result.stderr


def test_typer_note_source_usage_errors_do_not_access_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accessed = False

    def unexpected_vault_root() -> Path:
        nonlocal accessed
        accessed = True
        raise AssertionError("invalid source selection must not access the vault")

    monkeypatch.setattr(cli, "vault_root", unexpected_vault_root)

    missing = CliRunner().invoke(
        cli.app, ["task", "start", "TSK-EXAMPLE"], input="stdin must stay unread"
    )
    assert missing.exit_code == 2
    assert not accessed

    conflict = CliRunner().invoke(
        cli.app,
        [
            "note",
            "observe",
            "TSK-EXAMPLE",
            "--title",
            "Source check",
            "--body",
            "inline",
            "--body-file",
            "-",
        ],
        input="stdin must stay unread",
    )
    assert conflict.exit_code == 2
    assert not accessed


def test_temporary_typer_frontend_resolves_with_shared_service(tmp_path: Path) -> None:
    write(
        tmp_path / "Projects/Example/Tasks/Resolver CLI.md",
        """---
type: task
id: EXM-TSK-resolver
aliases:
  - EXM-TSK-resolver
---

# Resolver CLI
""",
    )
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        ["resolve", "--path", "EXM-TSK-resolver"],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert isinstance(cli.app, typer.Typer)
    assert result.exit_code == 0, result.output
    assert result.output == f"{tmp_path / 'Projects/Example/Tasks/Resolver CLI.md'}\n"


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_typer_frontend_requires_configured_vault(configured: str | None) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["resolve", "EXM-TSK-resolver"],
        env={"OAW_VAULT": configured},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ("oaw: OAW_VAULT is required; set it to the Obsidian vault path\n")


@pytest.mark.parametrize(
    ("arguments", "error_line"),
    [
        (
            ["task", "create", "--start", "--status", "todo"],
            "oaw task create: error: argument --status: not allowed with argument --start\n",
        ),
        (
            ["task", "create", "--start", "--allow-missing-session-id"],
            "oaw task create: error: argument --allow-missing-session-id: "
            "not allowed with argument --start\n",
        ),
        (
            ["ingest", "safe-export", "--dry-run", "--write"],
            "oaw ingest safe-export: error: argument --write: "
            "not allowed with argument --dry-run\n",
        ),
        (
            ["link", "ensure", "left", "right", "--dry-run", "--write"],
            "oaw link ensure: error: argument --write: not allowed with argument --dry-run\n",
        ),
        (
            ["run", "list", "--session", "example-session", "--current-session"],
            "oaw run list: error: argument --current-session: "
            "not allowed with argument --session\n",
        ),
        (
            [
                "link",
                "ensure-bidirectional",
                "left",
                "right",
                "--dry-run",
                "--write",
            ],
            "oaw link ensure-bidirectional: error: argument --write: "
            "not allowed with argument --dry-run\n",
        ),
    ],
)
def test_typer_conflicts_preserve_usage_diagnostics(
    arguments: list[str],
    error_line: str,
) -> None:
    result = CliRunner().invoke(cli.app, arguments, env={"COLUMNS": "80"})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.endswith(error_line)


@pytest.mark.parametrize(
    ("arguments", "invalid_value"),
    [
        (["--status", "invalid", "--status", "backlog"], "invalid"),
        (["--effort", "X", "--effort", "S"], "X"),
        (["--priority", "9", "--priority", "1"], "9"),
    ],
)
def test_typer_task_create_validates_every_choice_occurrence(
    arguments: list[str],
    invalid_value: str,
) -> None:
    full_arguments = ["task", "create", *arguments]
    result = CliRunner().invoke(cli.app, full_arguments, env={"COLUMNS": "80"})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert invalid_value in result.stderr


@pytest.mark.parametrize("priority", ["0", "4", "invalid"])
def test_typer_task_priority_rejects_values_outside_declared_choices(priority: str) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "priority",
            "PRT-TSK-example",
            "--priority",
            priority,
            "--note",
            "Re-rank.",
        ],
        env={"COLUMNS": "80"},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert priority in result.stderr


@pytest.mark.parametrize("arguments", [[], ["resolve"], ["unknown-command"]])
def test_typer_ordinary_usage_errors_preserve_usage_diagnostics(
    arguments: list[str],
) -> None:
    result = CliRunner().invoke(cli.app, arguments, env={"COLUMNS": "80"})

    assert result.exit_code == 2
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--status", "backlog"),
        ("--status", "todo"),
        ("--priority", "1"),
        ("--priority", "2"),
        ("--priority", "3"),
        ("--effort", "S"),
        ("--effort", "M"),
        ("--effort", "L"),
        ("--preparedness", "needs-triage"),
        ("--preparedness", "needs-design"),
        ("--preparedness", "prepared"),
    ],
)
def test_typer_task_create_accepts_each_declared_value(
    option: str,
    value: str,
    tmp_path: Path,
) -> None:
    write_project_index(tmp_path)
    title = f"Accepted {option.removeprefix('--')} {value}"
    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "create",
            "--project",
            "Parity",
            "--title",
            title,
            option,
            value,
            "--allow-missing-session-id",
        ],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert f"Status: {'backlog' if option != '--status' else value}" in result.stdout


@pytest.mark.parametrize("state", ["needs-triage", "needs-design", "prepared"])
def test_typer_task_preparedness_accepts_each_declared_value(state: str, tmp_path: Path) -> None:
    write_project_index(tmp_path)
    task = tmp_path / "Projects/Parity/Tasks/Example.md"
    write(
        task,
        """---
type: task
status: todo
id: PRT-TSK-example
aliases:
  - PRT-TSK-example
---

# Example
""",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "preparedness",
            "PRT-TSK-example",
            "--state",
            state,
            "--note",
            "Assessed.",
            "--allow-missing-session-id",
        ],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    assert f"Preparedness: {state}" in result.stdout


def test_typer_task_preparedness_rejects_invalid_state() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "preparedness",
            "PRT-TSK-example",
            "--state",
            "unknown",
            "--note",
            "Assess.",
        ],
        env={"COLUMNS": "80"},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "argument --state: invalid choice: 'unknown'" in result.stderr


def test_typer_task_relation_rejects_invalid_type() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "relation",
            "add",
            "PRT-TSK-source",
            "unknown",
            "PRT-TSK-target",
            "--note",
            "Invalid.",
        ],
        env={"COLUMNS": "80"},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "argument relation_type: invalid choice: 'unknown'" in result.stderr


def test_typer_domain_error_uses_stderr_and_exit_class_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["resolve", "--path", "missing"],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith("oaw: no note with frontmatter id or alias")


def test_typer_domain_error_does_not_write_the_vault(tmp_path: Path) -> None:
    write_project_index(tmp_path)
    before = vault_state(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "start",
            "PRT-TSK-missing",
            "--note",
            "Must not write",
        ],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no note with frontmatter id or alias" in result.stderr
    assert vault_state(tmp_path) == before
