import datetime as dt
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from oaw import cli

from .assertions import Assertions

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "oaw"
FIXTURES = ROOT / "tests" / "fixtures"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_oaw_in_process(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI via cli.main in this process, emulating the subprocess contract.

    The merged mapping replaces os.environ wholesale for the duration of the call,
    matching subprocess.run(env=...). Environment swapping assumes tests within one
    xdist worker run on a single thread. An exception cli.main does not translate is
    a programmer error, not CLI behavior: it propagates and fails the test instead
    of being downgraded to a subprocess-style nonzero exit.
    """
    stdout = StringIO()
    stderr = StringIO()
    saved_environ = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = cli.main(args)
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
    return subprocess.CompletedProcess(
        args=["oaw", *args],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def snapshot_tree_without_following_symlinks(
    root: Path,
) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in sorted([*directories, *files]):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.readlink(path))
            elif path.is_dir():
                snapshot[relative] = ("directory", None)
            else:
                snapshot[relative] = ("file", path.read_bytes())
    return snapshot


class TestOaw(Assertions):
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env["OAW_VAULT"] = str(self.vault)
        self.env["CODEX_THREAD_ID"] = "test-thread"
        write(
            self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md",
            """---
type: task
status: open
id: AGT-TSK-obsidian-task-ids
aliases:
  - AGT-TSK-obsidian-task-ids
---

# Resolve vault-wide Obsidian task IDs

## Problem

Text.
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md",
            """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-cli
aliases:
  - OAW-TSK-cli
tags:
  - projects
---

# Resolver CLI

## Goal

Build it.

## Agent sessions

""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Index.md",
            """---
type: project
id: OAW-index
aliases:
  - OAW-index
---

# Obsidian Agent Workflow
""",
        )
        write(
            self.vault / "Templates/Research packet.md",
            """---
type: research-prompt
project: {{project}}
track: {{track}}
title: {{title}}
created: {{date}}
---

# Prompt - {{title}}

## Running research sessions

## Local packet context

- Project: {{project}}
- Track: {{track}}

## Deep research prompt

```text
Research {{title}} for a reader with no access to local notes or files.

Precise questions:
1. Replace this placeholder with the research questions.

Deliverable: Replace this placeholder with the expected output format.
```
""",
        )
        write(
            self.vault / "Templates/Small project index.md",
            """---
type: project
project: example-project
status: active
repo: /path/to/repo
tags:
  - projects
---

# {{title}}

## Goal

Write the smallest useful description of the project outcome.

## Current state

- Status:
- Repo:
- Next action:

## Shared project workspace

![[Templates/Project workspace.base#Work queue]]

## Agent notes

Start here, then read active task notes before acting.
""",
        )
        write(
            self.vault / "Projects/Codex Delegation/Index.md",
            """---
type: project
id: CDX-index
aliases:
  - CDX-index
---

# Codex Delegation
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md",
            """---
type: task
project: obsidian-agent-workflow
status: archived
id: OAW-TSK-archived
aliases:
  - OAW-TSK-archived
---

# Archived task
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Board.md",
            """---
kanban-plugin: board
type: board
project: obsidian-agent-workflow
id: OAW-board
aliases:
  - OAW-board
---

## Active

## Todo

- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli

## Done

""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Inbox/Active capture.md",
            """---
type: capture
project: obsidian-agent-workflow
status: active
id: OAW-CAP-active
aliases:
  - OAW-CAP-active
---

# Active capture
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Inbox/Archived capture.md",
            """---
type: capture
project: obsidian-agent-workflow
status: archived
id: OAW-CAP-archived
aliases:
  - OAW-CAP-archived
---

# Archived capture
""",
        )

    def teardown_method(self):
        self.tmp.cleanup()

    def run_oaw(self, *args, env=None):
        return run_oaw_in_process([str(arg) for arg in args], self.merged_env(env))

    def run_oaw_subprocess(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(BIN), *args],
            env=self.merged_env(env),
            text=True,
            capture_output=True,
            check=False,
        )

    def merged_env(self, env=None):
        merged = self.env.copy()
        if env:
            merged.update(env)
        return merged

    def run_record_for(self, session_id: str) -> Path:
        for path in (self.vault / "Agents/Runs").glob("*.md"):
            if f'agent_session_id: "{session_id}"' in path.read_text(encoding="utf-8"):
                return path
        raise AssertionError(f"run record not found for {session_id}")

    def test_task_create_defaults_to_backlog_with_derived_id(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        proc = self.run_oaw(
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
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "Created: Projects/Obsidian Agent Workflow/Tasks/Improve resolver errors.md",
            proc.stdout,
        )
        self.assertIn("ID: OAW-TSK-improve-resolver-errors", proc.stdout)
        self.assertIn("Status: backlog", proc.stdout)
        self.assertNotIn("Board:", proc.stdout)
        note = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Improve resolver errors.md"
        ).read_text(encoding="utf-8")
        self.assertIn("type: task", note)
        self.assertIn("project: obsidian-agent-workflow", note)
        self.assertIn("status: backlog", note)
        self.assertIn("preparedness: needs-triage", note)
        self.assertIn("priority: 2", note)
        self.assertIn("effort: M", note)
        self.assertIn('  - "resolver-errors"', note)
        self.assertIn('  - "cli-contract"', note)
        self.assertIn("id: OAW-TSK-improve-resolver-errors", note)
        self.assertIn('session-ids:\n  - "test-thread"', note)
        self.assertIn("Error messages should list candidates.", note)
        self.assertIn("- [[Projects/Obsidian Agent Workflow/Index|OAW-index]]", note)
        self.assertIn("## Agent sessions", note)
        self.assertEqual(board_before, board_path.read_bytes())
        resolved = self.run_oaw("resolve", "--json", "OAW-TSK-improve-resolver-errors")
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        listing = self.run_oaw("list", "--project", "Obsidian Agent Workflow")
        self.assertIn("OAW-TSK-improve-resolver-errors", listing.stdout)

    def test_task_create_accepts_explicit_preparedness(self):
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Prepared task",
            "--preparedness",
            "prepared",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Prepared task.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("status: backlog\npreparedness: prepared\n", note)

    def test_boardless_project_task_lifecycle_never_creates_a_board(self):
        created_project = self.run_oaw(
            "project",
            "create",
            "--name",
            "Boardless Example",
            "--alias",
            "BLE",
            "--goal",
            "Exercise the task lifecycle without a duplicate board surface.",
        )
        self.assertEqual(created_project.returncode, 0, created_project.stderr)
        project_root = self.vault / "Projects/Boardless Example"
        board_path = project_root / "Board.md"
        self.assertFalse(board_path.exists())

        created_task = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:BLE",
            "--title",
            "Boardless lifecycle",
            "--start",
        )
        self.assertEqual(created_task.returncode, 0, created_task.stderr)
        self.assertNotIn("Board:", created_task.stdout)
        self.assertFalse(board_path.exists())

        reviewed = self.run_oaw(
            "task",
            "review",
            "BLE-TSK-boardless-lifecycle",
            "--note",
            "Ready for verification.",
            "--checks",
            "focused lifecycle check",
        )
        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertNotIn("Board:", reviewed.stdout)
        self.assertFalse(board_path.exists())

        restarted = self.run_oaw(
            "task",
            "start",
            "BLE-TSK-boardless-lifecycle",
            "--note",
            "Verification accepted; finishing.",
        )
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        completed = self.run_oaw(
            "task",
            "complete",
            "BLE-TSK-boardless-lifecycle",
            "--note",
            "Lifecycle verified.",
            "--checks",
            "focused lifecycle check",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task = (project_root / "Tasks/Boardless lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("status: done", task)
        self.assertFalse(board_path.exists())

    def test_task_create_writes_timezone_aware_iso8601_created_timestamp(self):
        before = dt.datetime.now(dt.UTC)
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Timestamped task",
        )
        after = dt.datetime.now(dt.UTC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        note = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Timestamped task.md"
        ).read_text(encoding="utf-8")
        created_line = next(line for line in note.splitlines() if line.startswith("created:"))
        created_value = created_line.split(":", 1)[1].strip()
        parsed = dt.datetime.fromisoformat(created_value.replace("Z", "+00:00"))

        self.assertTrue(parsed.tzinfo is not None)
        self.assertTrue(parsed.utcoffset() is not None)
        self.assertTrue(parsed >= before - dt.timedelta(seconds=1))
        self.assertTrue(parsed <= after + dt.timedelta(seconds=1))

    def test_task_create_todo_sets_status_without_touching_legacy_board(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "Obsidian Agent Workflow",
            "--title",
            "Todo task",
            "--status",
            "todo",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ID: OAW-TSK-todo-task", proc.stdout)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Todo task.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("status: todo", task)
        self.assertNotIn("Board:", proc.stdout)
        self.assertEqual(board_before, board_path.read_bytes())

    def test_task_create_start_is_atomic_without_capture(self):
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Atomic started task",
            "--start",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Status: active", proc.stdout)
        self.assertIn("Run: AGT-RUN-OAW-TSK-atomic-started-task", proc.stdout)
        task = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Atomic started task.md"
        ).read_text(encoding="utf-8")
        self.assertIn("status: active", task)
        self.assertIn("execution: agent", task)
        run = self.run_record_for("test-thread").read_text(encoding="utf-8")
        self.assertIn('task_id: "OAW-TSK-atomic-started-task"', run)
        self.assertIn("run_state: running", run)

    def test_task_create_rejects_start_with_human_execution_without_writes(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before = board_path.read_bytes()

        proc = self.run_oaw(
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

        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot --start a task with human execution", proc.stderr)
        self.assertEqual(before, board_path.read_bytes())
        self.assertFalse(
            (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Human task.md").exists()
        )

    def test_task_create_duplicate_id_fails_without_writes(self):
        before_board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text(
            encoding="utf-8"
        )
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Fresh title",
            "--id",
            "OAW-TSK-cli",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("already in use", proc.stderr)
        self.assertFalse(
            (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Fresh title.md").exists()
        )
        after_board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before_board, after_board)

    def test_task_create_existing_path_fails(self):
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Resolver CLI",
            "--id",
            "OAW-TSK-resolver-duplicate",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("task note already exists", proc.stderr)

    def test_task_create_unknown_project_fails(self):
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "No Such Project",
            "--title",
            "Anything",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("project not found", proc.stderr)

    def test_task_create_requires_session_id(self):
        env = {
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        proc = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "No session task",
            env=env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no stable session ID", proc.stderr)
        allowed = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "No session task",
            "--allow-missing-session-id",
            env=env,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        note = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/No session task.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("session-ids:", note)
        self.assertIn("`session_id=unavailable`", note)

    def test_task_create_from_capture_is_atomic_and_preserves_provenance(self):
        capture_path = self.vault / "Projects/Obsidian Agent Workflow/Inbox/Active capture.md"
        original = capture_path.read_text(encoding="utf-8")
        capture_path.write_text(
            original
            + "\n## Outcome\n\nExpected next shape: route the regression into a verified task.\n"
            + "\n## Evidence\n\nRouting-regression investigation details stay here.\n",
            encoding="utf-8",
        )
        proc = self.run_oaw(
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
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Status: todo", proc.stdout)
        self.assertIn("Capture: OAW-CAP-active -> triaged", proc.stdout)
        task_path = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Investigate routing regression.md"
        )
        task = task_path.read_text(encoding="utf-8")
        capture = capture_path.read_text(encoding="utf-8")
        tags = task.split("tags:\n", 1)[1].split("source-capture:", 1)[0]
        self.assertEqual(
            tags,
            '  - "projects"\n'
            '  - "obsidian-agent-workflow"\n'
            '  - "task"\n'
            '  - "capture-routing"\n'
            '  - "cli"\n',
        )
        self.assertIn("source-capture: OAW-CAP-active", task)
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Inbox/Active capture|OAW-CAP-active]]",
            task,
        )
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Investigate routing regression|OAW-TSK-investigate-routing-regression]]",
            capture,
        )
        self.assertIn(
            'destinations:\n  - "[[Projects/Obsidian Agent Workflow/Tasks/Investigate routing regression|OAW-TSK-investigate-routing-regression]]"',
            capture,
        )
        self.assertIn("status: triaged", capture)
        self.assertIn("Expected next shape: route the regression into a verified task.", capture)
        self.assertIn("Routing-regression investigation details stay here.", capture)
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("OAW-TSK-investigate-routing-regression", board)

    def test_task_create_from_capture_start_creates_active_task(self):
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-active",
            "--title",
            "Start capture work",
            "--start",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Status: active", proc.stdout)
        task = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Start capture work.md"
        ).read_text(encoding="utf-8")
        self.assertIn("status: active", task)
        self.assertIn('session-ids:\n  - "test-thread"', task)
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("OAW-TSK-start-capture-work", board)

    def test_task_create_from_capture_start_requires_real_session_provenance(self):
        capture_path = self.vault / "Projects/Obsidian Agent Workflow/Inbox/Active capture.md"
        before = capture_path.read_text(encoding="utf-8")
        env = {
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-active",
            "--title",
            "Start without provenance",
            "--start",
            env=env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no stable session ID", proc.stderr)
        self.assertEqual(before, capture_path.read_text(encoding="utf-8"))
        self.assertFalse(
            (
                self.vault / "Projects/Obsidian Agent Workflow/Tasks/Start without provenance.md"
            ).exists()
        )

    def test_task_create_rejects_conflicting_capture_intents(self):
        proc = self.run_oaw(
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
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not allowed with argument", proc.stderr)

    def test_task_create_from_capture_creation_failure_leaves_capture_unchanged(self):
        capture_path = self.vault / "Projects/Obsidian Agent Workflow/Inbox/Active capture.md"
        before = capture_path.read_text(encoding="utf-8")
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_text(encoding="utf-8")
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-active",
            "--title",
            "Duplicate task",
            "--id",
            "OAW-TSK-cli",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("already in use", proc.stderr)
        self.assertEqual(before, capture_path.read_text(encoding="utf-8"))
        self.assertEqual(board_before, board_path.read_text(encoding="utf-8"))

    def test_task_create_from_capture_link_failure_leaves_capture_unchanged(self):
        capture_path = self.vault / "Projects/Obsidian Agent Workflow/Inbox/Alias capture.md"
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
        before = capture_path.read_text(encoding="utf-8")
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-alias-only",
            "--title",
            "Must not be created",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("stable frontmatter id", proc.stderr)
        self.assertEqual(before, capture_path.read_text(encoding="utf-8"))
        self.assertFalse(
            (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Must not be created.md").exists()
        )

    def _write_canonical_capture(self, note_id: str, project_line: str) -> Path:
        path = self.vault / "Captures/Entries" / f"{note_id}.md"
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

    def test_task_create_from_canonical_capture_metadata(self):
        self._write_canonical_capture("OAW-CAP-canon", "project: obsidian-agent-workflow")
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-canon",
            "--title",
            "Promoted canonical capture",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task_path = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Promoted canonical capture.md"
        )
        self.assertTrue(task_path.exists())
        self.assertIn("Capture: OAW-CAP-canon -> triaged", proc.stdout)
        capture = (self.vault / "Captures/Entries/OAW-CAP-canon.md").read_text(encoding="utf-8")
        self.assertIn("status: triaged", capture)
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Promoted canonical capture"
            "|OAW-TSK-promoted-canonical-capture]]",
            capture,
        )

    def test_task_create_from_capture_project_conflict(self):
        self._write_canonical_capture("OAW-CAP-conflict", "project: obsidian-agent-workflow")
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-conflict",
            "--project",
            "obs:CDX",
            "--title",
            "Wrong project promotion",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("conflicts with the capture's project metadata", proc.stderr)
        self.assertFalse(
            (self.vault / "Projects/Codex Delegation/Tasks/Wrong project promotion.md").exists()
        )
        capture = (self.vault / "Captures/Entries/OAW-CAP-conflict.md").read_text(encoding="utf-8")
        self.assertIn("status: inbox", capture)

    def test_task_create_from_capture_no_metadata_outside_projects(self):
        self._write_canonical_capture("OAW-CAP-orphan", "project:")
        proc = self.run_oaw(
            "task",
            "create",
            "--from-capture",
            "OAW-CAP-orphan",
            "--title",
            "Orphan promotion",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--project", proc.stderr)
