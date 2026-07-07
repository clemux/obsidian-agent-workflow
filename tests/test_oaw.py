import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "oaw"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class OawTests(unittest.TestCase):
    def setUp(self):
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

    def tearDown(self):
        self.tmp.cleanup()

    def run_oaw(self, *args, env=None):
        merged = self.env.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, str(BIN), *args],
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_resolve_obs_prefix_to_json(self):
        proc = self.run_oaw("resolve", "--json", "obs:AGT-TSK-obsidian-task-ids")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["id"], "AGT-TSK-obsidian-task-ids")
        self.assertEqual(data["matched_by"], "id")
        self.assertIn("Agents/Tasks", data["relative_path"])

    def test_duplicate_ids_fail(self):
        write(
            self.vault / "Other.md",
            """---
id: AGT-TSK-obsidian-task-ids
---

# Other
""",
        )
        proc = self.run_oaw("resolve", "AGT-TSK-obsidian-task-ids")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not unique", proc.stderr)

    def test_task_start_updates_status_board_and_session(self):
        proc = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started work.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text()
        self.assertIn("status: active", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertLess(board.index("OAW-TSK-cli"), board.index("## Todo"))

    def test_complete_requires_checks(self):
        proc = self.run_oaw("task", "complete", "OAW-TSK-cli", "--note", "Done.")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires --checks", proc.stderr)

    def test_lifecycle_refuses_non_project_task(self):
        proc = self.run_oaw(
            "task",
            "start",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Should fail.",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Projects/*/Tasks", proc.stderr)


if __name__ == "__main__":
    unittest.main()
