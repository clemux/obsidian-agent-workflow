import datetime as dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oaw import cli
from oaw.sessions import SESSION_ENV

# Unset every supported harness session variable so test outcomes do not depend
# on which agent harness (if any) happens to be running the suite.
NO_SESSION_ENV: dict[str, str | None] = {env_name: None for _, env_name in SESSION_ENV}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_project_index(vault: Path, name: str = "Example", alias: str = "EXP") -> None:
    write(
        vault / f"Projects/{name}/Index.md",
        f"""---
type: project
id: {alias}-index
aliases:
  - {alias}-index
---

# {name}
""",
    )


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


def vault_state(vault: Path) -> dict[str, bytes]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }


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
    before = vault_state(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        arguments,
        input="stdin must not be read",
        env={**NO_SESSION_ENV, "OAW_VAULT": str(tmp_path)},
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "not allowed with argument" in result.stderr
    assert vault_state(tmp_path) == before


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
