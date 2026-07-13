import ast
import inspect
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from oaw import cli, typer_cli


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_typer_frontend_has_no_argparse_or_cli_dependency() -> None:
    tree = ast.parse(inspect.getsource(typer_cli))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert "argparse" not in imported_modules
    assert "oaw.cli" not in imported_modules
    assert "cli" not in imported_modules
    assert "oaw.cli" not in imported_from
    assert "cli" not in imported_from


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_typer_help_wins_over_an_unknown_option(help_flag: str) -> None:
    result = CliRunner().invoke(typer_cli.app, ["resolve", "--bogus", help_flag])

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("Usage: oaw resolve ")


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
    result = CliRunner().invoke(typer_cli.app, arguments)

    assert result.exit_code == exit_code
    assert result.output.startswith(output_prefix)


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
        typer_cli.app,
        ["resolve", "--path", "EXM-TSK-resolver"],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert isinstance(typer_cli.app, typer.Typer)
    assert result.exit_code == 0, result.output
    assert result.output == f"{tmp_path / 'Projects/Example/Tasks/Resolver CLI.md'}\n"


@pytest.mark.parametrize(
    ("arguments", "error_line"),
    [
        (
            ["task", "create", "--start", "--status", "todo"],
            "oaw task create: error: argument --status: not allowed with argument --start\n",
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
def test_typer_conflicts_preserve_argparse_diagnostics(
    arguments: list[str],
    error_line: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    with pytest.raises(SystemExit) as expected_exit:
        cli.build_parser().parse_args(arguments)
    expected = capsys.readouterr()
    result = CliRunner().invoke(typer_cli.app, arguments, env={"COLUMNS": "80"})

    assert expected_exit.value.code == result.exit_code == 2
    assert expected.out == result.stdout == ""
    assert result.stderr == expected.err
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
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_arguments = ["task", "create", *arguments]
    monkeypatch.setenv("COLUMNS", "80")
    with pytest.raises(SystemExit) as expected_exit:
        cli.build_parser().parse_args(full_arguments)
    expected = capsys.readouterr()
    result = CliRunner().invoke(typer_cli.app, full_arguments, env={"COLUMNS": "80"})

    assert expected_exit.value.code == result.exit_code == 2
    assert expected.out == result.stdout == ""
    assert result.stderr == expected.err
    assert invalid_value in result.stderr


@pytest.mark.parametrize("arguments", [[], ["resolve"], ["unknown-command"]])
def test_typer_ordinary_usage_errors_preserve_argparse_diagnostics(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    with pytest.raises(SystemExit) as expected_exit:
        cli.build_parser().parse_args(arguments)
    expected = capsys.readouterr()
    result = CliRunner().invoke(typer_cli.app, arguments, env={"COLUMNS": "80"})

    assert expected_exit.value.code == result.exit_code == 2
    assert expected.out == result.stdout == ""
    assert result.stderr == expected.err
