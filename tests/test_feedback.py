from pathlib import Path

import pytest
from typer.testing import CliRunner

from oaw import cli, feedback
from oaw.errors import OawError
from oaw.notes import write_new_note_atomic
from oaw.tags import creation_tag_block, creation_tags


def vault_state(vault: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(vault.rglob("*"))
    }


def feedback_args(*extra: str) -> list[str]:
    return [
        "feedback",
        "create",
        "--title",
        "Feedback title",
        "--type",
        "pain",
        "--scope",
        "CLI",
        "--body",
        "A durable observation.",
        *extra,
    ]


def test_creation_tags_are_safe_deduplicated_ordered_and_json_quoted() -> None:
    assert creation_tags(
        ("agent-feedback",), ["agent-feedback", "cli", "cli", "bug/fix", "foo_bar"]
    ) == [
        "agent-feedback",
        "cli",
        "bug/fix",
        "foo_bar",
    ]
    assert creation_tag_block(("agent-feedback",), ["cli"]) == [
        "tags:",
        '  - "agent-feedback"',
        '  - "cli"',
    ]


def test_creation_tags_reject_unsafe_values() -> None:
    for value in ("bad tag", "bad:tag", "../tag", "UPPER", "", " cli"):
        try:
            creation_tags(("agent-feedback",), [value])
        except Exception as exc:  # OawError is intentionally the stable public contract.
            assert "safe identifier" in str(exc)
        else:
            raise AssertionError(f"unsafe tag was accepted: {value!r}")


def test_feedback_body_round_trips_leading_indentation_and_trailing_newlines(
    tmp_path: Path,
) -> None:
    body = "    print('kept as an indented code block')\n\n"
    result = CliRunner().invoke(
        cli.app,
        feedback_args("--title", "Body round trip", "--body", body, "--date", "2026-07-14"),
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"},
    )

    assert result.exit_code == 0, result.stderr
    note = (tmp_path / "Agents/Feedback/2026-07-14 Body round trip.md").read_text(encoding="utf-8")
    assert f"## Feedback\n\n{body}## Agent sessions" in note


def test_feedback_rejects_unsafe_portable_filename_titles() -> None:
    for title in (
        "bad:name",
        "bad*name",
        "bad?name",
        "bad<name",
        "bad|name",
        "bad/child",
        "NUL",
        "COM1",
        "ends. ",
        "bad\x00name",
    ):
        with pytest.raises(OawError, match="safe filename title"):
            feedback.feedback_title(title)


