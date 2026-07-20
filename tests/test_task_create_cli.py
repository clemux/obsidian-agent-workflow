import datetime as dt
from pathlib import Path

import pytest

from tests import support
from tests.support import (
    run_record_for,
    snapshot_tree_without_following_symlinks,
    write,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault: just the Obsidian Agent Workflow project index.

    Most tests in this file resolve ``obs:OAW``/``--project`` against this
    project; tests that also need an existing task, capture notes, another
    project, or the project-create template add those
    factories inline at the top of the test.
    """
    root = support.make_vault(tmp_path)
    support.add_project_index(root, "Obsidian Agent Workflow", "OAW-index")
    return root


@pytest.fixture
def run_oaw(vault: Path):
    return support.make_runner(vault)


def add_resolver_cli_task(vault: Path) -> Path:
    """Add the legacy OAW-TSK-cli task note (id/path collision fixture)."""
    return support.add_task(
        vault,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )


def write_canonical_capture(vault: Path, note_id: str, project_line: str) -> Path:
    path = vault / "Captures/Entries" / f"{note_id}.md"
    write(
        path,
        "---\n"
        f"id: {note_id}\n"
        "aliases:\n"
        f"  - {note_id}\n"
        "type: capture\n"
        "created: 2026-07-19T10:00:00+00:00\n"
        "status: inbox\n"
        f"{project_line}\n"
        "review_after:\n"
        "destinations:\n"
        "---\n"
        "\n"
        f"# {note_id}\n",
    )
    return path


def test_task_create_defaults_to_backlog_with_derived_id(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Improve resolver errors",
        "--note",
        "Error messages should list candidates.",
        "--priority",
        "2",
        "--effort",
        "M",
        "--tag",
        "resolver-errors",
        "--tag",
        "resolver-errors",
        "--tag",
        "cli-contract",
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        "Created: Projects/Obsidian Agent Workflow/Tasks/Improve resolver errors.md" in proc.stdout
    )
    assert "ID: OAW-TSK-improve-resolver-errors" in proc.stdout
    assert "Status: backlog" in proc.stdout
    note = (vault / "Projects/Obsidian Agent Workflow/Tasks/Improve resolver errors.md").read_text(
        encoding="utf-8"
    )
    assert "type: task" in note
    assert "project: obsidian-agent-workflow" in note
    assert "status: backlog" in note
    assert "preparedness: needs-triage" in note
    assert "priority: 2" in note
    assert "effort: M" in note
    assert '  - "resolver-errors"' in note
    assert '  - "cli-contract"' in note
    assert "id: OAW-TSK-improve-resolver-errors" in note
    assert 'session-ids:\n  - "test-thread"' in note
    assert "Error messages should list candidates." in note
    assert "- [[Projects/Obsidian Agent Workflow/Index|OAW-index]]" in note
    assert "## Agent sessions" in note
    resolved = run_oaw("resolve", "--json", "OAW-TSK-improve-resolver-errors")
    assert resolved.returncode == 0, resolved.stderr
    listing = run_oaw("list", "--project", "Obsidian Agent Workflow")
    assert "OAW-TSK-improve-resolver-errors" in listing.stdout


def test_task_create_accepts_explicit_preparedness(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Prepared task",
        "--preparedness",
        "prepared",
    )

    assert proc.returncode == 0, proc.stderr
    note = (vault / "Projects/Obsidian Agent Workflow/Tasks/Prepared task.md").read_text(
        encoding="utf-8"
    )
    assert "status: backlog\npreparedness: prepared\n" in note


def test_task_create_writes_timezone_aware_iso8601_created_timestamp(run_oaw, vault):
    before = dt.datetime.now(dt.UTC)
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Timestamped task",
    )
    after = dt.datetime.now(dt.UTC)
    assert proc.returncode == 0, proc.stderr

    note = (vault / "Projects/Obsidian Agent Workflow/Tasks/Timestamped task.md").read_text(
        encoding="utf-8"
    )
    created_line = next(line for line in note.splitlines() if line.startswith("created:"))
    created_value = created_line.split(":", 1)[1].strip()
    parsed = dt.datetime.fromisoformat(created_value.replace("Z", "+00:00"))

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None
    assert parsed >= before - dt.timedelta(seconds=1)
    assert parsed <= after + dt.timedelta(seconds=1)


def test_task_create_todo_sets_status(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "Obsidian Agent Workflow",
        "--title",
        "Todo task",
        "--status",
        "todo",
    )
    assert proc.returncode == 0, proc.stderr
    assert "ID: OAW-TSK-todo-task" in proc.stdout
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Todo task.md").read_text(
        encoding="utf-8"
    )
    assert "status: todo" in task


def test_task_create_start_is_atomic_without_capture(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Atomic started task",
        "--start",
    )

    assert proc.returncode == 0, proc.stderr
    assert "Status: active" in proc.stdout
    assert "Run: AGT-RUN-OAW-TSK-atomic-started-task" in proc.stdout
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Atomic started task.md").read_text(
        encoding="utf-8"
    )
    assert "status: active" in task
    assert "execution: agent" in task
    run = run_record_for(vault, "test-thread").read_text(encoding="utf-8")
    assert 'task_id: "OAW-TSK-atomic-started-task"' in run
    assert "run_state: running" in run


def test_task_create_rejects_start_with_human_execution_without_writes(run_oaw, vault):
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Human task",
        "--execution",
        "human",
        "--start",
    )

    assert proc.returncode == 1
    assert "cannot --start a task with human execution" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_create_duplicate_id_fails_without_writes(run_oaw, vault):
    add_resolver_cli_task(vault)
    before = snapshot_tree_without_following_symlinks(vault)
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Fresh title",
        "--id",
        "OAW-TSK-cli",
    )
    assert proc.returncode == 1
    assert "already in use" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_create_existing_path_fails(run_oaw, vault):
    add_resolver_cli_task(vault)
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Resolver CLI",
        "--id",
        "OAW-TSK-resolver-duplicate",
    )
    assert proc.returncode == 1
    assert "task note already exists" in proc.stderr


def test_task_create_unknown_project_fails(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "No Such Project",
        "--title",
        "Anything",
    )
    assert proc.returncode == 1
    assert "project not found" in proc.stderr


def test_task_create_requires_session_id(run_oaw, vault):
    env = {
        "CODEX_THREAD_ID": "",
        "CLAUDE_SESSION_ID": "",
        "CLAUDE_CODE_SESSION_ID": "",
        "OPENCODE_SESSION_ID": "",
        "GEMINI_SESSION_ID": "",
    }
    proc = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "No session task",
        env=env,
    )
    assert proc.returncode == 1
    assert "no stable session ID" in proc.stderr
    allowed = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "No session task",
        "--allow-missing-session-id",
        env=env,
    )
    assert allowed.returncode == 0, allowed.stderr
    note = (vault / "Projects/Obsidian Agent Workflow/Tasks/No session task.md").read_text(
        encoding="utf-8"
    )
    assert "session-ids:" not in note
    assert "`session_id=unavailable`" in note


def test_task_create_from_capture_is_atomic_and_preserves_provenance(run_oaw, vault):
    support.add_captures(vault)
    capture_path = vault / "Projects/Obsidian Agent Workflow/Inbox/Active capture.md"
    original = capture_path.read_text(encoding="utf-8")
    capture_path.write_text(
        original
        + "\n## Outcome\n\nExpected next shape: route the regression into a verified task.\n"
        + "\n## Evidence\n\nRouting-regression investigation details stay here.\n",
        encoding="utf-8",
    )
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "obs:OAW-CAP-active",
        "--title",
        "Investigate routing regression",
        "--status",
        "todo",
        "--note",
        "Reproduce and fix the routing regression.",
        "--tag",
        "capture-routing",
        "--tag",
        "capture-routing",
        "--tag",
        "cli",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Status: todo" in proc.stdout
    assert "Capture: OAW-CAP-active -> triaged" in proc.stdout
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Investigate routing regression.md"
    task = task_path.read_text(encoding="utf-8")
    capture = capture_path.read_text(encoding="utf-8")
    tags = task.split("tags:\n", 1)[1].split("source-capture:", 1)[0]
    assert tags == (
        '  - "projects"\n'
        '  - "obsidian-agent-workflow"\n'
        '  - "task"\n'
        '  - "capture-routing"\n'
        '  - "cli"\n'
    )
    assert "source-capture: OAW-CAP-active" in task
    assert "[[Projects/Obsidian Agent Workflow/Inbox/Active capture|OAW-CAP-active]]" in task
    assert (
        "[[Projects/Obsidian Agent Workflow/Tasks/Investigate routing regression"
        "|OAW-TSK-investigate-routing-regression]]" in capture
    )
    assert (
        'destinations:\n  - "[[Projects/Obsidian Agent Workflow/Tasks/Investigate routing '
        'regression|OAW-TSK-investigate-routing-regression]]"' in capture
    )
    assert "status: triaged" in capture
    assert "Expected next shape: route the regression into a verified task." in capture
    assert "Routing-regression investigation details stay here." in capture


def test_task_create_from_capture_start_creates_active_task(run_oaw, vault):
    support.add_captures(vault)
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-active",
        "--title",
        "Start capture work",
        "--start",
    )
    assert proc.returncode == 0, proc.stderr
    assert "Status: active" in proc.stdout
    task = (vault / "Projects/Obsidian Agent Workflow/Tasks/Start capture work.md").read_text(
        encoding="utf-8"
    )
    assert "status: active" in task
    assert 'session-ids:\n  - "test-thread"' in task


def test_task_create_from_capture_start_requires_real_session_provenance(run_oaw, vault):
    support.add_captures(vault)
    before = snapshot_tree_without_following_symlinks(vault)
    env = {
        "CODEX_THREAD_ID": "",
        "CLAUDE_SESSION_ID": "",
        "CLAUDE_CODE_SESSION_ID": "",
        "OPENCODE_SESSION_ID": "",
        "GEMINI_SESSION_ID": "",
    }
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-active",
        "--title",
        "Start without provenance",
        "--start",
        env=env,
    )
    assert proc.returncode == 1
    assert "no stable session ID" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_create_rejects_conflicting_capture_intents(run_oaw, vault):
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-active",
        "--title",
        "Conflicting intent",
        "--status",
        "todo",
        "--start",
    )
    assert proc.returncode == 2
    assert "not allowed with argument" in proc.stderr


def test_task_create_from_capture_creation_failure_leaves_capture_unchanged(run_oaw, vault):
    support.add_captures(vault)
    add_resolver_cli_task(vault)
    before = snapshot_tree_without_following_symlinks(vault)
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-active",
        "--title",
        "Duplicate task",
        "--id",
        "OAW-TSK-cli",
    )
    assert proc.returncode == 1
    assert "already in use" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_create_from_capture_link_failure_leaves_capture_unchanged(run_oaw, vault):
    capture_path = vault / "Projects/Obsidian Agent Workflow/Inbox/Alias capture.md"
    write(
        capture_path,
        """---
type: capture
project: obsidian-agent-workflow
status: capture
aliases:
  - OAW-CAP-alias-only
---

# Alias capture

Routing-regression evidence.
""",
    )
    before = snapshot_tree_without_following_symlinks(vault)
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-alias-only",
        "--title",
        "Must not be created",
    )
    assert proc.returncode == 1
    assert "stable frontmatter id" in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_task_create_from_canonical_capture_metadata(run_oaw, vault):
    write_canonical_capture(vault, "OAW-CAP-canon", "project: obsidian-agent-workflow")
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-canon",
        "--title",
        "Promoted canonical capture",
    )
    assert proc.returncode == 0, proc.stderr
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Promoted canonical capture.md"
    assert task_path.exists()
    assert "Capture: OAW-CAP-canon -> triaged" in proc.stdout
    capture = (vault / "Captures/Entries/OAW-CAP-canon.md").read_text(encoding="utf-8")
    assert "status: triaged" in capture
    assert (
        "[[Projects/Obsidian Agent Workflow/Tasks/Promoted canonical capture"
        "|OAW-TSK-promoted-canonical-capture]]" in capture
    )


def test_task_create_from_capture_project_conflict(run_oaw, vault):
    support.add_project_index(vault, "Codex Delegation", "CDX-index")
    write_canonical_capture(vault, "OAW-CAP-conflict", "project: obsidian-agent-workflow")
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-conflict",
        "--project",
        "obs:CDX",
        "--title",
        "Wrong project promotion",
    )
    assert proc.returncode == 1
    assert "conflicts with the capture's project metadata" in proc.stderr
    assert not (vault / "Projects/Codex Delegation/Tasks/Wrong project promotion.md").exists()
    capture = (vault / "Captures/Entries/OAW-CAP-conflict.md").read_text(encoding="utf-8")
    assert "status: inbox" in capture


def test_task_create_from_capture_no_metadata_outside_projects(run_oaw, vault):
    write_canonical_capture(vault, "OAW-CAP-orphan", "project:")
    proc = run_oaw(
        "task",
        "create",
        "--from-capture",
        "OAW-CAP-orphan",
        "--title",
        "Orphan promotion",
    )
    assert proc.returncode == 1
    assert "--project" in proc.stderr
