import datetime as dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oaw import cli
from tests.support import (
    NO_SESSION_ENV,
    add_project_index,
    snapshot_tree_without_following_symlinks,
    write,
)


def write_project_index(vault: Path, name: str = "Example", alias: str = "EXP") -> None:
    add_project_index(vault, name, f"{alias}-index")


def write_task(vault: Path, note_id: str = "EXP-TSK-example", status: str = "todo") -> Path:
    path = vault / "Projects/Example/Tasks/Example.md"
    write(
        path,
        f"""---
type: task
status: {status}
id: {note_id}
aliases:
  - {note_id}
---

# Example
""",
    )
    return path


def write_note(vault: Path) -> Path:
    path = vault / "Notes/Shell-safe input.md"
    write(
        path,
        """---
type: note
id: SAFE-NOTE
aliases:
  - SAFE-NOTE
---

# Shell-safe input

## Observations
""",
    )
    return path


def test_task_note_accepts_note_file(tmp_path: Path) -> None:
    task_path = write_task(tmp_path)
    body = "Reviewed with `backticks` and $HOME text."
    body_file = tmp_path / "note.md"
    body_file.write_text(body, encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "note",
            "EXP-TSK-example",
            "--note-file",
            str(body_file),
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note = task_path.read_text(encoding="utf-8")
    assert body in note
    assert "## Agent sessions" in note


def test_task_promote_accepts_note_file(tmp_path: Path) -> None:
    task_path = write_task(tmp_path, status="backlog")
    body = "Selected `via file` with $VAR present.\n"
    body_file = tmp_path / "note.md"
    body_file.write_text(body, encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "promote",
            "EXP-TSK-example",
            "--note-file",
            str(body_file),
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note = task_path.read_text(encoding="utf-8")
    assert "status: todo" in note
    assert body.strip() in note


def test_task_create_accepts_note_file(tmp_path: Path) -> None:
    write_project_index(tmp_path)
    body = "Initial problem via file with `code` and $VALUE.\n"
    body_file = tmp_path / "problem.md"
    body_file.write_text(body, encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "create",
            "--project",
            "Example",
            "--title",
            "File-backed task",
            "--note-file",
            str(body_file),
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note = (tmp_path / "Projects/Example/Tasks/File-backed task.md").read_text(encoding="utf-8")
    assert body.strip() in note


def test_note_observe_accepts_body_file(tmp_path: Path) -> None:
    note_path = write_note(tmp_path)
    body = "Body via file with `code` and $HOME.\n"
    body_file = tmp_path / "observation.md"
    body_file.write_text(body, encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "note",
            "observe",
            "SAFE-NOTE",
            "--title",
            "File observation",
            "--body-file",
            str(body_file),
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note = note_path.read_text(encoding="utf-8")
    assert body.strip() in note
    assert f"### {dt.date.today().isoformat()} - File observation" in note


def test_note_file_dash_reads_stdin(tmp_path: Path) -> None:
    task_path = write_task(tmp_path)
    body = "From stdin with `code` and $HOME text."

    result = CliRunner().invoke(
        cli.app,
        ["task", "note", "EXP-TSK-example", "--note-file", "-", "--allow-missing-session-id"],
        input=body,
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note = task_path.read_text(encoding="utf-8")
    assert body in note


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_multiline_task_note_headings_stay_with_entry_after_second_append(
    tmp_path: Path, source: str
) -> None:
    task_path = write_task(tmp_path)
    body = "Summary.\n\n## Evidence\n\nDetails."
    first_arguments = ["task", "note", "EXP-TSK-example"]
    input_text = None
    if source == "file":
        body_file = tmp_path / "session-note.md"
        body_file.write_text(body, encoding="utf-8")
        first_arguments.extend(["--note-file", str(body_file)])
    else:
        first_arguments.extend(["--note-file", "-"])
        input_text = body
    first_arguments.append("--allow-missing-session-id")

    first = CliRunner().invoke(
        cli.app,
        first_arguments,
        input=input_text,
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )
    second = CliRunner().invoke(
        cli.app,
        [
            "task",
            "note",
            "EXP-TSK-example",
            "--note",
            "Second entry.",
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    note = task_path.read_text(encoding="utf-8")
    first_entry = note.index(" - Summary.")
    nested_heading = note.index("  ## Evidence")
    details = note.index("  Details.")
    second_entry = note.index(" - Second entry.")
    assert first_entry < nested_heading < details < second_entry
    assert "\n## Evidence\n" not in note


def test_note_file_preserves_newlines_exactly(tmp_path: Path) -> None:
    write_project_index(tmp_path)
    body = "First line.\r\n\r\nSecond line after a blank line.\r\n\r\n"
    body_file = tmp_path / "problem.md"
    body_file.write_bytes(body.encode("utf-8"))

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "create",
            "--project",
            "Example",
            "--title",
            "Newline-preserving task",
            "--note-file",
            str(body_file),
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 0, result.stderr
    note_bytes = (tmp_path / "Projects/Example/Tasks/Newline-preserving task.md").read_bytes()
    assert f"## Problem\n\n{body}".encode() in note_bytes


@pytest.mark.parametrize(
    "arguments",
    [
        ["task", "note", "EXP-TSK-example", "--note", "inline", "--note-file", "-"],
        [
            "note",
            "observe",
            "SAFE-NOTE",
            "--title",
            "Conflict",
            "--body",
            "inline",
            "--body-file",
            "-",
        ],
    ],
)
def test_note_and_note_file_conflict_errors(tmp_path: Path, arguments: list[str]) -> None:
    write_task(tmp_path)
    write_note(tmp_path)
    before = snapshot_tree_without_following_symlinks(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        arguments,
        input="stdin must not be read",
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "not allowed with argument" in result.stderr
    assert snapshot_tree_without_following_symlinks(tmp_path) == before


def test_missing_note_file_errors_clearly(tmp_path: Path) -> None:
    task_path = write_task(tmp_path)
    before = task_path.read_bytes()
    missing = tmp_path / "missing.md"

    result = CliRunner().invoke(
        cli.app,
        [
            "task",
            "note",
            "EXP-TSK-example",
            "--note-file",
            str(missing),
            "--allow-missing-session-id",
        ],
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert "could not read note file" in result.stderr
    assert str(missing) in result.stderr
    assert task_path.read_bytes() == before
