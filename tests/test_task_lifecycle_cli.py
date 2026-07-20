import datetime as dt
import json
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

from oaw import cli, lifecycle, resolver
from oaw.errors import OawError
from tests import support
from tests.support import (
    run_record_for,
    snapshot_tree_without_following_symlinks,
    write,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal lifecycle vault: the project index and the Resolver CLI task.

    Tests pay only for the notes nearly all of them touch. Tests that need the
    archived task, captures, or the vault-wide agent task add them inline.
    """
    root = support.make_vault(tmp_path)
    support.add_project_index(root, "Obsidian Agent Workflow", "OAW-index")
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )
    return root


@pytest.fixture
def run_oaw(vault: Path):
    """Return an in-process runner bound to the minimal ``vault``."""
    return support.make_runner(vault)


def test_task_start_updates_status_and_session(run_oaw, vault):
    proc = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started work.")
    assert proc.returncode == 0, proc.stderr
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "status: active" in task
    assert "CODEX_THREAD_ID=test-thread" in task
    assert 'session-ids:\n  - "test-thread"\n' in task


def test_task_start_is_idempotent_for_same_identity(vault):
    env = support.cli_env(vault)
    first = support.run_oaw_subprocess(
        ["task", "start", "OAW-TSK-cli", "--note", "First start."], env
    )
    second = support.run_oaw_subprocess(
        ["task", "start", "OAW-TSK-cli", "--note", "Refresh start."], env
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    records = list((vault / "Agents/Runs").glob("*.md"))
    assert len(records) == 1
    text = records[0].read_text(encoding="utf-8")
    assert " — start — First start." in text
    assert " — refresh — Refresh start." in text
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "execution: agent" in task


def test_claude_refresh_uses_provider_and_session_identity_and_preserves_env(
    vault,
):
    cleared = {
        "CODEX_THREAD_ID": "",
        "CLAUDE_SESSION_ID": "",
        "CLAUDE_CODE_SESSION_ID": "",
        "OPENCODE_SESSION_ID": "",
        "GEMINI_SESSION_ID": "",
    }
    first = support.run_oaw_subprocess(
        [
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Claude Code env start.",
        ],
        {**support.cli_env(vault), **cleared, "CLAUDE_CODE_SESSION_ID": "shared-claude-session"},
    )
    second = support.run_oaw_subprocess(
        [
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Claude env refresh.",
        ],
        {**support.cli_env(vault), **cleared, "CLAUDE_SESSION_ID": "shared-claude-session"},
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    records = list((vault / "Agents/Runs").glob("*.md"))
    assert len(records) == 1
    run_text = records[0].read_text(encoding="utf-8")
    assert "agent_session_env: CLAUDE_CODE_SESSION_ID" in run_text
    assert "agent_session_env: CLAUDE_SESSION_ID\n" not in run_text
    assert " — start — Claude Code env start." in run_text
    assert " — refresh — Claude env refresh." in run_text
    task_text = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text(
        encoding="utf-8"
    )
    assert "`CLAUDE_CODE_SESSION_ID=shared-claude-session`" in task_text
    assert "`CLAUDE_SESSION_ID=shared-claude-session`" in task_text


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("start", []),
        ("review", ["--checks", "pytest"]),
        ("complete", ["--checks", "pytest"]),
    ],
)
def test_lifecycle_rejects_corrupt_deterministic_run_before_writing(command, extra, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(
        run_path.read_text(encoding="utf-8").replace(
            "agent_session_env: CODEX_THREAD_ID",
            "agent_session_env: CLAUDE_SESSION_ID",
        ),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    result = run_oaw(
        "task",
        command,
        "OAW-TSK-cli",
        "--note",
        "Must reject corrupt record.",
        *extra,
    )

    assert result.returncode == 1
    assert "run record validation failed" in result.stderr
    assert "unsupported provider/session environment" in result.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            'task_id: "OAW-TSK-cli"',
            'task_id: "OAW-TSK-archived"',
            "task_id does not match",
        ),
        (
            'task: "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"',
            'task: "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-cli]]"',
            "task link does not match",
        ),
    ],
)
def test_refresh_rejects_misplaced_deterministic_task_scope(old, new, expected, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    before = snapshot_tree_without_following_symlinks(vault)

    refreshed = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Must not refresh.")

    assert refreshed.returncode == 1
    assert expected in refreshed.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_multiple_sessions_run_same_task_and_review_conflicts_even_when_stale(run_oaw, vault):
    first = run_oaw("task", "start", "OAW-TSK-cli", "--note", "First session.")
    second = run_oaw(
        "task",
        "start",
        "OAW-TSK-cli",
        "--note",
        "Second session.",
        env={"CODEX_THREAD_ID": "other-thread"},
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(list((vault / "Agents/Runs").glob("*.md"))) == 2

    other = run_record_for(vault, "other-thread")
    other.write_text(
        other.read_text(encoding="utf-8")
        .replace(
            next(
                line
                for line in other.read_text(encoding="utf-8").splitlines()
                if line.startswith("started_at:")
            ),
            'started_at: "1999-01-01T00:00:00Z"',
        )
        .replace(
            next(
                line
                for line in other.read_text(encoding="utf-8").splitlines()
                if line.startswith("last_event_at:")
            ),
            'last_event_at: "2000-01-01T00:00:00Z"',
        ),
        encoding="utf-8",
    )
    review = run_oaw(
        "task",
        "review",
        "OAW-TSK-cli",
        "--note",
        "Review handoff.",
        "--checks",
        "pytest",
    )
    assert review.returncode == 1
    assert "another session remains running" in review.stderr
    assert (
        "status: active"
        in (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    )
    listed = run_oaw("run", "list", "--json")
    rows = json.loads(listed.stdout)
    stale = next(row for row in rows if row["agent_session_id"] == "other-thread")
    assert stale["stale"]
    assert stale["run_state"] == "running"


def test_run_list_filters_by_session_and_current_session(run_oaw):
    mine = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Mine.")
    other = run_oaw(
        "task",
        "start",
        "OAW-TSK-cli",
        "--note",
        "Other session.",
        env={"CODEX_THREAD_ID": "other-thread"},
    )
    assert mine.returncode == 0, mine.stderr
    assert other.returncode == 0, other.stderr

    by_session = run_oaw("run", "list", "--session", "test-thread", "--json")
    assert by_session.returncode == 0, by_session.stderr
    rows = json.loads(by_session.stdout)
    assert len(rows) == 1
    assert rows[0]["agent_session_id"] == "test-thread"

    current = run_oaw("run", "list", "--current-session", "--json")
    assert current.returncode == 0, current.stderr
    assert json.loads(current.stdout) == rows

    other_rows = json.loads(run_oaw("run", "list", "--session", "other-thread", "--json").stdout)
    assert len(other_rows) == 1
    assert other_rows[0]["agent_session_id"] == "other-thread"

    missing = run_oaw("run", "list", "--session", "unknown-thread", "--json")
    assert missing.returncode == 0, missing.stderr
    assert json.loads(missing.stdout) == []


def test_run_list_session_filter_matches_appended_closer_session(run_oaw):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Mine.")
    assert started.returncode == 0, started.stderr
    run_id = next(
        line.split("Run: ", 1)[1]
        for line in started.stdout.splitlines()
        if line.startswith("Run: ")
    )
    closed = run_oaw(
        "run",
        "close",
        run_id,
        "--reason",
        "administrative cleanup",
        env={"CODEX_THREAD_ID": "closer-thread"},
    )
    assert closed.returncode == 0, closed.stderr

    rows = json.loads(run_oaw("run", "list", "--session", "closer-thread", "--json").stdout)
    assert len(rows) == 1
    assert rows[0]["id"] == run_id
    assert rows[0]["agent_session_id"] == "test-thread"


def test_run_list_current_session_requires_a_real_session_id(run_oaw):
    result = run_oaw(
        "run",
        "list",
        "--current-session",
        env={
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        },
    )
    assert result.returncode == 1
    assert "no stable session ID found" in result.stderr


@pytest.mark.parametrize("command", ["review", "complete"])
@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("id: AGT-RUN-", "id: AGT-RUN-forged-", "id/filename mismatch"),
        (
            'task_id: "OAW-TSK-cli"',
            'task_id: "OAW-TSK-archived"',
            "run-id/identity mismatch",
        ),
    ],
)
def test_transition_fails_closed_on_corrupt_sibling_record(
    command, old, new, expected, run_oaw, vault
):
    current = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Current.")
    sibling = run_oaw(
        "task",
        "start",
        "OAW-TSK-cli",
        "--note",
        "Sibling.",
        env={"CODEX_THREAD_ID": "other-thread"},
    )
    assert current.returncode == 0, current.stderr
    assert sibling.returncode == 0, sibling.stderr
    sibling_path = run_record_for(vault, "other-thread")
    sibling_path.write_text(
        sibling_path.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    result = run_oaw(
        "task",
        command,
        "OAW-TSK-cli",
        "--note",
        "Must fail closed.",
        "--checks",
        "pytest",
    )

    assert result.returncode == 1
    assert expected in result.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_one_shot_complete_refuses_another_running_session(run_oaw, vault):
    started = run_oaw(
        "task",
        "start",
        "OAW-TSK-cli",
        "--note",
        "Other session owns the task.",
        env={"CODEX_THREAD_ID": "other-thread"},
    )
    assert started.returncode == 0, started.stderr
    before = snapshot_tree_without_following_symlinks(vault)
    completed = run_oaw(
        "task",
        "complete",
        "OAW-TSK-cli",
        "--note",
        "Cannot bypass concurrency.",
        "--checks",
        "pytest",
    )
    assert completed.returncode == 1
    assert "another session remains running" in completed.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_cross_task_run_does_not_block_one_shot_completion(run_oaw, vault):
    started = run_oaw(
        "task",
        "start",
        "OAW-TSK-cli",
        "--note",
        "Unrelated task run.",
        env={"CODEX_THREAD_ID": "other-thread"},
    )
    assert started.returncode == 0, started.stderr
    write(
        vault / "Tasks/Independent.md",
        """---
type: task
status: todo
id: ROOT-TSK-independent
aliases:
  - ROOT-TSK-independent
---

# Independent
""",
    )
    completed = run_oaw(
        "task",
        "complete",
        "ROOT-TSK-independent",
        "--note",
        "Completed independently.",
        "--checks",
        "pytest",
    )
    assert completed.returncode == 0, completed.stderr
    task = (vault / "Tasks/Independent.md").read_text(encoding="utf-8")
    assert "status: done" in task
    assert "execution:" not in task
    run = run_record_for(vault, "test-thread").read_text(encoding="utf-8")
    assert "run_state: completed" in run
    assert "verification: pytest" in run


def test_pause_changes_only_callers_run_and_preserves_task_status(run_oaw, vault):
    for session in ("test-thread", "other-thread"):
        started = run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            f"Start {session}.",
            env={"CODEX_THREAD_ID": session},
        )
        assert started.returncode == 0, started.stderr
    paused = run_oaw("task", "pause", "OAW-TSK-cli", "--note", "Pausing caller.")
    assert paused.returncode == 0, paused.stderr
    assert "run_state: paused" in run_record_for(vault, "test-thread").read_text()
    assert "run_state: running" in run_record_for(vault, "other-thread").read_text()
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "status: active" in task


@pytest.mark.parametrize("execution", [None, "invalid"])
def test_pause_requires_explicit_agent_or_hybrid_execution(execution, run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    if execution:
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: todo", f"status: todo\nexecution: {execution}"
            ),
            encoding="utf-8",
        )
    before = snapshot_tree_without_following_symlinks(vault)
    paused = run_oaw("task", "pause", "OAW-TSK-cli", "--note", "No run.")
    assert paused.returncode == 1
    assert "requires execution: agent or hybrid" in paused.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_human_task_refuses_agent_start_before_any_write(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: todo", "status: todo\nexecution: human"
        ),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Must refuse.")
    assert started.returncode == 1
    assert "managed in Obsidian UI" in started.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize("command", ["backlog", "promote"])
def test_queue_transition_refuses_while_any_run_is_running(command, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Run remains active.")
    assert started.returncode == 0, started.stderr
    before = snapshot_tree_without_following_symlinks(vault)

    moved = run_oaw("task", command, "OAW-TSK-cli", "--note", "Must wait.")

    assert moved.returncode == 1
    assert "while an agent run is running" in moved.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_note_refreshes_only_existing_matching_running_run(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    noted = run_oaw("task", "note", "OAW-TSK-cli", "--note", "Progress.", "--checks", "pytest")
    assert noted.returncode == 0, noted.stderr
    run_text = run_record_for(vault, "test-thread").read_text(encoding="utf-8")
    assert " — note — Progress. — verification: pytest" in run_text

    write(
        vault / "Tasks/Unstarted.md",
        """---
type: task
status: todo
id: ROOT-TSK-unstarted
aliases:
  - ROOT-TSK-unstarted
---

# Unstarted
""",
    )
    count = len(list((vault / "Agents/Runs").glob("*.md")))
    unstarted = run_oaw("task", "note", "ROOT-TSK-unstarted", "--note", "Trace only.")
    assert unstarted.returncode == 0, unstarted.stderr
    assert len(list((vault / "Agents/Runs").glob("*.md"))) == count


def test_run_close_records_closer_without_changing_task(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_before = task_path.read_bytes()
    run_path = run_record_for(vault, "test-thread")
    identifier = run_path.stem

    closed = run_oaw(
        "run",
        "close",
        identifier,
        "--reason",
        "true",
        env={"CODEX_THREAD_ID": "closer-thread"},
    )

    assert closed.returncode == 0, closed.stderr
    text = run_path.read_text(encoding="utf-8")
    assert 'agent_session_id: "test-thread"' in text
    assert '  - "test-thread"' in text
    assert '  - "closer-thread"' in text
    assert "run_state: closed" in text
    assert 'ended_reason: "true"' in text
    assert task_before == task_path.read_bytes()


def test_run_close_rejects_an_unsafe_run_id_without_writes(run_oaw, vault):
    before = snapshot_tree_without_following_symlinks(vault)

    closed = run_oaw(
        "run",
        "close",
        "../../Projects/Obsidian Agent Workflow/Tasks/Resolver CLI",
        "--reason",
        "unsafe lookup",
    )

    assert closed.returncode == 1
    assert "invalid run id" in closed.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "agent_session_env: CODEX_THREAD_ID",
            "agent_session_env: CLAUDE_SESSION_ID",
            "unsupported provider/session environment",
        ),
        (
            'task: "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"',
            'task: "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-cli]]"',
            "task link does not match resolved task path/id",
        ),
    ],
)
def test_run_close_rejects_forged_identity_or_task_scope(old, new, expected, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    before = snapshot_tree_without_following_symlinks(vault)

    closed = run_oaw(
        "run",
        "close",
        run_path.stem,
        "--reason",
        "Must reject forged record.",
        env={"CODEX_THREAD_ID": "closer-thread"},
    )

    assert closed.returncode == 1
    assert expected in closed.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            'session-ids:\n  - "test-thread"',
            "session-ids: owner",
            "malformed session-ids",
        ),
        ("run_state: running", "run_state: unknown", "malformed run_state unknown"),
        (
            'started_at: "',
            'started_at: "not-a-timestamp-',
            "malformed started_at",
        ),
    ],
)
def test_run_close_rejects_malformed_mutable_schema_without_writes(
    old, new, expected, run_oaw, vault
):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    before = snapshot_tree_without_following_symlinks(vault)

    closed = run_oaw(
        "run",
        "close",
        run_path.stem,
        "--reason",
        "must not mutate",
        env={"CODEX_THREAD_ID": "closer-thread"},
    )

    assert closed.returncode == 1
    assert "run record validation failed" in closed.stderr
    assert expected in closed.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize("operation", ["list", "audit", "close", "start", "create-start"])
def test_symlinked_run_directory_fails_closed_without_writes(operation, run_oaw, vault):
    support.add_agent_task(
        vault,
        "Resolve vault-wide Obsidian task IDs.md",
        "AGT-TSK-obsidian-task-ids",
        status="open",
        body="# Resolve vault-wide Obsidian task IDs\n\n## Problem\n\nText.\n",
    )
    with tempfile.TemporaryDirectory() as outside_raw:
        outside = Path(outside_raw)
        marker = outside / "outside-marker.txt"
        marker.write_text("outside must remain untouched\n", encoding="utf-8")
        registry = vault / "Agents/Runs"
        registry.symlink_to(outside, target_is_directory=True)
        before_vault = snapshot_tree_without_following_symlinks(vault)
        before_outside = snapshot_tree_without_following_symlinks(outside)
        identifier = "AGT-RUN-OAW-TSK-cli-codex-0123456789ab"
        arguments = {
            "list": ("run", "list"),
            "audit": ("run", "audit"),
            "close": ("run", "close", identifier, "--reason", "must fail"),
            "start": (
                "task",
                "start",
                "OAW-TSK-cli",
                "--note",
                "Must not follow registry symlink.",
            ),
            "create-start": (
                "task",
                "create",
                "--project",
                "Obsidian Agent Workflow",
                "--title",
                "Blocked directory symlink task",
                "--start",
            ),
        }[operation]

        result = run_oaw(*arguments)

        assert result.returncode == 1
        assert "run registry directory must not be a symlink" in result.stderr
        assert before_vault == snapshot_tree_without_following_symlinks(vault)
        assert before_outside == snapshot_tree_without_following_symlinks(outside)


@pytest.mark.parametrize("operation", ["list", "audit", "close", "start", "create-start"])
def test_canonical_run_symlink_entry_fails_closed_without_writes(operation, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    canonical = run_record_for(vault, "test-thread")
    with tempfile.TemporaryDirectory() as outside_raw:
        outside = Path(outside_raw)
        outside_run = outside / canonical.name
        outside_run.write_bytes(canonical.read_bytes())
        canonical.unlink()
        canonical.symlink_to(outside_run)
        before_vault = snapshot_tree_without_following_symlinks(vault)
        before_outside = snapshot_tree_without_following_symlinks(outside)
        arguments = {
            "list": ("run", "list"),
            "audit": ("run", "audit"),
            "close": (
                "run",
                "close",
                canonical.stem,
                "--reason",
                "must fail",
            ),
            "start": (
                "task",
                "start",
                "OAW-TSK-cli",
                "--note",
                "Must not follow run symlink.",
            ),
            "create-start": (
                "task",
                "create",
                "--project",
                "Obsidian Agent Workflow",
                "--title",
                "Blocked entry symlink task",
                "--start",
            ),
        }[operation]

        result = run_oaw(*arguments)

        assert result.returncode == 1
        if operation == "audit":
            assert "noncanonical registry artifact" in result.stdout
        else:
            assert "run registry contains symlink entries" in result.stderr
        assert before_vault == snapshot_tree_without_following_symlinks(vault)
        assert before_outside == snapshot_tree_without_following_symlinks(outside)


def test_run_audit_reports_clean_registry(run_oaw):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr

    audited = run_oaw("run", "audit")

    assert audited.returncode == 0, audited.stderr
    assert audited.stdout == "Run audit: clean\n"


def test_run_audit_reports_id_and_timestamp_inconsistencies(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(
        run_path.read_text(encoding="utf-8")
        .replace(f"id: {run_path.stem}", "id: AGT-RUN-wrong")
        .replace(
            next(
                line
                for line in run_path.read_text(encoding="utf-8").splitlines()
                if line.startswith("last_event_at:")
            ),
            'last_event_at: "not-a-timestamp"',
        ),
        encoding="utf-8",
    )

    audited = run_oaw("run", "audit")

    assert audited.returncode == 1
    assert "id/filename mismatch" in audited.stdout
    assert "run-id/identity mismatch" in audited.stdout
    assert "malformed last_event_at" in audited.stdout


def test_run_audit_reports_malformed_schema_and_terminal_metadata(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    run_path.write_text(
        run_path.read_text(encoding="utf-8")
        .replace("type: agent-run", "type: other")
        .replace('  - "test-thread"', '  - "other-thread"\n  - "other-thread"')
        .replace("run_state: running", "run_state: completed"),
        encoding="utf-8",
    )

    audited = run_oaw("run", "audit")

    assert audited.returncode == 1
    assert "malformed type other" in audited.stdout
    assert "duplicate session-ids" in audited.stdout
    assert "agent_session_id missing from session-ids" in audited.stdout
    assert "terminal run missing valid ended_at" in audited.stdout
    assert "terminal run missing ended_reason" in audited.stdout


def test_run_audit_reports_record_under_noncanonical_filename(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    canonical = run_record_for(vault, "test-thread")
    write(vault / "Agents/Runs/misplaced.md", canonical.read_text(encoding="utf-8"))

    audited = run_oaw("run", "audit")

    assert audited.returncode == 1
    assert "misplaced.md: noncanonical registry artifact" in audited.stdout


def test_run_audit_reports_nested_and_non_markdown_artifacts(run_oaw, vault):
    directory = vault / "Agents/Runs"
    write(directory / "README.txt", "not a run\n")
    write(directory / "nested/AGT-RUN-hidden.md", "hidden run\n")

    audited = run_oaw("run", "audit")

    assert audited.returncode == 1
    assert "README.txt: noncanonical registry artifact" in audited.stdout
    assert "nested/AGT-RUN-hidden.md: noncanonical registry artifact" in audited.stdout


def test_run_audit_rejects_extra_keys_project_shape_and_end_ordering(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
    assert started.returncode == 0, started.stderr
    completed = run_oaw(
        "task",
        "complete",
        "OAW-TSK-cli",
        "--note",
        "Complete.",
        "--checks",
        "pytest",
    )
    assert completed.returncode == 0, completed.stderr
    run_path = run_record_for(vault, "test-thread")
    text = run_path.read_text(encoding="utf-8")
    ended_line = next(line for line in text.splitlines() if line.startswith("ended_at:"))
    run_path.write_text(
        text.replace('project: "obsidian-agent-workflow"', "project: [invalid]")
        .replace("run_state: completed", "unexpected: value\nrun_state: completed")
        .replace(ended_line, 'ended_at: "2000-01-01T00:00:00Z"'),
        encoding="utf-8",
    )

    audited = run_oaw("run", "audit")

    assert audited.returncode == 1
    assert "noncanonical schema keys: unexpected" in audited.stdout
    assert "malformed project" in audited.stdout
    assert "ended_at precedes started_at" in audited.stdout
    assert "ended_at precedes last_event_at" in audited.stdout


def test_task_review_requires_checks(run_oaw):
    missing = run_oaw("task", "review", "OAW-TSK-cli", "--note", "Ready for review.")
    assert missing.returncode != 0
    assert "--checks" in missing.stderr


def test_review_does_not_default_missing_execution(run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Established historical run.")
    assert started.returncode == 0, started.stderr
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("execution: agent\n", ""),
        encoding="utf-8",
    )

    reviewed = run_oaw(
        "task",
        "review",
        "OAW-TSK-cli",
        "--note",
        "Historical run ready.",
        "--checks",
        "pytest",
    )

    assert reviewed.returncode == 0, reviewed.stderr
    assert "execution:" not in task_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("note", "checks", "expected"),
    [
        (" \t", "pytest", "non-empty --note"),
        ("Ready for review.", " \t", "non-empty --checks"),
    ],
)
def test_task_review_rejects_blank_note_or_checks_without_writes(
    note, checks, expected, run_oaw, vault
):
    before = snapshot_tree_without_following_symlinks(vault)
    proc = run_oaw("task", "review", "OAW-TSK-cli", "--note", note, "--checks", checks)
    assert proc.returncode == 1
    assert expected in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_review_domain_rejects_blank_values_before_transaction(monkeypatch, vault):
    match = resolver.resolve_id("OAW-TSK-cli", vault)

    class UnexpectedTransaction:
        def __init__(self):
            raise AssertionError("validation must happen before transaction construction")

    monkeypatch.setattr(lifecycle, "VaultTransaction", UnexpectedTransaction)
    for note, checks, expected in (
        (" \t", "pytest", "non-empty --note"),
        ("Ready for review.", " \t", "non-empty --checks"),
    ):
        with pytest.raises(OawError, match=expected):
            lifecycle.update_task(match, vault, "review", note, checks, allow_missing=True)


@pytest.mark.parametrize("initial_status", ["backlog", "todo", "active", "review", "done"])
def test_task_review_accepts_every_lifecycle_source_status(initial_status, run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Established caller run.")
    assert started.returncode == 0, started.stderr
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: active", f"status: {initial_status}"
        ),
        encoding="utf-8",
    )
    proc = run_oaw(
        "task",
        "review",
        "OAW-TSK-cli",
        "--note",
        "Ready for review.",
        "--checks",
        "pytest",
    )
    assert proc.returncode == 0, proc.stderr
    task = task_path.read_text(encoding="utf-8")
    assert "status: review" in task
    assert "Ready for review.; checks: pytest" in task
    assert "CODEX_THREAD_ID=test-thread" in task


def test_task_review_transaction_failure_restores_vault(monkeypatch, run_oaw, vault):
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Established caller run.")
    assert started.returncode == 0, started.stderr
    before = snapshot_tree_without_following_symlinks(vault)
    original_commit = lifecycle.VaultTransaction.commit

    def fail_second_replace(transaction):
        calls = 0

        def replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected run replacement failure")
            Path(source).replace(destination)

        return original_commit(transaction, replace=replace)

    monkeypatch.setenv("OAW_VAULT", str(vault))
    monkeypatch.setenv("CODEX_THREAD_ID", "test-thread")
    monkeypatch.setattr(lifecycle.VaultTransaction, "commit", fail_second_replace)
    stderr = StringIO()
    with redirect_stderr(stderr):
        result = cli.main(
            [
                "task",
                "review",
                "OAW-TSK-cli",
                "--note",
                "Ready for review.",
                "--checks",
                "pytest",
            ]
        )

    assert result == 1
    assert "transaction failed and was rolled back" in stderr.getvalue()
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_complete_requires_checks(run_oaw):
    proc = run_oaw("task", "complete", "OAW-TSK-cli", "--note", "Done.")
    assert proc.returncode != 0
    assert "required: --checks" in proc.stderr


@pytest.mark.parametrize("priority", [1, 2, 3])
def test_task_priority_updates_metadata_trace_and_preserves_task_state(priority, run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: todo\n",
            "status: todo\npriority: 3 # retained comment\neffort: M\n",
        ),
        encoding="utf-8",
    )
    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-cli",
        "--priority",
        str(priority),
        "--note",
        "Re-ranked against the cross-project queue.",
    )

    assert proc.returncode == 0, proc.stderr
    assert f"Priority: {priority}" in proc.stdout
    assert "Status: todo" in proc.stdout
    task = task_path.read_text(encoding="utf-8")
    assert f"status: todo\npriority: {priority} # retained comment\neffort: M\n" in task
    assert 'session-ids:\n  - "test-thread"\n' in task
    assert "`CODEX_THREAD_ID=test-thread`" in task
    assert "Re-ranked against the cross-project queue." in task
    assert not (vault / "Agents/Runs").exists()


@pytest.mark.parametrize(
    "relative_path",
    [
        "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md",
        "Tasks/Root priority task.md",
    ],
)
def test_task_priority_supports_non_project_task_locations(relative_path, run_oaw, vault):
    support.add_agent_task(
        vault,
        "Resolve vault-wide Obsidian task IDs.md",
        "AGT-TSK-obsidian-task-ids",
        status="open",
        body="# Resolve vault-wide Obsidian task IDs\n\n## Problem\n\nText.\n",
    )
    path = vault / relative_path
    if relative_path.startswith("Tasks/"):
        write(
            path,
            """---
type: task
status: backlog
id: ROOT-TSK-priority
aliases:
  - ROOT-TSK-priority
---

# Root priority task
""",
        )
        note_id = "ROOT-TSK-priority"
    else:
        note_id = "AGT-TSK-obsidian-task-ids"

    proc = run_oaw(
        "task",
        "priority",
        note_id,
        "--priority",
        "2",
        "--note",
        "Ranked task.",
    )

    assert proc.returncode == 0, proc.stderr
    assert "priority: 2" in path.read_text(encoding="utf-8")


def test_task_priority_allows_explicit_missing_session_trace(run_oaw, vault):
    cleared = {
        "CODEX_THREAD_ID": "",
        "CLAUDE_SESSION_ID": "",
        "CLAUDE_CODE_SESSION_ID": "",
        "OPENCODE_SESSION_ID": "",
        "GEMINI_SESSION_ID": "",
    }
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"

    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-cli",
        "--priority",
        "2",
        "--note",
        "Untraceable update accepted.",
        "--allow-missing-session-id",
        env=cleared,
    )

    assert proc.returncode == 0, proc.stderr
    task = task_path.read_text(encoding="utf-8")
    assert "priority: 2" in task
    assert "`session_id=unavailable`" in task
    assert "session-ids:" not in task


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("priority:\n  - 2\n", "must be a scalar 1, 2, or 3"),
        ("priority: high\n", "must be a scalar 1, 2, or 3"),
        ("priority: 2\npriority: 3\n", "duplicate field: priority"),
        ("'priority': 2\n", "unsupported or malformed field"),
        ("priority: 2\n  continuation\n", "flat scalar fields and flat block lists"),
        ("broken: [\n", "field broken has an unclosed flow value"),
        ("broken: foo: bar\n", "field broken contains an unsupported YAML value"),
        (
            "broken-list:\n  - foo: bar\n",
            "field broken-list contains an unsupported YAML value",
        ),
    ],
)
def test_task_priority_rejects_malformed_priority_without_writing(
    replacement, expected, run_oaw, vault
):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: todo\n", "status: todo\n" + replacement
        ),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-cli",
        "--priority",
        "1",
        "--note",
        "Must fail.",
    )

    assert proc.returncode == 1
    assert expected in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_priority_rejects_unsupported_location_without_writing(run_oaw, vault):
    path = vault / "Projects/Obsidian Agent Workflow/Archive/Tasks/Outside task.md"
    write(
        path,
        """---
type: task
status: backlog
id: OAW-TSK-outside
aliases:
  - OAW-TSK-outside
---

# Outside task
""",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-outside",
        "--priority",
        "2",
        "--note",
        "Must fail.",
    )

    assert proc.returncode == 1
    assert "lifecycle writes are supported" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_priority_domain_rejects_unclosed_frontmatter_before_transaction(monkeypatch, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    match = resolver.resolve_id("OAW-TSK-cli", vault)
    malformed = task_path.read_text(encoding="utf-8").replace(
        "\n---\n\n# Resolver CLI", "\n\n# Resolver CLI"
    )
    task_path.write_text(malformed, encoding="utf-8")
    before = snapshot_tree_without_following_symlinks(vault)

    class UnexpectedTransaction:
        def __init__(self):
            raise AssertionError("validation must happen before transaction construction")

    monkeypatch.setattr(lifecycle, "VaultTransaction", UnexpectedTransaction)

    with pytest.raises(OawError, match="frontmatter is not closed"):
        lifecycle.update_task_priority(match, vault, 2, "Must fail.", False)
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_priority_rejects_non_task_frontmatter_without_writing(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace("type: task", "type: capture"),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-cli",
        "--priority",
        "2",
        "--note",
        "Must fail.",
    )

    assert proc.returncode == 1
    assert "requires frontmatter type: task" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_priority_rejects_blank_note_without_writing(run_oaw, vault):
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "priority",
        "OAW-TSK-cli",
        "--priority",
        "2",
        "--note",
        "  ",
    )

    assert proc.returncode == 1
    assert "requires non-empty --note" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize("state", ["needs-triage", "needs-design", "prepared"])
def test_task_preparedness_updates_metadata_trace_without_changing_run(state, run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started implementation.")
    assert started.returncode == 0, started.stderr
    run_path = run_record_for(vault, "test-thread")
    before_run = run_path.read_bytes()

    proc = run_oaw(
        "task",
        "preparedness",
        "OAW-TSK-cli",
        "--state",
        state,
        "--note",
        "Assessed design sufficiency.",
    )

    assert proc.returncode == 0, proc.stderr
    assert f"Preparedness: {state}" in proc.stdout
    assert "Status: active" in proc.stdout
    task = task_path.read_text(encoding="utf-8")
    assert f"preparedness: {state}" in task
    assert "status: active" in task
    assert "Assessed design sufficiency." in task
    assert before_run == run_path.read_bytes()


def test_task_preparedness_rejects_malformed_existing_value_without_writing(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: todo\n", "status: todo\npreparedness:\n  - prepared\n"
        ),
        encoding="utf-8",
    )
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "preparedness",
        "OAW-TSK-cli",
        "--state",
        "prepared",
        "--note",
        "Must fail.",
    )

    assert proc.returncode == 1
    assert "must be a scalar" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_note_appends_session_without_status_change(run_oaw, vault):
    support.add_task(
        vault,
        "Obsidian Agent Workflow",
        "Archived task.md",
        "OAW-TSK-archived",
        project="obsidian-agent-workflow",
        status="archived",
        body="# Archived task\n",
    )
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "status: archived\n",
            'status: archived\nsession-ids:\n  - "old,with-comma"\n'
            "  - earlier-thread # prior run\n",
        ),
        encoding="utf-8",
    )

    proc = run_oaw(
        "task",
        "note",
        "OAW-TSK-archived",
        "--note",
        "Reviewed independently.",
        "--checks",
        "python -m unittest",
    )

    assert proc.returncode == 0, proc.stderr
    assert "Updated: Projects/Obsidian Agent Workflow/Tasks/Archived task.md" in proc.stdout
    assert "Status: archived" in proc.stdout
    task = task_path.read_text(encoding="utf-8")
    assert "status: archived" in task
    assert "CODEX_THREAD_ID=test-thread" in task
    assert "Reviewed independently.; checks: python -m unittest" in task
    assert (
        'session-ids:\n  - "old,with-comma"\n  - earlier-thread # prior run\n  - "test-thread"\n'
    ) in task

    repeated = run_oaw(
        "task",
        "note",
        "OAW-TSK-archived",
        "--note",
        "Same session again.",
    )
    assert repeated.returncode == 0, repeated.stderr
    task = task_path.read_text(encoding="utf-8")
    assert task.count('  - "old,with-comma"\n') == 1
    assert task.count("  - earlier-thread # prior run\n") == 1
    assert task.count('  - "test-thread"\n') == 1


def test_task_note_ignores_inline_agent_sessions_marker_before_real_heading(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    write(
        task_path,
        """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-cli
aliases:
  - OAW-TSK-cli
---

# Track feature dogfooding sessions in an Obsidian Base

## Problem

The task's `session-ids` and `## Agent sessions` mix implementation and dogfooding provenance.

## Outcome

Keep the two concepts separate.

## Agent sessions

- 2026-07-13 - Claude Code - `CLAUDE_CODE_SESSION_ID=old-thread` - Created task note.
""",
    )

    proc = run_oaw(
        "task",
        "note",
        "OAW-TSK-cli",
        "--note",
        "Finished the implementation-ready design.",
    )

    assert proc.returncode == 0, proc.stderr
    task = task_path.read_text(encoding="utf-8")
    assert (
        "The task's `session-ids` and `## Agent sessions` mix implementation and "
        "dogfooding provenance.\n\n"
        "## Outcome\n\n"
        "Keep the two concepts separate.\n\n"
        "## Agent sessions\n\n"
        "- 2026-07-13 - Claude Code - `CLAUDE_CODE_SESSION_ID=old-thread` - "
        "Created task note.\n"
        f"- {dt.date.today().isoformat()} - Codex - `CODEX_THREAD_ID=test-thread` - "
        "Finished the implementation-ready design.\n"
    ) in task


def test_task_note_requires_session_id_unless_allowed(run_oaw, vault):
    env = {
        "CODEX_THREAD_ID": "",
        "CLAUDE_SESSION_ID": "",
        "CLAUDE_CODE_SESSION_ID": "",
        "OPENCODE_SESSION_ID": "",
        "GEMINI_SESSION_ID": "",
    }
    proc = run_oaw(
        "task",
        "note",
        "OAW-TSK-cli",
        "--note",
        "No session.",
        env=env,
    )
    assert proc.returncode != 0
    assert "no stable session ID found" in proc.stderr

    allowed = run_oaw(
        "task",
        "note",
        "OAW-TSK-cli",
        "--note",
        "Accepted missing session.",
        "--allow-missing-session-id",
        env=env,
    )
    assert allowed.returncode == 0, allowed.stderr
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "session_id=unavailable" in task
    assert "session-ids:" not in task


def test_task_backlog_updates_status_and_session(run_oaw, vault):
    proc = run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
    assert proc.returncode == 0, proc.stderr
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "status: backlog" in task
    assert "CODEX_THREAD_ID=test-thread" in task


def test_task_promote_updates_status(run_oaw, vault):
    setup = run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
    assert setup.returncode == 0, setup.stderr
    proc = run_oaw("task", "promote", "OAW-TSK-cli", "--note", "Selected next.")
    assert proc.returncode == 0, proc.stderr
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
    assert "status: todo" in task
