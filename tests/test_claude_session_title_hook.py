"""Behavior contract for the Claude Code session-title hook."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "claude-session-title-hook.sh"
SETUP = Path(__file__).resolve().parents[1] / "scripts" / "claude-setup.sh"
SESSION = "935fa13a-e447-4717-9a16-73bacaa6ebdc"
TASK = "FAB-TSK-workflow-approval"


def run_hook(mode: str, payload: dict, state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOK), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "OAW_SESSION_TITLE_STATE_DIR": str(state_dir)},
    )


def bash_event(command: str, stdout: str = "", stderr: str = "") -> dict:
    return {
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": stderr},
    }


def recorded_title(state_dir: Path) -> str | None:
    state = state_dir / SESSION
    return state.read_text().strip() if state.exists() else None


def test_successful_start_records_the_implementation_marker(tmp_path: Path) -> None:
    run_hook("record", bash_event(f"oaw task start {TASK} --note x", "Status: active"), tmp_path)
    assert recorded_title(tmp_path) == f"[I] {TASK}"


def test_review_and_completion_record_their_own_markers(tmp_path: Path) -> None:
    run_hook(
        "record", bash_event(f"oaw task review {TASK} --checks pytest", "Status: review"), tmp_path
    )
    assert recorded_title(tmp_path) == f"[R] {TASK}"

    # A lifecycle command is often chained behind an administrative one.
    run_hook(
        "record",
        bash_event(
            f"oaw run close AGT-RUN-X --reason y && oaw task complete {TASK} --checks pytest",
            "Run state: closed\nUpdated: note.md\nStatus: done\nBoard: updated",
        ),
        tmp_path,
    )
    assert recorded_title(tmp_path) == f"[DONE] {TASK}"


def test_refused_lifecycle_command_leaves_the_title_alone(tmp_path: Path) -> None:
    """The status is read from stdout, so a command that failed cannot retitle the session."""
    run_hook("record", bash_event(f"oaw task start {TASK}", "Status: active"), tmp_path)
    run_hook(
        "record",
        bash_event(
            f"oaw task complete {TASK} --checks pytest",
            stdout="",
            stderr="oaw: transition refused while another session remains running",
        ),
        tmp_path,
    )
    assert recorded_title(tmp_path) == f"[I] {TASK}"


def test_unrelated_commands_record_nothing(tmp_path: Path) -> None:
    run_hook("record", bash_event("git status", "clean"), tmp_path)
    run_hook("record", bash_event(f"oaw resolve {TASK}", f"ID: {TASK}"), tmp_path)
    assert recorded_title(tmp_path) is None


def test_emit_sets_a_changed_title(tmp_path: Path) -> None:
    run_hook("record", bash_event(f"oaw task start {TASK}", "Status: active"), tmp_path)
    result = run_hook(
        "emit",
        {"session_id": SESSION, "session_title": "Start obs FAB-TSK workflow approval"},
        tmp_path,
    )
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "sessionTitle": f"[I] {TASK}",
        }
    }


def test_emit_stays_silent_when_the_title_already_matches(tmp_path: Path) -> None:
    run_hook("record", bash_event(f"oaw task start {TASK}", "Status: active"), tmp_path)
    result = run_hook("emit", {"session_id": SESSION, "session_title": f"[I] {TASK}"}, tmp_path)
    assert result.stdout == ""


def test_emit_stays_silent_for_a_session_with_no_recorded_title(tmp_path: Path) -> None:
    result = run_hook("emit", {"session_id": SESSION, "session_title": "Some session"}, tmp_path)
    assert result.stdout == ""


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shell_scripts_pass_shellcheck() -> None:
    subprocess.run(["shellcheck", str(HOOK), str(SETUP)], check=True)
