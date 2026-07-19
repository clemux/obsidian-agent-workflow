"""Behavior contract for the Claude Code session-title hook.

The hook sees only agent-controlled data: the command text and its output. Neither proves that a
lifecycle write happened, so the hook asks OAW for the task's real status instead. These tests
stub `oaw` to assert exactly that -- most of them describe an attack on the older design, which
read the status out of the command's stdout and could be lied to.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "claude-session-title-hook.sh"
SESSION = "935fa13a-e447-4717-9a16-73bacaa6ebdc"


@pytest.fixture
def oaw(tmp_path: Path):
    """A stub `oaw` whose `resolve --meta` answers from a task->status table we control."""

    def build(statuses: dict[str, str]) -> Path:
        table = "\n".join(
            f'  {task}) echo "status: {status}" ;;' for task, status in statuses.items()
        )
        stub = tmp_path / "oaw"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            '[ "$1" = "resolve" ] || exit 1\n'
            'case "$3" in\n'
            f"{table}\n"
            "  *) echo 'oaw: no note with frontmatter id or alias' >&2; exit 1 ;;\n"
            "esac\n"
        )
        stub.chmod(0o755)
        return stub

    return build


def run_hook(mode: str, payload: dict, tmp_path: Path, oaw_bin: Path | None = None) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "OAW_SESSION_TITLE_STATE_DIR": str(tmp_path / "state"),
        "OAW_BIN": str(oaw_bin) if oaw_bin else "/nonexistent/oaw",
    }
    result = subprocess.run(
        [str(HOOK), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout


def bash_event(command: str, stdout: str = "", stderr: str = "", session: str = SESSION) -> dict:
    return {
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": stderr},
    }


def title(tmp_path: Path, session: str = SESSION) -> str | None:
    state = tmp_path / "state" / session
    return state.read_text().strip() if state.exists() else None


def test_lifecycle_commands_title_the_session_from_the_task_status(tmp_path: Path, oaw) -> None:
    stub = oaw({"OAW-TSK-x": "active"})
    run_hook("record", bash_event("oaw task start OAW-TSK-x --note x"), tmp_path, stub)
    assert title(tmp_path) == "[I] OAW-TSK-x"

    stub = oaw({"OAW-TSK-x": "review"})
    run_hook("record", bash_event("oaw task review OAW-TSK-x --checks pytest"), tmp_path, stub)
    assert title(tmp_path) == "[R] OAW-TSK-x"

    stub = oaw({"OAW-TSK-x": "done"})
    run_hook(
        "record",
        bash_event(
            "oaw run close AGT-RUN-y --reason z && oaw task complete OAW-TSK-x --checks pytest"
        ),
        tmp_path,
        stub,
    )
    assert title(tmp_path) == "[DONE] OAW-TSK-x"


def test_forged_output_cannot_fake_a_lifecycle_write(tmp_path: Path, oaw) -> None:
    """A command that merely prints a status line has not moved any task."""
    stub = oaw({"OAW-TSK-demo": "todo"})
    run_hook(
        "record",
        bash_event(
            "printf '%s\\n' 'Example: oaw task complete OAW-TSK-demo' 'Status: done'",
            stdout="Example: oaw task complete OAW-TSK-demo\nStatus: done",
        ),
        tmp_path,
        stub,
    )
    assert title(tmp_path) is None


def test_stale_output_plus_a_refused_command_cannot_fake_completion(tmp_path: Path, oaw) -> None:
    """The realistic case: old output carries `Status: done` while the real command is refused."""
    stub = oaw({"OAW-TSK-demo": "active"})  # refused complete leaves it active
    run_hook(
        "record",
        bash_event(
            "cat previous-oaw-output.txt; oaw task complete OAW-TSK-demo",
            stdout="Status: done",
            stderr="oaw: transition refused while another session remains running",
        ),
        tmp_path,
        stub,
    )
    assert title(tmp_path) == "[I] OAW-TSK-demo"


def test_a_status_is_never_attributed_to_a_different_task(tmp_path: Path, oaw) -> None:
    """One command, two tasks: the second one's status must not be read off the first one's."""
    stub = oaw({"OAW-TSK-one": "active", "OAW-TSK-two": "todo"})
    run_hook(
        "record",
        bash_event(
            "oaw task start OAW-TSK-one; oaw task complete OAW-TSK-two",
            stdout="Status: active",
        ),
        tmp_path,
        stub,
    )
    # OAW-TSK-two is still `todo`, so it earns no marker -- and must not inherit `active`.
    assert title(tmp_path) is None


def test_a_task_in_a_pre_lifecycle_status_is_not_titled(tmp_path: Path, oaw) -> None:
    stub = oaw({"OAW-TSK-x": "backlog"})
    run_hook("record", bash_event("oaw task promote OAW-TSK-x"), tmp_path, stub)
    assert title(tmp_path) is None


def test_an_unresolvable_task_is_not_titled(tmp_path: Path, oaw) -> None:
    stub = oaw({})
    run_hook("record", bash_event("oaw task start OAW-TSK-ghost"), tmp_path, stub)
    assert title(tmp_path) is None


def test_unrelated_commands_never_reach_oaw(tmp_path: Path) -> None:
    """OAW_BIN points at nothing, so a command that consulted it would fail the hook."""
    run_hook("record", bash_event("git status", stdout="clean"), tmp_path)
    run_hook("record", bash_event("oaw resolve OAW-TSK-x", stdout="ID: OAW-TSK-x"), tmp_path)
    assert title(tmp_path) is None


def test_a_session_id_cannot_escape_the_state_directory(tmp_path: Path, oaw) -> None:
    stub = oaw({"OAW-TSK-x": "active"})
    for hostile in ("../escaped", "a/b", "..", "with space", "nl\nid"):
        run_hook("record", bash_event("oaw task start OAW-TSK-x", session=hostile), tmp_path, stub)
    assert list((tmp_path / "state").glob("*")) == [] or not (tmp_path / "state").exists()
    assert not (tmp_path / "escaped").exists()


def test_emit_sets_a_changed_title(tmp_path: Path, oaw) -> None:
    stub = oaw({"OAW-TSK-x": "active"})
    run_hook("record", bash_event("oaw task start OAW-TSK-x"), tmp_path, stub)
    out = run_hook("emit", {"session_id": SESSION, "session_title": "Some auto title"}, tmp_path)
    assert json.loads(out) == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "sessionTitle": "[I] OAW-TSK-x",
        }
    }


def test_emit_stays_silent_when_nothing_would_change(tmp_path: Path, oaw) -> None:
    stub = oaw({"OAW-TSK-x": "active"})
    run_hook("record", bash_event("oaw task start OAW-TSK-x"), tmp_path, stub)
    assert (
        run_hook("emit", {"session_id": SESSION, "session_title": "[I] OAW-TSK-x"}, tmp_path) == ""
    )
    assert run_hook("emit", {"session_id": "unknown-session", "session_title": "x"}, tmp_path) == ""