def test_feedback_explicit_id_and_date_use_stable_contract(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"}
    for invalid_id in ("obs:AGT-FDBK-test", "AGT-FDBK-UPPER", "AGT-FDBK-a_b", " AGT-FDBK-test"):
        result = runner.invoke(cli.app, feedback_args("--id", invalid_id), env=env)
        assert result.exit_code == 1
        assert "AGT-FDBK-<safe-slug>" in result.stderr
    for invalid_date in ("2026-7-14", "2026-07-1", "2026-02-30"):
        result = runner.invoke(cli.app, feedback_args("--date", invalid_date), env=env)
        assert result.exit_code == 1
        assert "date must use YYYY-MM-DD" in result.stderr
    assert not (tmp_path / "Agents/Feedback").exists()


def test_atomic_new_note_failure_leaves_no_path_or_created_directory(tmp_path: Path) -> None:
    destination = tmp_path / "Agents/Feedback/failure.md"

    def fail_link(_source: str, _destination: str) -> None:
        raise OSError("injected link failure")

    with pytest.raises(OSError, match="injected link failure"):
        write_new_note_atomic(destination, "complete note", link=fail_link)
    assert not destination.exists()
    assert not (tmp_path / "Agents").exists()


def test_feedback_converts_atomic_publication_errors_to_domain_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_create(_path: Path, _text: str) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr(feedback, "write_new_note_atomic", fail_create)
    result = CliRunner().invoke(
        cli.app,
        feedback_args(),
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "could not create feedback note" in result.stderr
    assert "injected publication failure" in result.stderr


def test_feedback_create_writes_dated_note_with_safe_scalars_and_exact_output(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        cli.app,
        feedback_args(
            "--scope",
            "true",
            "--command",
            "date: 2026-07-14",
            "--tag",
            "cli",
            "--tag",
            "agent-feedback",
            "--tag",
            "cli",
            "--tag",
            "bug/fix",
            "--date",
            "2026-07-14",
        ),
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread-123"},
    )

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "Created: Agents/Feedback/2026-07-14 Feedback title.md\n"
        "ID: AGT-FDBK-feedback-title\n"
        "Status: backlog\n"
    )
    note = (tmp_path / "Agents/Feedback/2026-07-14 Feedback title.md").read_text(encoding="utf-8")
    assert "type: pain\nstatus: backlog" in note
    assert "\ndate:" not in note
    assert 'scope: "true"' in note
    assert 'command: "date: 2026-07-14"' in note
    assert 'id: "AGT-FDBK-feedback-title"' in note
    assert '  - "AGT-FDBK-feedback-title"' in note
    assert note.index('  - "agent-feedback"') < note.index('  - "cli"')
    assert note.count('  - "cli"') == 1
    assert '  - "bug/fix"' in note
    assert 'session-ids:\n  - "thread-123"' in note
    assert "## Feedback\n\nA durable observation." in note


def test_feedback_create_accepts_explicit_id_body_file_and_stdin(tmp_path: Path) -> None:
    body_file = tmp_path / "body.md"
    body_file.write_text("From a file.\n", encoding="utf-8")
    runner = CliRunner()
    file_arguments = [
        "feedback",
        "create",
        "--title",
        "File body",
        "--type",
        "verified",
        "--scope",
        "tests",
        "--body-file",
        str(body_file),
        "--id",
        "AGT-FDBK-explicit",
        "--date",
        "2026-07-12",
    ]
    result = runner.invoke(
        cli.app,
        file_arguments,
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"},
    )
    assert result.exit_code == 0, result.stderr
    note = (tmp_path / "Agents/Feedback/2026-07-12 File body.md").read_text(encoding="utf-8")
    assert "From a file." in note
    assert 'id: "AGT-FDBK-explicit"' in note

    stdin_arguments = [
        "feedback",
        "create",
        "--title",
        "Stdin body",
        "--type",
        "idea",
        "--scope",
        "tests",
        "--body-file",
        "-",
        "--date",
        "2026-07-13",
    ]
    result = runner.invoke(
        cli.app,
        stdin_arguments,
        input="From stdin.\n",
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"},
    )
    assert result.exit_code == 0, result.stderr
    assert "From stdin." in (tmp_path / "Agents/Feedback/2026-07-13 Stdin body.md").read_text(
        encoding="utf-8"
    )


def test_feedback_body_source_validation_empty_and_unreadable_do_not_write(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"}
    before = vault_state(tmp_path)
    missing = runner.invoke(
        cli.app,
        [
            "feedback",
            "create",
            "--title",
            "Missing body",
            "--type",
            "bug",
            "--scope",
            "tests",
            "--body-file",
            str(tmp_path / "missing.md"),
        ],
        env=env,
    )
    assert missing.exit_code == 1
    assert "could not read feedback body file" in missing.stderr
    assert vault_state(tmp_path) == before

    empty_file = tmp_path / "empty.md"
    empty_file.write_text("\n", encoding="utf-8")
    empty_file_result = runner.invoke(
        cli.app,
        [
            "feedback",
            "create",
            "--title",
            "Empty file",
            "--type",
            "bug",
            "--scope",
            "tests",
            "--body-file",
            str(empty_file),
        ],
        env=env,
    )
    assert empty_file_result.exit_code == 1
    assert "feedback body must not be empty" in empty_file_result.stderr
    after_empty_file = vault_state(tmp_path)
    assert after_empty_file == {**before, "empty.md": b"\n"}

    empty = runner.invoke(
        cli.app,
        feedback_args("--body", "   "),
        env=env,
    )
    assert empty.exit_code == 1
    assert "feedback body must not be empty" in empty.stderr
    assert vault_state(tmp_path) == after_empty_file

    neither = runner.invoke(
        cli.app,
        ["feedback", "create", "--title", "No body", "--type", "pain", "--scope", "tests"],
        env=env,
    )
    assert neither.exit_code == 2
    assert "one of --body, --body-file" in neither.stderr

    both = runner.invoke(
        cli.app,
        [
            "feedback",
            "create",
            "--title",
            "Two bodies",
            "--type",
            "pain",
            "--scope",
            "tests",
            "--body",
            "inline",
            "--body-file",
            str(empty_file),
        ],
        env=env,
    )
    assert both.exit_code == 2
    assert "not allowed with argument --body" in both.stderr


def test_feedback_rejects_duplicate_id_and_path_without_overwriting(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": "thread"}
    created = runner.invoke(
        cli.app,
        feedback_args("--date", "2026-07-14"),
        env=env,
    )
    assert created.exit_code == 0, created.stderr
    path = tmp_path / "Agents/Feedback/2026-07-14 Feedback title.md"
    original = path.read_bytes()
    duplicate_id = runner.invoke(
        cli.app,
        feedback_args("--date", "2026-07-15"),
        env=env,
    )
    assert duplicate_id.exit_code == 1
    assert "id 'AGT-FDBK-feedback-title' is already in use" in duplicate_id.stderr
    assert not (tmp_path / "Agents/Feedback/2026-07-15 Feedback title.md").exists()

    path_conflict = runner.invoke(
        cli.app,
        feedback_args("--date", "2026-07-14", "--id", "AGT-FDBK-another"),
        env=env,
    )
    assert path_conflict.exit_code == 1
    assert "feedback note already exists" in path_conflict.stderr
    assert path.read_bytes() == original


def test_feedback_requires_session_unless_the_explicit_missing_path_is_accepted(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    arguments = feedback_args("--date", "2026-07-14")
    result = runner.invoke(
        cli.app, arguments, env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": None}
    )
    assert result.exit_code == 1
    assert "no stable session ID found" in result.stderr
    assert not (tmp_path / "Agents/Feedback").exists()

    result = runner.invoke(
        cli.app,
        [*arguments, "--allow-missing-session-id"],
        env={"OAW_VAULT": str(tmp_path), "CODEX_THREAD_ID": None},
    )
    assert result.exit_code == 0, result.stderr
    note = (tmp_path / "Agents/Feedback/2026-07-14 Feedback title.md").read_text(encoding="utf-8")
    assert "session-ids:" not in note
    assert "`session_id=unavailable`" in note
