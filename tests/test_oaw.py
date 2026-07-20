import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from oaw import cli, links, resolver
from oaw.errors import OawError

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

    @pytest.mark.parametrize("writer", ["pause", "priority", "preparedness", "relation"])
    def test_remaining_lifecycle_note_writers_materialize_obs_references(self, writer):
        if writer == "pause":
            started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started for pause.")
            self.assertEqual(started.returncode, 0, started.stderr)

        note = "Trace obs:OAW-TSK-archived."
        arguments = {
            "pause": ("task", "pause", "OAW-TSK-cli", "--note", note),
            "priority": (
                "task",
                "priority",
                "OAW-TSK-cli",
                "--priority",
                "2",
                "--note",
                note,
            ),
            "preparedness": (
                "task",
                "preparedness",
                "OAW-TSK-cli",
                "--state",
                "prepared",
                "--note",
                note,
            ),
            "relation": (
                "task",
                "relation",
                "add",
                "OAW-TSK-cli",
                "follows",
                "OAW-TSK-archived",
                "--note",
                note,
            ),
        }

        result = self.run_oaw(*arguments[writer])

        self.assertEqual(result.returncode, 0, result.stderr)
        durable_note = (
            "Trace [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]."
        )
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        self.assertIn(durable_note, task_path.read_text(encoding="utf-8"))
        if writer == "pause":
            self.assertIn(
                durable_note,
                self.run_record_for("test-thread").read_text(encoding="utf-8"),
            )

    @pytest.mark.parametrize("writer", ["pause", "priority", "preparedness", "relation"])
    def test_remaining_lifecycle_note_materialization_fails_before_any_write(self, writer):
        if writer == "pause":
            started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started for pause.")
            self.assertEqual(started.returncode, 0, started.stderr)

        note = "Trace obs:OAW-TSK-does-not-exist."
        arguments = {
            "pause": ("task", "pause", "OAW-TSK-cli", "--note", note),
            "priority": (
                "task",
                "priority",
                "OAW-TSK-cli",
                "--priority",
                "2",
                "--note",
                note,
            ),
            "preparedness": (
                "task",
                "preparedness",
                "OAW-TSK-cli",
                "--state",
                "prepared",
                "--note",
                note,
            ),
            "relation": (
                "task",
                "relation",
                "add",
                "OAW-TSK-cli",
                "follows",
                "OAW-TSK-archived",
                "--note",
                note,
            ),
        }
        before = snapshot_tree_without_following_symlinks(self.vault)

        result = self.run_oaw(*arguments[writer])

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "no note with frontmatter id or alias 'OAW-TSK-does-not-exist'",
            result.stderr,
        )
        self.assertEqual(before, snapshot_tree_without_following_symlinks(self.vault))

    def test_list_tasks_preserves_archived_rows(self):
        proc = self.run_oaw("list", "--project", "Obsidian Agent Workflow")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OAW-TSK-cli", proc.stdout)
        self.assertIn("OAW-TSK-archived", proc.stdout)

    def _seed_ranked_tasks(self):
        tasks = self.vault / "Projects/Ranking/Tasks"
        write(
            tasks / "High.md",
            """---
type: task
project: ranking
status: todo
priority: 1
effort: M
id: RNK-TSK-high
---

# High leverage task

## Problem

High priority work that must ship the ranked view first.
""",
        )
        write(
            tasks / "Mid.md",
            """---
type: task
project: ranking
status: todo
priority: 2
effort: S
id: RNK-TSK-mid
---

# Mid priority task

## Problem

Normal next-session work with clear value.
""",
        )
        write(
            tasks / "Untriaged.md",
            """---
type: task
project: ranking
status: todo
id: RNK-TSK-untriaged
---

# Untriaged task

## Problem

Work that has no priority or effort assigned yet.
""",
        )

    def test_list_default_output_unchanged_by_new_flags(self):
        proc = self.run_oaw("list", "--project", "Obsidian Agent Workflow")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for line in proc.stdout.splitlines():
            self.assertEqual(len(line.split("\t")), 4, line)
        self.assertIn(
            "OAW-TSK-cli\ttodo\tResolver CLI\t"
            "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md",
            proc.stdout,
        )

    def test_list_sort_priority_orders_by_rank_then_effort_then_title(self):
        self._seed_ranked_tasks()
        proc = self.run_oaw(
            "list",
            "--project",
            "Ranking",
            "--sort",
            "priority",
            "--fields",
            "id",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout.splitlines(),
            ["RNK-TSK-high", "RNK-TSK-mid", "RNK-TSK-untriaged"],
        )

    def test_list_sort_priority_tie_breaks_on_effort_and_title(self):
        tasks = self.vault / "Projects/Ties/Tasks"
        write(
            tasks / "A.md",
            "---\ntype: task\nstatus: todo\npriority: 1\neffort: L\n"
            "id: TIE-TSK-a\n---\n\n# Aardvark\n",
        )
        write(
            tasks / "B.md",
            "---\ntype: task\nstatus: todo\npriority: 1\neffort: S\n"
            "id: TIE-TSK-b\n---\n\n# Zebra\n",
        )
        write(
            tasks / "C.md",
            "---\ntype: task\nstatus: todo\npriority: 1\neffort: S\n"
            "id: TIE-TSK-c\n---\n\n# Antelope\n",
        )
        proc = self.run_oaw("list", "--project", "Ties", "--sort", "priority", "--fields", "id")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # effort S before L; within equal effort, title Antelope before Zebra.
        self.assertEqual(
            proc.stdout.splitlines(),
            ["TIE-TSK-c", "TIE-TSK-b", "TIE-TSK-a"],
        )

    def test_list_field_projection_adds_frontmatter_columns(self):
        self._seed_ranked_tasks()
        proc = self.run_oaw(
            "list",
            "--project",
            "Ranking",
            "--sort",
            "priority",
            "--fields",
            "id,priority,effort,title",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], "RNK-TSK-high\t1\tM\tHigh leverage task")
        # Missing priority/effort project as empty columns and sort last.
        self.assertEqual(lines[-1], "RNK-TSK-untriaged\t\t\tUntriaged task")

    def test_list_unknown_field_errors_clearly(self):
        self._seed_ranked_tasks()
        proc = self.run_oaw("list", "--project", "Ranking", "--fields", "id,bogus")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("unknown list field: bogus", proc.stderr)

    def test_list_goal_column_snippets_problem_section(self):
        self._seed_ranked_tasks()
        proc = self.run_oaw("list", "--project", "Ranking", "--fields", "id", "--goal")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "RNK-TSK-high\tHigh priority work that must ship the ranked view first.",
            proc.stdout,
        )

    def test_list_json_emits_sorted_projected_records(self):
        self._seed_ranked_tasks()
        proc = self.run_oaw(
            "list",
            "--project",
            "Ranking",
            "--sort",
            "priority",
            "--fields",
            "id,priority,goal",
            "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(
            [row["id"] for row in payload], ["RNK-TSK-high", "RNK-TSK-mid", "RNK-TSK-untriaged"]
        )
        self.assertEqual(payload[0]["priority"], "1")
        self.assertEqual(payload[-1]["priority"], "")
        self.assertEqual(
            payload[0]["goal"], "High priority work that must ship the ranked view first."
        )

    def test_list_invalid_sort_choice_is_usage_error(self):
        proc = self.run_oaw("list", "--project", "Obsidian Agent Workflow", "--sort", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage: oaw list", proc.stderr)
        self.assertIn("invalid choice: 'nope'", proc.stderr)

    def test_list_accepts_project_aliases(self):
        expected = self.run_oaw("list", "--project", "Obsidian Agent Workflow")
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for alias in ["OAW", "obs:OAW"]:
            with self.subTest(alias=alias):
                proc = self.run_oaw("list", "--project", alias)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, expected.stdout)

    def test_list_prefers_exact_project_folder_over_bare_alias(self):
        task = self.vault / "Projects/OAW/Tasks/Exact folder task.md"
        write(
            task,
            "---\nid: EXACT-TSK-folder\nstatus: todo\ntype: task\n---\n\n# Exact folder task\n",
        )

        exact = self.run_oaw("list", "--project", "OAW", "--fields", "id")
        explicit_alias = self.run_oaw("list", "--project", "obs:OAW", "--fields", "id")

        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(exact.stdout.splitlines(), ["EXACT-TSK-folder"])
        self.assertEqual(explicit_alias.returncode, 0, explicit_alias.stderr)
        self.assertIn("OAW-TSK-cli", explicit_alias.stdout.splitlines())
        self.assertNotIn("EXACT-TSK-folder", explicit_alias.stdout.splitlines())

    def test_list_accepts_project_folder_without_index_note(self):
        task = self.vault / "Projects/No Index/Tasks/Loose task.md"
        write(
            task,
            "---\nid: NOIDX-TSK-loose\nstatus: todo\ntype: task\n---\n\n# Loose task\n",
        )

        proc = self.run_oaw("list", "--project", "No Index")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NOIDX-TSK-loose", proc.stdout)

    def test_list_rejects_unknown_project_alias(self):
        proc = self.run_oaw("list", "--project", "obs:BOGUS")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("project not found: obs:BOGUS", proc.stderr)

    def test_lifecycle_supports_agents_task_without_board_output(self):
        proc = self.run_oaw(
            "task",
            "start",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Should fail.",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md").read_text()
        self.assertIn("status: active", note)
        self.assertIn("execution: agent", note)
        self.assertNotIn("Board:", proc.stdout)

    def test_note_session_appends_agent_session_to_non_project_note(self):
        proc = self.run_oaw(
            "note",
            "session",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Reviewed resolver policy.",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md").read_text()
        self.assertIn("## Agent sessions", note)
        self.assertIn("CODEX_THREAD_ID=test-thread", note)
        self.assertIn('session-ids:\n  - "test-thread"\n', note)
        self.assertIn("Reviewed resolver policy.", note)
        self.assertIn("Updated: Agents/Tasks/Resolve vault-wide Obsidian task IDs.md", proc.stdout)

    def test_note_session_leaves_blank_line_before_following_heading(self):
        path = self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
        write(
            path,
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

## Agent sessions

- 2026-07-13 - Claude Code - `CLAUDE_CODE_SESSION_ID=old-thread` - Existing entry.

## Decisions

Keep this decision.
""",
        )

        proc = self.run_oaw(
            "note",
            "session",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Reviewed resolver policy.",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = path.read_text(encoding="utf-8")
        self.assertIn("Reviewed resolver policy.", note)
        after_entry = note.split("Reviewed resolver policy.", 1)[1]
        before_heading = after_entry.split("## Decisions", 1)[0]
        self.assertEqual(before_heading, "\n\n")

    def test_note_session_refuses_unsupported_session_ids_without_writing(self):
        path = self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
        baseline = path.read_text(encoding="utf-8")
        for session_ids in (
            'session-ids: ["old,with-comma", earlier-thread]\n',
            "session-ids:\n  owner: earlier-thread\n",
            "session-ids:\n  - null\n",
        ):
            with self.subTest(session_ids=session_ids):
                before = baseline.replace("status: open\n", "status: open\n" + session_ids)
                path.write_text(before, encoding="utf-8")

                proc = self.run_oaw(
                    "note",
                    "session",
                    "AGT-TSK-obsidian-task-ids",
                    "--note",
                    "Must not corrupt session metadata.",
                )

                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("session-ids must", proc.stderr)
                self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_note_observe_appends_block_under_target_section(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Research/Evidence.md",
            """---
type: research
id: OAW-RES-evidence
aliases:
  - OAW-RES-evidence
---

# Evidence

## Observations

### Existing

Keep.

## Decisions

Later.
""",
        )
        proc = self.run_oaw(
            "note",
            "observe",
            "OAW-RES-evidence",
            "--title",
            "Lint gap",
            "--body",
            "Provider-visible text needs a mechanical check.",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Projects/Obsidian Agent Workflow/Research/Evidence.md").read_text()
        self.assertRegex(note, r"### \d{4}-\d{2}-\d{2} - Lint gap")
        self.assertIn("Provider-visible text needs a mechanical check.", note)
        self.assertLess(note.index("Lint gap"), note.index("## Decisions"))

    def test_note_observe_ignores_headings_inside_fenced_code(self):
        path = self.vault / "Projects/Obsidian Agent Workflow/Research/Fenced.md"
        write(
            path,
            """---
type: research
id: OAW-RES-fenced
---

# Fenced

## Observations

```bash
# run the tests
python -m unittest
```

Keep this conclusion.

## Decisions

Later.
""",
        )

        proc = self.run_oaw(
            "note",
            "observe",
            "OAW-RES-fenced",
            "--title",
            "Fence-safe append",
            "--body",
            "This block belongs after the fence.",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = path.read_text(encoding="utf-8")
        self.assertGreater(note.index("Fence-safe append"), note.index("Keep this conclusion."))
        self.assertLess(note.index("Fence-safe append"), note.index("## Decisions"))

    def test_retro_create_writes_dated_template(self):
        proc = self.run_oaw(
            "retro",
            "create",
            "--title",
            "Resolver dogfood",
            "--summary",
            "Captured the resolver workflow and follow-ups.",
            "--date",
            "2026-07-09",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.vault / "Agents/Retrospectives/2026-07-09 resolver dogfood.md"
        self.assertTrue(path.exists())
        note = path.read_text(encoding="utf-8")
        self.assertIn("type: retrospective", note)
        self.assertIn("status: draft", note)
        self.assertIn("id: AGT-RETRO-2026-07-09-resolver-dogfood", note)
        self.assertIn("session-ids:", note)
        self.assertIn("  - test-thread", note)
        self.assertIn("# 2026-07-09 - Resolver dogfood", note)
        self.assertIn("Captured the resolver workflow and follow-ups.", note)
        self.assertIn("Created: Agents/Retrospectives/2026-07-09 resolver dogfood.md", proc.stdout)

    def test_retro_create_rejects_duplicate_id(self):
        proc = self.run_oaw(
            "retro",
            "create",
            "--title",
            "Duplicate ID",
            "--date",
            "2026-07-09",
            "--id",
            "AGT-TSK-obsidian-task-ids",
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("id 'AGT-TSK-obsidian-task-ids' is already in use", proc.stderr)
        self.assertFalse((self.vault / "Agents/Retrospectives/2026-07-09 duplicate id.md").exists())

    def test_retro_create_rejects_whitespace_only_id(self):
        proc = self.run_oaw(
            "retro",
            "create",
            "--title",
            "Whitespace ID",
            "--date",
            "2026-07-09",
            "--id",
            "   ",
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires a non-empty --id", proc.stderr)

    def test_retro_create_normalizes_accented_title_slug(self):
        proc = self.run_oaw_subprocess(
            "retro",
            "create",
            "--title",
            "Révision générale",
            "--date",
            "2026-07-09",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.vault / "Agents/Retrospectives/2026-07-09 revision generale.md"
        self.assertTrue(path.exists())
        note = path.read_text(encoding="utf-8")
        self.assertIn("id: AGT-RETRO-2026-07-09-revision-generale", note)

    def test_export_note_requires_safe_marker(self):
        proc = self.run_oaw(
            "export",
            "note",
            "OAW-TSK-cli",
            "--output-root",
            str(self.vault / "exports"),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("export-scope: work", proc.stderr)

    def test_export_note_writes_bundle_manifest_and_artifacts(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Work export.md",
            """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-work-export
aliases:
  - OAW-TSK-work-export
export-scope: work
return_ingest: true
export_artifacts:
  - scripts/run.sh
---

# Work export

Run this at work.
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/scripts/run.sh",
            "#!/bin/sh\necho work\n",
        )
        output_root = self.vault / "exports"
        proc = self.run_oaw(
            "export",
            "note",
            "OAW-TSK-work-export",
            "--output-root",
            str(output_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bundle = output_root / "OAW-TSK-work-export"
        manifest_path = bundle / "manifest.json"
        note_path = bundle / "note.md"
        artifact_path = bundle / "artifacts/Projects/Obsidian Agent Workflow/Tasks/scripts/run.sh"
        self.assertTrue(manifest_path.exists())
        self.assertTrue(note_path.exists())
        self.assertTrue(artifact_path.exists())
        self.assertIn("intentionally exported", note_path.read_text(encoding="utf-8"))

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "oaw-safe-export-v1")
        self.assertEqual(manifest["target"], "work")
        self.assertEqual(manifest["source"]["id"], "OAW-TSK-work-export")
        self.assertEqual(
            manifest["source"]["path"],
            "Projects/Obsidian Agent Workflow/Tasks/Work export.md",
        )
        self.assertEqual(
            manifest["artifacts"][0]["path"], artifact_path.relative_to(bundle).as_posix()
        )

        valid = self.run_oaw("export", "validate", str(bundle))
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("Export: valid", valid.stdout)

    def test_export_validate_rejects_tampered_marker(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Work export.md",
            """---
type: task
id: OAW-TSK-work-export
aliases:
  - OAW-TSK-work-export
export-scope: work
---

# Work export
""",
        )
        output_root = self.vault / "exports"
        proc = self.run_oaw(
            "export",
            "note",
            "OAW-TSK-work-export",
            "--output-root",
            str(output_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bundle = output_root / "OAW-TSK-work-export"
        note_path = bundle / "note.md"
        note_path.write_text(
            note_path.read_text(encoding="utf-8").replace(
                "export-scope: work",
                "export-scope: personal",
            ),
            encoding="utf-8",
        )
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["note"]["sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        proc = self.run_oaw("export", "validate", str(bundle))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("export-scope: work", proc.stderr)

    def test_export_note_failure_leaves_no_partial_bundle_and_retry_succeeds(self):
        note = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Retry export.md"
        artifact = note.parent / "missing.txt"
        write(
            note,
            """---
type: task
id: OAW-TSK-retry-export
export-scope: work
export_artifacts:
  - missing.txt
---

# Retry export
""",
        )
        output_root = self.vault / "exports"

        failed = self.run_oaw_subprocess(
            "export",
            "note",
            "OAW-TSK-retry-export",
            "--output-root",
            str(output_root),
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((output_root / "OAW-TSK-retry-export").exists())
        self.assertEqual(list(output_root.glob(".OAW-TSK-retry-export.tmp-*")), [])

        write(artifact, "ready\n")
        retried = self.run_oaw_subprocess(
            "export",
            "note",
            "OAW-TSK-retry-export",
            "--output-root",
            str(output_root),
        )
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertTrue((output_root / "OAW-TSK-retry-export/manifest.json").exists())

    def test_export_note_sanitizes_bundle_name_from_id(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Escape export.md",
            """---
type: task
id: ../escape
export-scope: work
---

# Escape export
""",
        )
        output_root = self.vault / "exports"

        proc = self.run_oaw(
            "export",
            "note",
            "../escape",
            "--output-root",
            str(output_root),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((output_root / "escape/manifest.json").exists())
        self.assertFalse((self.vault / "escape").exists())

    def test_export_validate_rejects_paths_outside_bundle(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Path export.md",
            """---
type: task
id: OAW-TSK-path-export
export-scope: work
---

# Path export
""",
        )
        output_root = self.vault / "exports"
        exported = self.run_oaw(
            "export",
            "note",
            "OAW-TSK-path-export",
            "--output-root",
            str(output_root),
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        bundle = output_root / "OAW-TSK-path-export"
        outside = output_root / "stolen.md"
        outside.write_text((bundle / "note.md").read_text(encoding="utf-8"), encoding="utf-8")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["note"]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()

        for escaped_path in ("../stolen.md", str(outside)):
            with self.subTest(path=escaped_path):
                manifest["note"]["path"] = escaped_path
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                proc = self.run_oaw("export", "validate", str(bundle))
                self.assertNotEqual(proc.returncode, 0)
                self.assertRegex(
                    proc.stderr, r"manifest path (escapes bundle|must be bundle-relative)"
                )

    def test_list_capture_hides_archived_by_default(self):
        proc = self.run_oaw(
            "list",
            "--project",
            "Obsidian Agent Workflow",
            "--type",
            "capture",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OAW-CAP-active", proc.stdout)
        self.assertNotIn("OAW-CAP-archived", proc.stdout)

    def test_list_capture_can_include_or_select_archived(self):
        proc = self.run_oaw(
            "list",
            "--project",
            "Obsidian Agent Workflow",
            "--type",
            "capture",
            "--include-archived",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OAW-CAP-active", proc.stdout)
        self.assertIn("OAW-CAP-archived", proc.stdout)

        archived = self.run_oaw(
            "list",
            "--project",
            "Obsidian Agent Workflow",
            "--type",
            "capture",
            "--status",
            "archived",
        )
        self.assertEqual(archived.returncode, 0, archived.stderr)
        self.assertNotIn("OAW-CAP-active", archived.stdout)
        self.assertIn("OAW-CAP-archived", archived.stdout)

    def test_safe_export_ingest_dry_run_reads_markers_and_leaves_files(self):
        ingestion = self.vault / "handoff"
        safe = ingestion / "safe.md"
        legacy = ingestion / "legacy.md"
        unsafe = ingestion / "unsafe.md"
        write(
            safe,
            """---
export-scope: personal
---

# Safe

Body.
""",
        )
        write(
            legacy,
            """---
tags:
  - safe-export-personal
---

# Legacy
""",
        )
        write(
            unsafe,
            """---
project: private
---

# Unsafe
""",
        )

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--destination",
            "Imports/Handoff",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Mode: dry-run", proc.stdout)
        self.assertIn(
            "ACCEPT safe.md [export-scope: personal] -> Imports/Handoff/safe.md; dry-run",
            proc.stdout,
        )
        self.assertIn(
            "ACCEPT legacy.md [tag: safe-export-personal] -> Imports/Handoff/legacy.md; dry-run",
            proc.stdout,
        )
        self.assertIn(
            "REJECT unsafe.md [missing safe export marker] -> quarantine; dry-run", proc.stdout
        )
        self.assertTrue(safe.exists())
        self.assertTrue(legacy.exists())
        self.assertTrue(unsafe.exists())
        self.assertFalse((self.vault / "Imports/Handoff/safe.md").exists())
        self.assertFalse((ingestion / ".rejected/unsafe.md").exists())

    def test_safe_export_ingest_write_ingests_safe_and_quarantines_rejected(self):
        ingestion = self.vault / "handoff"
        safe = ingestion / "nested/safe.md"
        unsafe = ingestion / "unsafe.md"
        existing = self.vault / "Imports/Handoff/nested/safe.md"
        write(
            safe,
            """---
export-approved: personal
---

# Safe
""",
        )
        write(
            unsafe,
            """---
export-scope: work
---

# Unsafe
""",
        )
        write(existing, "existing\n")

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--destination",
            "Imports/Handoff",
            "--write",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "ACCEPT nested/safe.md [export-approved: personal] -> Imports/Handoff/nested/safe-2.md; removed source",
            proc.stdout,
        )
        self.assertIn(
            "REJECT unsafe.md [missing safe export marker] -> quarantine .rejected/unsafe.md",
            proc.stdout,
        )
        self.assertFalse(safe.exists())
        self.assertFalse(unsafe.exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing\n")
        self.assertTrue((self.vault / "Imports/Handoff/nested/safe-2.md").exists())
        self.assertTrue((ingestion / ".rejected/unsafe.md").exists())

    def test_safe_export_ingest_rejects_unclosed_frontmatter(self):
        ingestion = self.vault / "handoff"
        broken = ingestion / "broken.md"
        write(
            broken,
            """---
export-scope: personal
# no closing fence
Body that should not be trusted.
""",
        )

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("REJECT broken.md [frontmatter is not closed:", proc.stdout)

    def test_safe_export_ingest_refuses_absolute_destination(self):
        ingestion = self.vault / "handoff"
        write(
            ingestion / "safe.md",
            """---
export-scope: personal
---

# Safe
""",
        )

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--destination",
            str(self.vault / "absolute"),
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--destination must be vault-relative", proc.stderr)

    def test_safe_export_ingest_refuses_conflicting_modes(self):
        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--dry-run",
            "--write",
        )

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("not allowed with argument", proc.stderr)

    def test_safe_export_ingest_refuses_root_that_contains_vault(self):
        ingestion = self.vault / "misconfigured"
        nested_vault = ingestion / "vault"
        note = nested_vault / "Projects/Demo/Tasks/Unsafe.md"
        write(note, "---\nid: DEMO-TSK-unsafe\n---\n\n# Unsafe\n")

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--write",
            env={"OAW_VAULT": str(nested_vault)},
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ingestion root must not be or contain the vault", proc.stderr)
        self.assertTrue(note.exists())

    def test_safe_export_ingest_refuses_destination_inside_ingestion_root(self):
        ingestion = self.vault / "handoff"
        write(
            ingestion / "safe.md",
            "---\nexport-scope: personal\n---\n\n# Safe\n",
        )

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--destination",
            "handoff/imported",
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("destination must not be inside the ingestion root", proc.stderr)

    def test_safe_export_ingest_dry_run_previews_collision_destination(self):
        ingestion = self.vault / "handoff"
        write(
            ingestion / "safe.md",
            "---\nexport-scope: personal\n---\n\n# Safe\n",
        )
        write(self.vault / "Imports/Handoff/safe.md", "existing\n")

        proc = self.run_oaw(
            "ingest",
            "safe-export",
            "--ingestion-root",
            str(ingestion),
            "--destination",
            "Imports/Handoff",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("-> Imports/Handoff/safe-2.md; dry-run", proc.stdout)

    def test_session_lookup_reports_vault_note_hit(self):
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task.write_text(
            task.read_text(encoding="utf-8")
            + "- 2026-07-09 - Codex - `CODEX_THREAD_ID=lookup-thread` - Logged.\n",
            encoding="utf-8",
        )

        proc = self.run_oaw(
            "session",
            "lookup",
            "  lookup-thread  ",
            "--codex-root",
            str(self.vault / "missing-codex"),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Session: lookup-thread", proc.stdout)
        self.assertIn("Vault matches:", proc.stdout)
        self.assertIn(
            "- Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli",
            proc.stdout,
        )
        self.assertNotIn("Harness artifacts:", proc.stdout)

    def test_session_lookup_reports_duplicate_note_ids_without_failing(self):
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task.write_text(
            task.read_text(encoding="utf-8") + "\nlookup-duplicate-session\n",
            encoding="utf-8",
        )
        write(
            self.vault / "Projects/Other/Tasks/Duplicate CLI.md",
            """---
type: task
id: OAW-TSK-cli
---

# Duplicate CLI

lookup-duplicate-session
""",
        )

        proc = self.run_oaw(
            "session",
            "lookup",
            "lookup-duplicate-session",
            "--codex-root",
            str(self.vault / "missing-codex"),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md", proc.stdout)
        self.assertIn("Projects/Other/Tasks/Duplicate CLI.md", proc.stdout)
        self.assertEqual(proc.stdout.count("id: OAW-TSK-cli"), 2)

    def test_session_lookup_summarizes_harness_artifacts(self):
        session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
        codex_root = self.vault / "harness/codex/sessions"
        claude_root = self.vault / "harness/claude/projects"
        rollout = codex_root / "2026/07/09" / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
        subagent = (
            claude_root / "-tmp-project" / "parent-session/subagents" / f"agent-{session_id}.jsonl"
        )
        write(
            rollout,
            '{"type":"session_meta","cwd":"/workspace/example"}\n'
            '{"type":"response_item","payload":{"type":"message","role":"user",'
            '"content":[{"type":"input_text","text":"# AGENTS.md instructions for /repo"}]}}\n'
            '{"type":"response_item","payload":{"type":"message","role":"user",'
            '"content":[{"type":"input_text","text":"Find the owning note."}]}}\n'
            '{"type":"tool_output","content":"Read Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"}\n',
        )
        write(
            parent,
            '{"message":{"role":"user","content":"Parent transcript request."}}\n',
        )
        write(subagent, '{"content":"subagent output"}\n')

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(claude_root),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Harness artifacts:", proc.stdout)
        self.assertIn(f"- codex-rollout: {rollout}", proc.stdout)
        self.assertIn(f"- claude-transcript: {parent}", proc.stdout)
        self.assertIn(f"- claude-subagent: {subagent}", proc.stdout)
        self.assertIn("cwd: /workspace/example", proc.stdout)
        self.assertIn("first user: Find the owning note.", proc.stdout)
        self.assertIn(
            "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md",
            proc.stdout,
        )

    def test_session_lookup_verbose_reports_codex_metrics(self):
        session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
        codex_root = self.vault / "harness/codex/sessions"
        rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", rollout)

        default_proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )
        verbose_proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--verbose",
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(default_proc.returncode, 0, default_proc.stderr)
        self.assertNotIn("Started:", default_proc.stdout)
        self.assertNotIn("Turns:", default_proc.stdout)
        self.assertNotIn("Tokens:", default_proc.stdout)
        self.assertEqual(verbose_proc.returncode, 0, verbose_proc.stderr)
        self.assertIn("Started: 2026-07-09T12:00:00Z", verbose_proc.stdout)
        self.assertIn("Ended: 2026-07-09T12:02:05Z", verbose_proc.stdout)
        self.assertIn("Duration: 00:02:05", verbose_proc.stdout)
        self.assertIn("Turns: user=2, assistant=2", verbose_proc.stdout)
        self.assertIn(
            "Tokens: input=250, output=80, cached=75, total=330",
            verbose_proc.stdout,
        )

    def test_session_lookup_verbose_reports_vault_and_codex_matches(self):
        session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task.write_text(
            task.read_text(encoding="utf-8").replace(
                "---\n\n# Resolver CLI",
                f"session-ids:\n  - {session_id}\n---\n\n# Resolver CLI",
            ),
            encoding="utf-8",
        )
        codex_root = self.vault / "harness/codex/sessions"
        rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", rollout)

        default_proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )
        verbose_proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--verbose",
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(default_proc.returncode, 0, default_proc.stderr)
        self.assertIn("Vault matches:", default_proc.stdout)
        self.assertNotIn("Harness artifacts:", default_proc.stdout)
        self.assertNotIn("Started:", default_proc.stdout)
        self.assertEqual(verbose_proc.returncode, 0, verbose_proc.stderr)
        self.assertIn("Vault matches:", verbose_proc.stdout)
        self.assertIn(
            "- Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli",
            verbose_proc.stdout,
        )
        self.assertIn("Harness artifacts:", verbose_proc.stdout)
        self.assertIn(f"- codex-rollout: {rollout}", verbose_proc.stdout)
        self.assertIn("Started: 2026-07-09T12:00:00Z", verbose_proc.stdout)
        self.assertIn("Tokens: input=250, output=80, cached=75, total=330", verbose_proc.stdout)

    def test_session_lookup_verbose_marks_missing_and_unsupported_metrics_unavailable(self):
        session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
        codex_root = self.vault / "harness/codex/sessions"
        claude_root = self.vault / "harness/claude/projects"
        rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        rollout.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "session_lookup/codex-missing.jsonl", rollout)
        write(claude_root / "project" / f"{session_id}.jsonl", "{}\n")

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--verbose",
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(claude_root),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("Started: unavailable"), 2)
        self.assertEqual(proc.stdout.count("Ended: unavailable"), 2)
        self.assertEqual(proc.stdout.count("Duration: unavailable"), 2)
        self.assertEqual(proc.stdout.count("Turns: user=unavailable, assistant=unavailable"), 2)
        self.assertEqual(
            proc.stdout.count(
                "Tokens: input=unavailable, output=unavailable, cached=unavailable, total=unavailable"
            ),
            2,
        )

    def test_session_lookup_unknown_exits_successfully(self):
        proc = self.run_oaw(
            "session",
            "lookup",
            "not-logged-session",
            "--codex-root",
            str(self.vault / "missing-codex"),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Session: not-logged-session", proc.stdout)
        self.assertIn("Status: not logged", proc.stdout)

    def test_session_lookup_treats_glob_metacharacters_literally(self):
        session_id = "abc[1]"
        codex_root = self.vault / "harness/codex/sessions"
        rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        write(rollout, '{"type":"session_meta","cwd":"/workspace/example"}\n')

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"- codex-rollout: {rollout}", proc.stdout)

    def test_session_lookup_default_finds_archived_codex_rollout(self):
        session_id = "019f5001-0000-7111-8222-b15aa4c27782"
        codex_home = self.vault / "harness/codex"
        archived = (
            codex_home / "archived_sessions" / f"rollout-2026-07-11T10-00-00-{session_id}.jsonl"
        )
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", archived)

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--claude-root",
            str(self.vault / "missing-claude"),
            env={"CODEX_HOME": str(codex_home)},
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"- codex-rollout: {archived}", proc.stdout)

        explicit_override = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_home / "sessions"),
            "--claude-root",
            str(self.vault / "missing-claude"),
            env={"CODEX_HOME": str(codex_home)},
        )
        env_override = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--claude-root",
            str(self.vault / "missing-claude"),
            env={
                "CODEX_HOME": str(codex_home),
                "OAW_CODEX_SESSIONS_ROOT": str(codex_home / "sessions"),
            },
        )
        for overridden in (explicit_override, env_override):
            self.assertEqual(overridden.returncode, 0, overridden.stderr)
            self.assertIn("Status: not logged", overridden.stdout)
            self.assertNotIn(str(archived), overridden.stdout)

    def test_session_lookup_prefers_active_duplicate_rollout(self):
        session_id = "019f5004-3333-7444-8555-e48cc7f6bb15"
        codex_home = self.vault / "harness/codex"
        filename = f"rollout-2026-07-11T11-00-00-{session_id}.jsonl"
        active = codex_home / "sessions/2026/07/11" / filename
        archived = codex_home / "archived_sessions" / filename
        write(active, '{"type":"session_meta","cwd":"/active"}\n')
        write(archived, '{"type":"session_meta","cwd":"/archived"}\n')

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--claude-root",
            str(self.vault / "missing-claude"),
            env={"CODEX_HOME": str(codex_home)},
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"- codex-rollout: {active}", proc.stdout)
        self.assertIn("cwd: /active", proc.stdout)
        self.assertNotIn(str(archived), proc.stdout)
        self.assertEqual(proc.stdout.count("- codex-rollout:"), 1)

    def test_session_lookup_keeps_duplicate_rollouts_within_one_root(self):
        session_id = "019f5005-4444-7555-8666-f59dd806cc26"
        codex_root = self.vault / "harness/codex/sessions"
        filename = f"rollout-2026-07-11T12-00-00-{session_id}.jsonl"
        first = codex_root / "2026/07/11" / filename
        second = codex_root / "restored" / filename
        write(first, '{"type":"session_meta","cwd":"/first"}\n')
        write(second, '{"type":"session_meta","cwd":"/second"}\n')

        proc = self.run_oaw(
            "session",
            "lookup",
            session_id,
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"- codex-rollout: {first}", proc.stdout)
        self.assertIn(f"- codex-rollout: {second}", proc.stdout)
        self.assertEqual(proc.stdout.count("- codex-rollout:"), 2)

    def test_link_check_and_list_handle_escaped_pipe_in_table(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Linked task.md",
            """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-linked
aliases:
  - OAW-TSK-linked
---

# Linked task

| Related |
| --- |
| [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|CLI]] |
""",
        )

        check = self.run_oaw("link", "check", "OAW-TSK-linked", "OAW-TSK-cli")
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("Left links right: yes", check.stdout)
        self.assertIn("Right links left: no", check.stdout)

        listed = self.run_oaw("link", "list", "OAW-TSK-linked")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|CLI]]",
            listed.stdout,
        )
        self.assertIn(
            "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli",
            listed.stdout,
        )
        self.assertIn("alias: CLI", listed.stdout)

    def test_link_ensure_dry_run_and_write_append_only(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"

        dry = self.run_oaw(
            "link",
            "ensure",
            "OAW-TSK-cli",
            "OAW-TSK-archived",
            "--section",
            "Related",
            "--label",
            "OAW-TSK-archived",
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("Dry-run: would update", dry.stdout)
        self.assertNotIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]",
            task_path.read_text(encoding="utf-8"),
        )

        written = self.run_oaw(
            "link",
            "ensure",
            "OAW-TSK-cli",
            "OAW-TSK-archived",
            "--section",
            "Related",
            "--label",
            "OAW-TSK-archived",
            "--write",
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertIn(
            "Updated: Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md", written.stdout
        )
        task = task_path.read_text(encoding="utf-8")
        self.assertIn("## Related", task)
        self.assertEqual(
            task.count("[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"),
            1,
        )

        again = self.run_oaw(
            "link",
            "ensure",
            "OAW-TSK-cli",
            "OAW-TSK-archived",
            "--section",
            "Related",
            "--label",
            "different alias",
            "--write",
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("Link: present", again.stdout)
        self.assertEqual(
            task_path.read_text(encoding="utf-8").count(
                "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"
            ),
            1,
        )

    def test_link_materialize_previews_writes_and_is_idempotent(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        original = task_path.read_text(encoding="utf-8").replace(
            "tags:\n", "materialize-example: obs:DOES-NOT-EXIST\ntags:\n"
        )
        source = (
            original
            + """
## Materialization examples

See obs:OAW-TSK-cli, obs:OAW-TSK-archived! Keep \\obs:OAW-TSK-cli literal.
[[OAW-TSK-cli|existing obs:OAW-TSK-archived]] and `obs:OAW-TSK-cli` stay literal.
[obs:OAW-TSK-cli](https://example.test/obs:OAW-TSK-cli)
![[Existing embed|obs:OAW-TSK-cli]] and ![obs:OAW-TSK-cli](image.png).
[obs:OAW-TSK-cli][reference] and <https://example.test/obs:OAW-TSK-cli>.
[reference]: https://example.test/obs:OAW-TSK-cli
Keep prefixobs:OAW-TSK-cli, /obs:OAW-TSK-cli, obs:OAW-TSK-cli/path, and obs:OAW-TSK-cli.md.
Bare OAW-TSK-cli stays bare; project obs:OAW resolves normally.
| Reference |
| obs:OAW-TSK-cli |
```text
obs:OAW-TSK-cli
```
"""
        )
        task_path.write_text(source, encoding="utf-8")

        preview = self.run_oaw("link", "materialize", "OAW-TSK-cli")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn(
            "obs:OAW-TSK-cli -> [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]",
            preview.stdout,
        )
        self.assertIn("Dry-run: would update", preview.stdout)
        self.assertEqual(task_path.read_text(encoding="utf-8"), source)

        written = self.run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
        self.assertEqual(written.returncode, 0, written.stderr)
        materialized = task_path.read_text(encoding="utf-8")
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]], "
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]!",
            materialized,
        )
        self.assertIn("\\obs:OAW-TSK-cli literal", materialized)
        self.assertIn("[[OAW-TSK-cli|existing obs:OAW-TSK-archived]]", materialized)
        self.assertIn("`obs:OAW-TSK-cli`", materialized)
        self.assertIn("[obs:OAW-TSK-cli](https://example.test/obs:OAW-TSK-cli)", materialized)
        self.assertIn("![[Existing embed|obs:OAW-TSK-cli]]", materialized)
        self.assertIn("![obs:OAW-TSK-cli](image.png)", materialized)
        self.assertIn("[obs:OAW-TSK-cli][reference]", materialized)
        self.assertIn("<https://example.test/obs:OAW-TSK-cli>", materialized)
        self.assertIn("[reference]: https://example.test/obs:OAW-TSK-cli", materialized)
        self.assertIn("prefixobs:OAW-TSK-cli", materialized)
        self.assertIn("/obs:OAW-TSK-cli", materialized)
        self.assertIn("obs:OAW-TSK-cli/path", materialized)
        self.assertIn("obs:OAW-TSK-cli.md", materialized)
        self.assertIn("Bare OAW-TSK-cli stays bare", materialized)
        self.assertIn("[[Projects/Obsidian Agent Workflow/Index|OAW]]", materialized)
        self.assertIn(
            "| [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]] |",
            materialized,
        )
        self.assertIn("materialize-example: obs:DOES-NOT-EXIST", materialized)
        self.assertIn("```text\nobs:OAW-TSK-cli\n```", materialized)

        again = self.run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("References: none", again.stdout)
        self.assertEqual(task_path.read_text(encoding="utf-8"), materialized)

    def test_link_materialize_errors_without_writing_for_missing_or_ambiguous_ids(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8")
            + "\nValid obs:OAW-TSK-archived then missing obs:OAW-TSK-nope.\n",
            encoding="utf-8",
        )
        before_missing = task_path.read_bytes()
        missing = self.run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no note with frontmatter id or alias 'OAW-TSK-nope'", missing.stderr)
        self.assertEqual(task_path.read_bytes(), before_missing)

        write(
            self.vault / "Projects/Other/Tasks/Duplicate.md",
            "---\nid: OAW-TSK-archived\n---\n\n# Duplicate\n",
        )
        task_path.write_text(
            "---\nid: OAW-TSK-materialize-source\n---\n\n# Source\n\nobs:OAW-TSK-archived\n"
        )
        before_ambiguous = task_path.read_bytes()
        ambiguous = self.run_oaw("link", "materialize", "OAW-TSK-materialize-source", "--write")
        self.assertEqual(ambiguous.returncode, 1)
        self.assertIn("id 'OAW-TSK-archived' is not unique", ambiguous.stderr)
        self.assertEqual(task_path.read_bytes(), before_ambiguous)

    def test_link_materialize_rejects_malformed_reference_and_rolls_back(self, monkeypatch):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8") + "\nMalformed obs: stays literal.\n",
            encoding="utf-8",
        )
        malformed_before = task_path.read_bytes()
        malformed = self.run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("malformed obs reference", malformed.stderr)
        self.assertEqual(task_path.read_bytes(), malformed_before)

        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "Malformed obs: stays literal.", "Valid obs:OAW-TSK-archived."
            ),
            encoding="utf-8",
        )
        rollback_before = task_path.read_bytes()

        def fail_commit(_self):
            raise OawError("simulated transaction failure")

        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        monkeypatch.setattr(links.VaultTransaction, "commit", fail_commit)
        stderr = StringIO()
        with redirect_stderr(stderr):
            returncode = cli.main(["link", "materialize", "OAW-TSK-cli", "--write"])

        self.assertEqual(returncode, 1)
        self.assertIn("simulated transaction failure", stderr.getvalue())
        self.assertEqual(task_path.read_bytes(), rollback_before)

    def test_link_materialize_refuses_to_overwrite_a_concurrent_edit(self, monkeypatch):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8") + "\nValid obs:OAW-TSK-archived.\n",
            encoding="utf-8",
        )
        concurrent = task_path.read_bytes() + b"Concurrent edit remains.\n"
        original_commit = links.VaultTransaction.commit

        def commit_after_concurrent_edit(transaction):
            task_path.write_bytes(concurrent)
            original_commit(transaction)

        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        monkeypatch.setattr(links.VaultTransaction, "commit", commit_after_concurrent_edit)
        stderr = StringIO()
        with redirect_stderr(stderr):
            returncode = cli.main(["link", "materialize", "OAW-TSK-cli", "--write"])

        self.assertEqual(returncode, 1)
        self.assertIn("note changed on disk since it was read", stderr.getvalue())
        self.assertEqual(task_path.read_bytes(), concurrent)

    def test_obs_materialization_caches_repeated_resolution(self, monkeypatch):
        references = resolver.scan_note_references(self.vault)
        original = links.resolve_id_from_references
        calls = []

        def recording_resolve(target, root, cached_references):
            calls.append(target)
            return original(target, root, cached_references)

        monkeypatch.setattr(links, "resolve_id_from_references", recording_resolve)
        rendered, replacements = links.materialize_obs_references(
            "obs:OAW-TSK-cli and obs:OAW-TSK-cli", self.vault, references
        )

        self.assertEqual(calls, ["OAW-TSK-cli"])
        self.assertEqual(len(replacements), 2)
        self.assertEqual(rendered.count("[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI"), 2)

    def test_obs_materialization_preserves_bytes_and_complex_protected_spans(self):
        write(
            self.vault / "Projects/Legacy/Tasks/Underscore.md",
            "---\nid: OAW-TSK-legacy_v2\n---\n\n# Legacy\n",
        )
        durable = "[[Projects/Legacy/Tasks/Underscore|OAW-TSK-legacy_v2]]"
        source = (
            "  obs:OAW-TSK-legacy_v2  \r\n"
            "[[Existing|alias]] and obs:OAW-TSK-cli\r\n"
            "````text\r\n"
            "obs:OAW-TSK-cli\r\n"
            "```\r\n"
            "obs:OAW-TSK-archived\r\n"
            "````\r\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertTrue(rendered.startswith(f"  {durable}  \r\n"))
        self.assertIn(
            "[[Existing|alias]] and "
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]\r\n",
            rendered,
        )
        self.assertIn(
            "````text\r\nobs:OAW-TSK-cli\r\n```\r\nobs:OAW-TSK-archived\r\n````\r\n",
            rendered,
        )
        self.assertEqual(
            [item.reference for item in replacements], ["obs:OAW-TSK-legacy_v2", "obs:OAW-TSK-cli"]
        )

    def test_obs_materialization_protects_bare_uri_and_query_values(self):
        source = (
            "https://example.test/?ref=obs:OAW-TSK-cli\n"
            "mailto:agent@example.test?subject=obs:OAW-TSK-archived\n"
            "obsidian://open?vault=example&file=obs:OAW-TSK-cli\n"
            "urn:example:item?related=obs:OAW-TSK-archived\n"
            "/relative/path?ref=obs:OAW-TSK-cli\n"
            "data:text/plain,obs:OAW-TSK-archived\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(rendered, source)
        self.assertEqual(replacements, [])

    def test_obs_materialization_keeps_standalone_prose_references_eligible(self):
        source = (
            "See obs:OAW-TSK-cli, (obs:OAW-TSK-archived), and value=obs:OAW-TSK-cli in prose.\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(len(replacements), 3)
        self.assertIn(
            "See [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]",
            rendered,
        )
        self.assertIn(
            "([[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]])",
            rendered,
        )
        self.assertIn(
            "value=[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]",
            rendered,
        )

    def test_obs_materialization_protects_container_nested_fenced_code(self):
        source = (
            "> ~~~text\n"
            "> obs:OAW-TSK-cli\n"
            "> ~~~\n"
            "\n"
            "- ~~~text\n"
            "  obs:OAW-TSK-archived\n"
            "  ~~~\n"
            "\n"
            "> ```text\n"
            "> literal ``` here\n"
            "> obs:OAW-TSK-cli\n"
            "> ```\n"
            "\n"
            "```text\n"
            "- ```\n"
            "> ```\n"
            "obs:OAW-TSK-archived\n"
            "```\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(rendered, source)
        self.assertEqual(replacements, [])

    def test_obs_materialization_protects_container_nested_indented_code(self):
        sources = (
            ">     obs:OAW-TSK-cli\n",
            "-     obs:OAW-TSK-archived\n",
            "- item\n\n      obs:OAW-TSK-cli\n",
        )

        for source in sources:
            with self.subTest(source=source):
                rendered, replacements = links.materialize_obs_references(source, self.vault)
                self.assertEqual(rendered, source)
                self.assertEqual(replacements, [])

    def test_obs_materialization_protects_container_nested_reference_definitions(self):
        source = (
            "> [quoted obs:OAW-TSK-cli]: /quote\n"
            ">\n"
            "> [quoted obs:OAW-TSK-cli]\n"
            "\n"
            "- [listed obs:OAW-TSK-archived]: /list\n"
            "\n"
            "  [listed obs:OAW-TSK-archived]\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(rendered, source)
        self.assertEqual(replacements, [])

    def test_obs_materialization_protects_commonmark_indented_code_blocks(self):
        source = (
            "    obs:OAW-TSK-cli\n"
            "\tobs:OAW-TSK-archived\n"
            "\n"
            "Paragraph continuation:\n"
            "    obs:OAW-TSK-cli\n"
            "\n"
            "    obs:OAW-TSK-archived\n"
            "\tobs:OAW-TSK-cli\n"
            "outside obs:OAW-TSK-archived\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertTrue(rendered.startswith("    obs:OAW-TSK-cli\n\tobs:OAW-TSK-archived\n"))
        self.assertIn(
            "    [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]\n",
            rendered,
        )
        self.assertIn("    obs:OAW-TSK-archived\n\tobs:OAW-TSK-cli\n", rendered)
        self.assertIn(
            "outside [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]",
            rendered,
        )
        self.assertEqual(
            [item.reference for item in replacements], ["obs:OAW-TSK-cli", "obs:OAW-TSK-archived"]
        )

    def test_link_materialize_write_preserves_crlf_bytes(self):
        path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/CRLF source.md"
        path.write_bytes(
            b"---\r\n"
            b"id: OAW-TSK-crlf-source\r\n"
            b"aliases:\r\n"
            b"  - OAW-TSK-crlf-source\r\n"
            b"---\r\n\r\n"
            b"# CRLF source\r\n\r\n"
            b"See obs:OAW-TSK-cli.\r\n"
        )
        path.chmod(0o644)

        proc = self.run_oaw("link", "materialize", "OAW-TSK-crlf-source", "--write")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        written = path.read_bytes()
        self.assertNotIn(b"\n", written.replace(b"\r\n", b""))
        self.assertIn(
            b"[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].\r\n",
            written,
        )

    def test_obs_materialization_protects_complex_markdown_link_labels(self):
        source = (
            "[nested [obs:OAW-TSK-cli] label](https://example.test/a_(b)) "
            "then obs:OAW-TSK-cli.\n"
            "[escaped \\] obs:OAW-TSK-archived](https://example.test/target) "
            "then obs:OAW-TSK-archived.\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn(
            "[nested [obs:OAW-TSK-cli] label](https://example.test/a_(b)) then "
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].",
            rendered,
        )
        self.assertIn(
            "[escaped \\] obs:OAW-TSK-archived](https://example.test/target) then "
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]].",
            rendered,
        )
        self.assertEqual(
            [item.reference for item in replacements],
            ["obs:OAW-TSK-cli", "obs:OAW-TSK-archived"],
        )

    def test_obs_materialization_protects_multiline_markdown_links_and_images(self):
        source = (
            "[See obs:OAW-TSK-cli\r\nfor details](https://example.test/path) | "
            "obs:OAW-TSK-archived |\r\n"
            "[See obs:OAW-TSK-archived\r\nby reference][details]\r\n"
            "![Alt obs:OAW-TSK-cli\r\ncontinued](image.png)\r\n"
            "Outside obs:OAW-TSK-archived.\r\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(
            rendered,
            "[See obs:OAW-TSK-cli\r\nfor details](https://example.test/path) | "
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task\\|"
            "OAW-TSK-archived]] |\r\n"
            "[See obs:OAW-TSK-archived\r\nby reference][details]\r\n"
            "![Alt obs:OAW-TSK-cli\r\ncontinued](image.png)\r\n"
            "Outside [[Projects/Obsidian Agent Workflow/Tasks/Archived task|"
            "OAW-TSK-archived]].\r\n",
        )
        self.assertEqual(
            [item.reference for item in replacements],
            ["obs:OAW-TSK-archived", "obs:OAW-TSK-archived"],
        )

    def test_obs_materialization_protects_only_defined_shortcut_reference_links(self):
        source = (
            "[obs:OAW-TSK-cli] and [arbitrary obs:OAW-TSK-archived].\n"
            "[See obs:OAW-TSK-archived\nfor details]\n"
            "[fenced obs:OAW-TSK-archived]\n"
            "[obs:OAW-TSK-cli]: https://example.test/cli\n"
            "[See obs:OAW-TSK-archived for details]: https://example.test/details\n"
            "```text\n[fenced obs:OAW-TSK-archived]: https://example.test/fenced\n```\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn("[obs:OAW-TSK-cli]", rendered)
        self.assertIn(
            "[arbitrary [[Projects/Obsidian Agent Workflow/Tasks/Archived task|"
            "OAW-TSK-archived]]].",
            rendered,
        )
        self.assertIn("[See obs:OAW-TSK-archived\nfor details]\n", rendered)
        self.assertIn(
            "[fenced [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]",
            rendered,
        )
        self.assertIn("[obs:OAW-TSK-cli]: https://example.test/cli\n", rendered)
        self.assertEqual(
            [item.reference for item in replacements],
            ["obs:OAW-TSK-archived", "obs:OAW-TSK-archived"],
        )

    def test_fake_definitions_in_multiline_protected_spans_do_not_activate_shortcuts(self):
        source = (
            "``code starts\n"
            "[code obs:OAW-TSK-cli]: https://example.test/code\n"
            "code ends``\n"
            "[outer label\n"
            "[link obs:OAW-TSK-archived]: https://example.test/link\n"
            "continued](https://example.test/outer)\n"
            "[code obs:OAW-TSK-cli]\n"
            "[link obs:OAW-TSK-archived]\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn("[code obs:OAW-TSK-cli]: https://example.test/code", rendered)
        self.assertIn("[link obs:OAW-TSK-archived]: https://example.test/link", rendered)
        self.assertIn(
            "[code [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]]",
            rendered,
        )
        self.assertIn(
            "[link [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]",
            rendered,
        )
        self.assertEqual(len(replacements), 2)

    def test_shortcut_definitions_require_valid_destination_variants(self):
        source = (
            "[angle obs:OAW-TSK-cli] and [bare obs:OAW-TSK-archived].\n"
            "[empty obs:OAW-TSK-cli]\n"
            "[angle obs:OAW-TSK-cli]: <https://example.test/angle>\n"
            '[bare obs:OAW-TSK-archived]: /docs_(v1) "Documentation"\n'
            "[empty obs:OAW-TSK-cli]:\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn("[angle obs:OAW-TSK-cli] and [bare obs:OAW-TSK-archived].", rendered)
        self.assertEqual(
            rendered.count("[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"), 2
        )
        self.assertIn("[angle obs:OAW-TSK-cli]: <https://example.test/angle>", rendered)
        self.assertIn('[bare obs:OAW-TSK-archived]: /docs_(v1) "Documentation"', rendered)
        self.assertEqual(
            [item.reference for item in replacements],
            ["obs:OAW-TSK-cli", "obs:OAW-TSK-cli"],
        )

    def test_reference_definition_continuation_titles_are_protected(self):
        source = (
            "[double obs:OAW-TSK-cli] [single obs:OAW-TSK-archived] "
            "[paren obs:OAW-TSK-cli]\n"
            "[double obs:OAW-TSK-cli]: /double\n"
            '  "Double title obs:OAW-TSK-archived"\n'
            "[single obs:OAW-TSK-archived]: <https://example.test/single>\n"
            " 'Single title obs:OAW-TSK-cli'\n"
            "[paren obs:OAW-TSK-cli]: /paren\n"
            "   (Parenthesized title obs:OAW-TSK-archived)\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(rendered, source)
        self.assertEqual(replacements, [])

    def test_invalid_reference_definition_title_continuation_remains_prose(self):
        source = (
            "[invalid obs:OAW-TSK-cli]\n"
            "[invalid obs:OAW-TSK-cli]: /invalid\n"
            '  "unterminated title obs:OAW-TSK-archived\n'
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn("[invalid obs:OAW-TSK-cli]\n", rendered)
        self.assertIn("[invalid obs:OAW-TSK-cli]: /invalid\n", rendered)
        self.assertIn(
            '  "unterminated title [[Projects/Obsidian Agent Workflow/Tasks/Archived task|'
            "OAW-TSK-archived]]\n",
            rendered,
        )
        self.assertEqual([item.reference for item in replacements], ["obs:OAW-TSK-archived"])

    def test_reference_definition_title_rejects_tabs_and_nested_parentheses(self):
        source = (
            "[tab obs:OAW-TSK-cli] [nested obs:OAW-TSK-cli] "
            "[escaped obs:OAW-TSK-cli]\n"
            "[tab obs:OAW-TSK-cli]: /tab\n"
            '\t"tab title obs:OAW-TSK-archived"\n'
            "[nested obs:OAW-TSK-cli]: /nested\n"
            "  (outer (nested title obs:OAW-TSK-archived)\n"
            "[escaped obs:OAW-TSK-cli]: /escaped\n"
            "  (escaped \\( title obs:OAW-TSK-archived)\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn("[tab obs:OAW-TSK-cli] [nested obs:OAW-TSK-cli]", rendered)
        self.assertIn(
            '\t"tab title [[Projects/Obsidian Agent Workflow/Tasks/Archived task|'
            'OAW-TSK-archived]]"\n',
            rendered,
        )
        self.assertIn(
            "  (outer (nested title "
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]])\n",
            rendered,
        )
        self.assertIn("  (escaped \\( title obs:OAW-TSK-archived)\n", rendered)
        self.assertEqual(len(replacements), 2)

    def test_cross_line_link_candidate_stops_at_blank_block_boundary(self):
        source = "[not a link obs:OAW-TSK-cli\n\ncontinued](https://example.test)\n"

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(
            rendered,
            "[not a link [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
            "OAW-TSK-cli]]\n\ncontinued](https://example.test)\n",
        )
        self.assertEqual([item.reference for item in replacements], ["obs:OAW-TSK-cli"])

    def test_obs_materialization_protects_balanced_reference_definition_labels(self):
        source = (
            "[nested [obs:OAW-TSK-cli] label]: https://example.test/obs:OAW-TSK-archived\n"
            "[escaped \\] obs:OAW-TSK-archived]: https://example.test/obs:OAW-TSK-cli\n"
            "Outside obs:OAW-TSK-cli.\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn(
            "[nested [obs:OAW-TSK-cli] label]: https://example.test/obs:OAW-TSK-archived\n",
            rendered,
        )
        self.assertIn(
            "[escaped \\] obs:OAW-TSK-archived]: https://example.test/obs:OAW-TSK-cli\n",
            rendered,
        )
        self.assertIn(
            "Outside [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].",
            rendered,
        )
        self.assertEqual([item.reference for item in replacements], ["obs:OAW-TSK-cli"])

    def test_obs_materialization_protects_complete_multiline_reference_definitions(self):
        source = (
            "[next-line obs:OAW-TSK-cli]:\n"
            "  <https://example.test/obs:OAW-TSK-archived>\n"
            "[multi\n"
            "label obs:OAW-TSK-archived]: /docs\n"
            "[title obs:OAW-TSK-cli]: /title\n"
            '  "title obs:OAW-TSK-archived"\n'
            "Outside obs:OAW-TSK-cli.\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(
            rendered,
            source.removesuffix("Outside obs:OAW-TSK-cli.\n")
            + "Outside [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].\n",
        )
        self.assertEqual([item.reference for item in replacements], ["obs:OAW-TSK-cli"])

    def test_obs_materialization_rejects_invalid_reference_definition_destinations_and_labels(self):
        oversized_label = "x" * 1000
        source = (
            "[invalid destination obs:OAW-TSK-cli]: https://example.test/<bad>\n"
            f"[{oversized_label} obs:OAW-TSK-archived]: /too-long\n"
        )

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertIn(
            "[invalid destination [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
            "OAW-TSK-cli]]]: https://example.test/<bad>",
            rendered,
        )
        self.assertIn(
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]: /too-long",
            rendered,
        )
        self.assertEqual(
            [item.reference for item in replacements],
            ["obs:OAW-TSK-cli", "obs:OAW-TSK-archived"],
        )

    def test_cross_line_link_candidate_stops_at_setext_block_boundary(self):
        source = "[not a link obs:OAW-TSK-cli\n===\ncontinued](https://example.test)\n"

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(
            rendered,
            "[not a link [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
            "OAW-TSK-cli]]\n===\ncontinued](https://example.test)\n",
        )
        self.assertEqual([item.reference for item in replacements], ["obs:OAW-TSK-cli"])

    def test_multiline_code_spans_are_protected_by_shared_automatic_materialization(self):
        note = (
            "`single line break\nobs:OAW-TSK-cli\nclosing` then obs:OAW-TSK-cli.\n"
            "``multi line break\nobs:OAW-TSK-archived\nclosing`` then "
            "obs:OAW-TSK-archived."
        )
        rendered, replacements = links.materialize_obs_references(note, self.vault)

        self.assertIn("`single line break\nobs:OAW-TSK-cli\nclosing` then [[", rendered)
        self.assertIn("``multi line break\nobs:OAW-TSK-archived\nclosing`` then [[", rendered)
        self.assertEqual(len(replacements), 2)

        created = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Multiline materialization",
            "--note",
            note,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        created_text = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Multiline materialization.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`single line break\nobs:OAW-TSK-cli\nclosing` then [[", created_text)
        self.assertIn("``multi line break\nobs:OAW-TSK-archived\nclosing`` then [[", created_text)

    def test_table_pipe_detection_inherits_cross_line_code_span_state(self):
        source = "``code starts\nobs:OAW-TSK-archived closes`` | obs:OAW-TSK-cli |\n"

        rendered, replacements = links.materialize_obs_references(source, self.vault)

        self.assertEqual(
            rendered,
            "``code starts\nobs:OAW-TSK-archived closes`` | "
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]] |\n",
        )
        self.assertEqual(len(replacements), 1)
        self.assertEqual(
            replacements[0].link,
            "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]]",
        )

    def test_automatic_materialization_failures_do_not_partially_write(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before_board = board_path.read_bytes()
        missing_task = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Missing materialized target",
            "--note",
            "See obs:OAW-TSK-does-not-exist.",
        )
        self.assertEqual(missing_task.returncode, 1)
        self.assertFalse(
            (
                self.vault / "Projects/Obsidian Agent Workflow/Tasks/Missing materialized target.md"
            ).exists()
        )
        self.assertEqual(board_path.read_bytes(), before_board)

        target = self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
        before_target = target.read_bytes()
        missing_observation = self.run_oaw(
            "note",
            "observe",
            "AGT-TSK-obsidian-task-ids",
            "--title",
            "Missing target",
            "--body",
            "See obs:OAW-TSK-does-not-exist.",
        )
        self.assertEqual(missing_observation.returncode, 1)
        self.assertEqual(target.read_bytes(), before_target)

    def test_durable_prose_writes_share_obs_materialization(self):
        created = self.run_oaw(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Materialized prose",
            "--note",
            "Start from obs:OAW-TSK-cli.",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Materialized prose.md"
        task = task_path.read_text(encoding="utf-8")
        durable = "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"
        self.assertIn(f"Start from {durable}.", task)

        transitioned = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-materialized-prose",
            "--note",
            "Continue with obs:OAW-TSK-archived.",
        )
        self.assertEqual(transitioned.returncode, 0, transitioned.stderr)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn(
            "Continue with [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]].",
            task,
        )

        noted = self.run_oaw(
            "task",
            "note",
            "OAW-TSK-materialized-prose",
            "--note",
            "Task note obs:OAW-TSK-cli.",
            "--checks",
            "obs:OAW-TSK-archived",
        )
        self.assertEqual(noted.returncode, 0, noted.stderr)
        task_text = task_path.read_text(encoding="utf-8")
        self.assertIn(f"Task note {durable}.", task_text)
        self.assertIn("checks: obs:OAW-TSK-archived", task_text)

        project = self.run_oaw(
            "project",
            "create",
            "--name",
            "Materialized Project",
            "--alias",
            "MAT",
            "--goal",
            "Build from obs:OAW-TSK-cli.",
        )
        self.assertEqual(project.returncode, 0, project.stderr)
        project_index = self.vault / "Projects/Materialized Project/Index.md"
        self.assertIn(f"Build from {durable}.", project_index.read_text(encoding="utf-8"))

        session = self.run_oaw(
            "note",
            "session",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Session note obs:OAW-TSK-cli.",
        )
        self.assertEqual(session.returncode, 0, session.stderr)
        agent_task = self.vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
        self.assertIn(f"Session note {durable}.", agent_task.read_text(encoding="utf-8"))

        observation = self.run_oaw(
            "note",
            "observe",
            "AGT-TSK-obsidian-task-ids",
            "--title",
            "Literal title obs:OAW-TSK-cli",
            "--body",
            "Observation body obs:OAW-TSK-cli.",
        )
        self.assertEqual(observation.returncode, 0, observation.stderr)
        agent_text = agent_task.read_text(encoding="utf-8")
        self.assertIn("Literal title obs:OAW-TSK-cli", agent_text)
        self.assertIn(f"Observation body {durable}.", agent_text)

        feedback = self.run_oaw(
            "feedback",
            "create",
            "--title",
            "Materialized feedback",
            "--type",
            "verified",
            "--scope",
            "materialization",
            "--body",
            "Feedback body obs:OAW-TSK-cli.",
            "--command",
            "obs:OAW-TSK-archived",
        )
        self.assertEqual(feedback.returncode, 0, feedback.stderr)
        feedback_note = next((self.vault / "Agents/Feedback").glob("*Materialized feedback.md"))
        feedback_text = feedback_note.read_text(encoding="utf-8")
        self.assertIn(f"Feedback body {durable}.", feedback_text)
        self.assertIn('command: "obs:OAW-TSK-archived"', feedback_text)

        retro = self.run_oaw(
            "retro",
            "create",
            "--title",
            "Materialized retrospective",
            "--summary",
            "Summary obs:OAW-TSK-cli.",
        )
        self.assertEqual(retro.returncode, 0, retro.stderr)
        retro_note = next(
            (self.vault / "Agents/Retrospectives").glob("*materialized retrospective.md")
        )
        self.assertIn(f"Summary {durable}.", retro_note.read_text(encoding="utf-8"))

        research = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "materialization-exclusion",
            "--title",
            "Research obs:OAW-TSK-cli",
        )
        self.assertEqual(research.returncode, 0, research.stderr)
        prompt = self.vault / (
            "Projects/Obsidian Agent Workflow/Research/materialization-exclusion/Prompt.md"
        )
        self.assertIn("Research obs:OAW-TSK-cli", prompt.read_text(encoding="utf-8"))

    def test_link_materialize_rejects_conflicting_dry_run_and_write(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8") + "\nobs:OAW-TSK-archived\n",
            encoding="utf-8",
        )
        before = task_path.read_bytes()

        proc = self.run_oaw("link", "materialize", "OAW-TSK-cli", "--dry-run", "--write")

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("not allowed with argument", proc.stderr)
        self.assertEqual(task_path.read_bytes(), before)

    def test_link_ensure_rejects_conflicting_dry_run_and_write(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        before = task_path.read_text(encoding="utf-8")

        proc = self.run_oaw(
            "link",
            "ensure",
            "OAW-TSK-cli",
            "OAW-TSK-archived",
            "--dry-run",
            "--write",
        )

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("not allowed with argument", proc.stderr)
        self.assertEqual(before, task_path.read_text(encoding="utf-8"))

    def test_link_ensure_bidirectional_rejects_conflicting_dry_run_and_write(self):
        proc = self.run_oaw(
            "link",
            "ensure-bidirectional",
            "OAW-TSK-cli",
            "OAW-TSK-archived",
            "--dry-run",
            "--write",
        )

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("not allowed with argument", proc.stderr)

    def test_link_ensure_bidirectional_writes_missing_reciprocal_links(self):
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Alpha.md",
            """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-alpha
aliases:
  - OAW-TSK-alpha
---

# Alpha
""",
        )
        write(
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Beta.md",
            """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-beta
aliases:
  - OAW-TSK-beta
---

# Beta
""",
        )

        proc = self.run_oaw(
            "link",
            "ensure-bidirectional",
            "OAW-TSK-alpha",
            "OAW-TSK-beta",
            "--write",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        alpha = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Alpha.md").read_text()
        beta = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Beta.md").read_text()
        self.assertIn("[[Projects/Obsidian Agent Workflow/Tasks/Beta|OAW-TSK-beta]]", alpha)
        self.assertIn("[[Projects/Obsidian Agent Workflow/Tasks/Alpha|OAW-TSK-alpha]]", beta)

    def test_link_lint_suggests_durable_opaque_id_replacements(self):
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
        task.write_text(
            task.read_text(encoding="utf-8")
            + "\n## Related\n\n- [[OAW-TSK-cli]]\n- [[PMX-UNKNOWN]]\n",
            encoding="utf-8",
        )

        proc = self.run_oaw("link", "lint")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "Archived task.md: [[OAW-TSK-cli]] -> [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]",
            proc.stdout,
        )
        self.assertIn("Archived task.md: [[PMX-UNKNOWN]] -> (unresolved)", proc.stdout)

    def test_link_lint_skips_non_utf8_notes(self):
        bad = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Binary.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"---\nid: OAW-TSK-binary\n---\n\xff\xfe")
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
        task.write_text(
            task.read_text(encoding="utf-8") + "\n- [[OAW-TSK-cli]]\n",
            encoding="utf-8",
        )

        proc = self.run_oaw("link", "lint")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Archived task.md: [[OAW-TSK-cli]]", proc.stdout)

    def test_link_commands_ignore_wikilinks_inside_fenced_code(self):
        task = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
        task.write_text(
            task.read_text(encoding="utf-8") + "\n```markdown\n[[OAW-TSK-cli]]\n```\n",
            encoding="utf-8",
        )

        listed = self.run_oaw("link", "list", "OAW-TSK-archived")
        linted = self.run_oaw("link", "lint")

        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertNotIn("[[OAW-TSK-cli]]", listed.stdout)
        self.assertEqual(linted.returncode, 0, linted.stderr)
        self.assertNotIn("Archived task.md: [[OAW-TSK-cli]]", linted.stdout)

    def test_session_snapshot_copies_artifacts_and_writes_manifest(self):
        session_id = "73550790-5af5-4efc-828c-72e6e1053d8f"
        codex_thread = "019f3e73-029f-7ea2-9772-fdfa1e25fb8f"
        task_codex_thread = "019f3e8d-8307-7052-b367-57e78f3316ae"
        fork_session_id = "019f3ef0-1111-7222-8333-c26aa5d38893"
        claude_root = self.vault / "harness/claude/projects"
        codex_root = self.vault / "harness/codex/sessions"
        plugin_root = self.vault / "harness/claude/plugins/data"
        output_root = self.vault / "Agents/Retrospectives/attachments"

        parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
        write(
            parent,
            f'{{"timestamp":"2026-07-07T21:18:45.572Z","sessionId":"{session_id}",'
            f'"content":"Codex thread CODEX_THREAD_ID={codex_thread}; '
            'plugin job task-mrb5j4y9-7k3yjy"}}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "subagents/agent-a8fbf333b1df5e1e9.jsonl",
            '{"timestamp":"2026-07-07T21:19:00.413Z","content":"delegated"}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "subagents/nested/agent-nested.jsonl",
            '{"content":"nested delegated transcript"}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "tasks/background.output",
            f"background transcript references codex_thread={task_codex_thread}\n",
        )
        write(
            claude_root / "-tmp-project" / session_id / "subagents/workflows/wf-123/run.jsonl",
            '{"content":"workflow run journal"}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "workflows/scripts/nightly.md",
            "# Workflow script\n",
        )
        fork_parent = claude_root / "-tmp-project" / f"{fork_session_id}.jsonl"
        write(
            fork_parent,
            f'{{"timestamp":"2026-07-07T22:00:00.000Z","sessionId":"{fork_session_id}",'
            '"content":"forked context"}}\n',
        )
        matching_rollout = (
            codex_root / "2026/07/07" / f"rollout-2026-07-07T23-19-12-{codex_thread}.jsonl"
        )
        write(matching_rollout, '{"event":"turn_aborted"}\n')
        task_rollout = (
            codex_root / "2026/07/07" / f"rollout-2026-07-07T23-30-00-{task_codex_thread}.jsonl"
        )
        write(task_rollout, '{"content":"referenced from task output"}\n')
        grep_rollout = (
            codex_root
            / "2026/07/07"
            / "rollout-2026-07-07T23-48-09-019f3e8d-8307-7052-b367-57e78f3316ae.jsonl"
        )
        write(grep_rollout, '{"content":"session-inspection-claude-codex other"}\n')
        write(
            plugin_root / "codex-openai-codex/state/example/jobs/task-mrb5j4y9-7k3yjy.log",
            "running\n",
        )

        proc = self.run_oaw(
            "session",
            "snapshot",
            session_id,
            "--slug",
            "SR dogfood zombie Codex",
            "--partial",
            "--grep",
            "session-inspection-claude-codex",
            "--claude-session",
            fork_session_id,
            "--output-root",
            str(output_root),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
            "--plugin-data-root",
            str(plugin_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-07-sr-dogfood-zombie-codex"
        manifest_path = snapshot / "manifest.json"
        self.assertTrue((snapshot / "claude/parent-73550790-PARTIAL.jsonl").exists())
        self.assertTrue((snapshot / "claude/agent-a8fbf333b1df5e1e9.jsonl").exists())
        self.assertTrue((snapshot / "claude/subagents/nested/agent-nested.jsonl").exists())
        self.assertTrue((snapshot / "claude/tasks/background.output").exists())
        self.assertTrue((snapshot / "claude/workflows/wf-123/run.jsonl").exists())
        self.assertTrue((snapshot / "claude/workflow-scripts/nightly.md").exists())
        self.assertTrue((snapshot / "claude/forks/parent-019f3ef0.jsonl").exists())
        self.assertTrue((snapshot / "codex" / matching_rollout.name).exists())
        self.assertTrue((snapshot / "codex" / task_rollout.name).exists())
        self.assertTrue((snapshot / "codex" / grep_rollout.name).exists())
        self.assertTrue((snapshot / "plugin-logs/task-mrb5j4y9-7k3yjy.log").exists())
        self.assertIn(f"Manifest: {manifest_path}", proc.stdout)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "oaw-session-snapshot-v1")
        self.assertEqual(manifest["session_id"], session_id)
        self.assertEqual(manifest["snapshot"]["mode"], "claude-parent")
        self.assertEqual(manifest["snapshot"]["parent_completeness"], "partial")
        sources = {entry["source"] for entry in manifest["files"]}
        self.assertIn(str(parent), sources)
        self.assertIn(str(matching_rollout), sources)
        self.assertIn(str(task_rollout), sources)
        self.assertIn(str(fork_parent), sources)
        categories = {entry["category"] for entry in manifest["files"]}
        self.assertIn("claude-task-output", categories)
        self.assertIn("claude-workflow-artifact", categories)
        self.assertIn("claude-workflow-script", categories)
        self.assertIn("claude-fork-parent", categories)
        self.assertTrue(all(entry["sha256"] for entry in manifest["files"]))

    def test_session_snapshot_refresh_updates_parent_and_adds_subagents(self):
        session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
        claude_root = self.vault / "harness/claude/projects"
        output_root = self.vault / "attachments"
        parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
        write(
            parent,
            f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
            '"content":"first"}}\n',
        )
        nested_subagent = (
            claude_root / "-tmp-project" / session_id / "subagents/nested/agent-nested.jsonl"
        )
        write(nested_subagent, '{"content":"nested"}\n')

        base_args = (
            "session",
            "snapshot",
            session_id,
            "--slug",
            "refresh test",
            "--partial",
            "--output-root",
            str(output_root),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(self.vault / "missing-codex"),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
        )
        first = self.run_oaw_subprocess(*base_args)
        self.assertEqual(first.returncode, 0, first.stderr)
        snapshot = output_root / "2026-07-08-refresh-test"
        nested_copy = snapshot / "claude/subagents/nested/agent-nested.jsonl"
        self.assertTrue(nested_copy.exists())
        stale = snapshot / "codex/stale-rollout.jsonl"
        write(stale, "{}\n")
        manifest_path = snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(
            {
                "category": "codex-rollout",
                "source": "/tmp/stale-rollout.jsonl",
                "destination": "codex/stale-rollout.jsonl",
                "copied_at": "2026-07-08T01:00:00+00:00",
                "completeness": "complete",
                "size_bytes": 3,
                "sha256": "stale",
            }
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        write(
            parent,
            f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
            '"content":"second"}}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "subagents/agent-new.jsonl",
            '{"content":"new subagent"}\n',
        )
        second = self.run_oaw_subprocess(*base_args)
        self.assertEqual(second.returncode, 0, second.stderr)

        parent_copy = snapshot / "claude/parent-019f3ed8-PARTIAL.jsonl"
        self.assertIn("second", parent_copy.read_text(encoding="utf-8"))
        self.assertTrue((snapshot / "claude/agent-new.jsonl").exists())
        self.assertTrue(nested_copy.exists())
        self.assertFalse(stale.exists())
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        destinations = {entry["destination"] for entry in manifest["files"]}
        self.assertIn("claude/agent-new.jsonl", destinations)
        self.assertIn("claude/subagents/nested/agent-nested.jsonl", destinations)

    def test_session_snapshot_supports_codex_only_thread_and_discovers_references(self):
        thread_id = "019f48d7-39c2-7043-9c19-5a3565995898"
        child_thread = "019f48d8-1111-7222-8333-c26aa5d38893"
        grandchild_thread = "019f48d9-2222-7333-8444-d37bb6e49904"
        codex_root = self.vault / "harness/codex/sessions"
        plugin_root = self.vault / "harness/claude/plugins/data"
        output_root = self.vault / "attachments"
        rollout = codex_root / "2026/07/10" / f"rollout-2026-07-10T00-00-00-{thread_id}.jsonl"
        child_rollout = (
            codex_root / "2026/07/10" / f"rollout-2026-07-10T00-05-00-{child_thread}.jsonl"
        )
        grandchild_rollout = (
            codex_root / "2026/07/10" / f"rollout-2026-07-10T00-10-00-{grandchild_thread}.jsonl"
        )
        write(
            rollout,
            f'{{"timestamp":"2026-07-10T00:00:00.000Z","content":"codex_thread={child_thread}"}}\n',
        )
        write(
            child_rollout,
            '{"timestamp":"2026-07-10T00:05:00.000Z",'
            f'"content":"codex_thread={grandchild_thread}; '
            'plugin task-abcd1234-efgh5678"}\n',
        )
        write(grandchild_rollout, '{"timestamp":"2026-07-10T00:10:00.000Z"}\n')
        write(
            plugin_root / "example/jobs/task-abcd1234-efgh5678.log",
            "complete\n",
        )

        proc = self.run_oaw(
            "session",
            "snapshot",
            thread_id.upper(),
            "--codex-only",
            "--slug",
            "codex only",
            "--output-root",
            str(output_root),
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
            "--plugin-data-root",
            str(plugin_root),
            env={"CODEX_THREAD_ID": thread_id},
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-10-codex-only"
        self.assertTrue((snapshot / "codex" / rollout.name).exists())
        self.assertTrue((snapshot / "codex" / child_rollout.name).exists())
        self.assertTrue((snapshot / "codex" / grandchild_rollout.name).exists())
        self.assertTrue((snapshot / "plugin-logs/task-abcd1234-efgh5678.log").exists())
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["snapshot"]["mode"], "codex-only")
        self.assertIsNone(manifest["snapshot"]["parent_transcript"])
        self.assertEqual(manifest["snapshot"]["parent_completeness"], "partial")
        codex_entries = [
            entry for entry in manifest["files"] if entry["category"] == "codex-rollout"
        ]
        self.assertTrue(all(entry["completeness"] == "partial" for entry in codex_entries))
        self.assertIn("Transcript: partial", proc.stdout)

    def test_session_snapshot_default_discovers_archived_codex_lineage(self):
        thread_id = "019f5001-0000-7111-8222-b15aa4c27782"
        child_thread = "019f5002-1111-7222-8333-c26aa5d38893"
        grandchild_thread = "019f5003-2222-7333-8444-d37bb6e49904"
        codex_home = self.vault / "harness/codex"
        output_root = self.vault / "attachments"
        parent = codex_home / "archived_sessions" / f"rollout-2026-07-11T10-00-00-{thread_id}.jsonl"
        child = (
            codex_home / "sessions/2026/07/11" / f"rollout-2026-07-11T10-05-00-{child_thread}.jsonl"
        )
        grandchild = (
            codex_home
            / "archived_sessions"
            / f"rollout-2026-07-11T10-10-00-{grandchild_thread}.jsonl"
        )
        for destination, fixture in (
            (parent, "codex-archived-parent.jsonl"),
            (child, "codex-active-child.jsonl"),
            (grandchild, "codex-archived-grandchild.jsonl"),
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURES / "session_snapshot" / fixture, destination)

        proc = self.run_oaw(
            "session",
            "snapshot",
            thread_id,
            "--codex-only",
            "--slug",
            "archived lineage",
            "--output-root",
            str(output_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
            env={"CODEX_HOME": str(codex_home)},
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-11-archived-lineage"
        for rollout in (parent, child, grandchild):
            self.assertTrue((snapshot / "codex" / rollout.name).is_file())
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        sources = {entry["source"] for entry in manifest["files"]}
        self.assertTrue({str(parent), str(child), str(grandchild)} <= sources)

        overridden = self.run_oaw(
            "session",
            "snapshot",
            thread_id,
            "--codex-only",
            "--output-root",
            str(self.vault / "override-attachments"),
            env={
                "CODEX_HOME": str(codex_home),
                "OAW_CODEX_SESSIONS_ROOT": str(codex_home / "sessions"),
            },
        )
        self.assertEqual(overridden.returncode, 1)
        self.assertIn(f"Codex rollout not found for thread {thread_id}", overridden.stderr)

    def test_session_snapshot_prefers_active_duplicate_rollout(self):
        thread_id = "019f5004-3333-7444-8555-e48cc7f6bb15"
        codex_home = self.vault / "harness/codex"
        filename = f"rollout-2026-07-11T11-00-00-{thread_id}.jsonl"
        active = codex_home / "sessions/2026/07/11" / filename
        archived = codex_home / "archived_sessions" / filename
        write(active, '{"timestamp":"2026-07-11T11:00:00.000Z","content":"active winner"}\n')
        write(
            archived,
            '{"timestamp":"2026-07-11T11:00:00.000Z","content":"archived duplicate"}\n',
        )
        output_root = self.vault / "attachments"

        proc = self.run_oaw(
            "session",
            "snapshot",
            thread_id,
            "--codex-only",
            "--codex-rollout",
            filename,
            "--slug",
            "active precedence",
            "--output-root",
            str(output_root),
            env={"CODEX_HOME": str(codex_home)},
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-11-active-precedence"
        copied = snapshot / "codex" / filename
        self.assertIn("active winner", copied.read_text(encoding="utf-8"))
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        sources = [
            entry["source"] for entry in manifest["files"] if entry["category"] == "codex-rollout"
        ]
        self.assertEqual(sources, [str(active)])
        self.assertNotIn(str(archived), sources)

    def test_session_snapshot_rejects_duplicate_rollout_filename_within_one_root(self):
        session_id = "019f5005-4444-7555-8666-f59dd806cc26"
        claude_root = self.vault / "harness/claude/projects"
        codex_root = self.vault / "harness/codex/sessions"
        filename = "rollout-2026-07-11T12-00-00-019f5006-5555-7666-8777-a60ee917dd37.jsonl"
        write(
            claude_root / "-tmp-project" / f"{session_id}.jsonl",
            f'{{"timestamp":"2026-07-11T12:00:00.000Z","sessionId":"{session_id}"}}\n',
        )
        first = codex_root / "2026/07/11" / filename
        second = codex_root / "restored" / filename
        write(first, '{"content":"first"}\n')
        write(second, '{"content":"second"}\n')

        proc = self.run_oaw(
            "session",
            "snapshot",
            session_id,
            "--codex-rollout",
            filename,
            "--output-root",
            str(self.vault / "attachments"),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"Codex rollout '{filename}' is not unique", proc.stderr)
        self.assertIn(str(first), proc.stderr)
        self.assertIn(str(second), proc.stderr)

    def test_session_snapshot_codex_only_requires_the_primary_rollout(self):
        thread_id = "019f48d7-39c2-7043-9c19-5a3565995898"
        unrelated_id = "019f48d8-1111-7222-8333-c26aa5d38893"
        codex_root = self.vault / "harness/codex/sessions"
        write(
            codex_root / f"rollout-2026-07-10T00-00-00-{unrelated_id}.jsonl",
            "unrelated marker\n",
        )

        proc = self.run_oaw(
            "session",
            "snapshot",
            thread_id,
            "--codex-only",
            "--grep",
            "unrelated marker",
            "--output-root",
            str(self.vault / "attachments"),
            "--codex-root",
            str(codex_root),
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(f"Codex rollout not found for thread {thread_id}", proc.stderr)

    def test_session_snapshot_codex_only_rejects_non_uuid_thread(self):
        proc = self.run_oaw(
            "session",
            "snapshot",
            "*",
            "--codex-only",
            "--codex-root",
            str(self.vault / "harness/codex/sessions"),
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires a full Codex thread UUID", proc.stderr)

    def test_session_snapshot_rejects_partial_and_complete_on_stderr(self):
        proc = self.run_oaw(
            "session",
            "snapshot",
            "019f48d7-39c2-7043-9c19-5a3565995898",
            "--partial",
            "--complete",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "oaw: --partial and --complete are mutually exclusive\n")

    def test_session_snapshot_accepts_repeated_codex_thread_options(self):
        primary_thread = "019f48d7-39c2-7043-9c19-5a3565995898"
        extra_threads = (
            "019f48d8-1111-7222-8333-c26aa5d38893",
            "019f48d9-2222-7333-8444-d37bb6e49904",
        )
        codex_root = self.vault / "harness/codex/sessions"
        output_root = self.vault / "attachments"
        rollouts = []
        for index, thread_id in enumerate((primary_thread, *extra_threads)):
            rollout = codex_root / f"rollout-{index}-{thread_id}.jsonl"
            write(rollout, "{}\n")
            rollouts.append(rollout)

        proc = self.run_oaw(
            "session",
            "snapshot",
            primary_thread,
            "--codex-only",
            "--partial",
            "--date",
            "2026-07-13",
            "--codex-thread",
            extra_threads[0],
            "--codex-thread",
            extra_threads[1],
            "--output-root",
            str(output_root),
            "--codex-root",
            str(codex_root),
            "--claude-root",
            str(self.vault / "missing-claude"),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-13-019f48d7"
        self.assertTrue(all((snapshot / "codex" / rollout.name).exists() for rollout in rollouts))

    def test_session_snapshot_accepts_other_repeated_options(self):
        session_id = "019f5ac2-efd4-7171-965a-6e6f8d0a1a27"
        fork_session_ids = (
            "019f5ac3-1111-7222-8333-c26aa5d38893",
            "019f5ac4-2222-7333-8444-d37bb6e49904",
        )
        claude_root = self.vault / "harness/claude/projects"
        codex_root = self.vault / "harness/codex/sessions"
        output_root = self.vault / "attachments"
        write(
            claude_root / "-tmp-project" / f"{session_id}.jsonl",
            f'{{"timestamp":"2026-07-13T10:00:00.000Z","sessionId":"{session_id}"}}\n',
        )
        for session in fork_session_ids:
            write(
                claude_root / "-tmp-project" / f"{session}.jsonl",
                f'{{"timestamp":"2026-07-13T10:01:00.000Z","sessionId":"{session}"}}\n',
            )
        explicit_rollouts = (
            codex_root / "rollout-explicit-one.jsonl",
            codex_root / "rollout-explicit-two.jsonl",
        )
        grep_rollouts = (
            codex_root / "rollout-grep-one.jsonl",
            codex_root / "rollout-grep-two.jsonl",
        )
        for rollout in explicit_rollouts:
            write(rollout, "{}\n")
        write(grep_rollouts[0], '{"content":"first repeated grep marker"}\n')
        write(grep_rollouts[1], '{"content":"second repeated grep marker"}\n')

        proc = self.run_oaw(
            "session",
            "snapshot",
            session_id,
            "--slug",
            "repeated options",
            "--codex-rollout",
            str(explicit_rollouts[0]),
            "--codex-rollout",
            str(explicit_rollouts[1]),
            "--claude-session",
            fork_session_ids[0],
            "--claude-session",
            fork_session_ids[1],
            "--grep",
            "first repeated grep marker",
            "--grep",
            "second repeated grep marker",
            "--output-root",
            str(output_root),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-13-repeated-options"
        for session in fork_session_ids:
            self.assertTrue((snapshot / f"claude/forks/parent-{session[:8]}.jsonl").exists())
        for rollout in (*explicit_rollouts, *grep_rollouts):
            self.assertTrue((snapshot / "codex" / rollout.name).exists())

    def test_session_snapshot_does_not_treat_bare_session_id_as_fork_parent(self):
        session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
        unrelated_id = "019f9999-1111-7222-8333-c26aa5d38893"
        claude_root = self.vault / "harness/claude/projects"
        parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
        write(
            parent,
            f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
            f'"content":"payload sessionId: {unrelated_id}"}}\n',
        )
        write(
            claude_root / "-tmp-project" / f"{unrelated_id}.jsonl",
            f'{{"timestamp":"2026-07-08T02:00:00.000Z","sessionId":"{unrelated_id}"}}\n',
        )
        output_root = self.vault / "attachments"

        proc = self.run_oaw(
            "session",
            "snapshot",
            session_id,
            "--output-root",
            str(output_root),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(self.vault / "missing-codex"),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = output_root / "2026-07-08-019f3ed8"
        self.assertFalse((snapshot / "claude/forks/parent-019f9999.jsonl").exists())

    def test_session_snapshot_grep_fails_on_ambiguous_rollouts(self):
        session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
        claude_root = self.vault / "harness/claude/projects"
        codex_root = self.vault / "harness/codex/sessions"
        write(
            claude_root / "-tmp-project" / f"{session_id}.jsonl",
            f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}"}}\n',
        )
        write(codex_root / "2026/07/08/rollout-a.jsonl", "shared marker\n")
        write(codex_root / "2026/07/08/rollout-b.jsonl", "shared marker\n")

        proc = self.run_oaw(
            "session",
            "snapshot",
            session_id,
            "--grep",
            "shared marker",
            "--output-root",
            str(self.vault / "attachments"),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
            "--plugin-data-root",
            str(self.vault / "missing-plugin"),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("matched multiple Codex rollouts", proc.stderr)

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
