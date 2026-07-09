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
            self.vault / "Projects/Next steps.md",
            """---
kanban-plugin: board
type: board
id: NEXT-board
aliases:
  - NEXT-board
---

# Next steps board

## Now (current session)

## Next session(s)

- [ ] [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|Resolver CLI]] - finish lifecycle work (OAW-TSK-cli)

## Done

%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
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

    def test_resolve_short_project_alias_to_project_index(self):
        proc = self.run_oaw("resolve", "--json", "obs:CDX")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["id"], "CDX-index")
        self.assertEqual(data["matched_by"], "project-alias")
        self.assertEqual(data["relative_path"], "Projects/Codex Delegation/Index.md")

    def test_resolve_exact_match_wins_over_project_alias(self):
        write(
            self.vault / "Projects/Codex Delegation/Tasks/Short code.md",
            """---
type: task
id: CDX
aliases:
  - CDX
---

# Short code
""",
        )
        proc = self.run_oaw("resolve", "--json", "obs:CDX")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["id"], "CDX")
        self.assertEqual(data["matched_by"], "id")

    def test_resolve_ambiguous_project_alias_fails_with_candidates(self):
        write(
            self.vault / "Projects/Other Codex/Index.md",
            """---
type: project
id: CDX-index
aliases:
  - CDX-index
---

# Other Codex
""",
        )
        proc = self.run_oaw("resolve", "obs:CDX")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not unique", proc.stderr)
        self.assertIn("Projects/Codex Delegation/Index.md (project-alias)", proc.stderr)
        self.assertIn("Projects/Other Codex/Index.md (project-alias)", proc.stderr)

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

    def test_task_note_appends_session_without_status_or_board_change(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before_board = board_path.read_text(encoding="utf-8")

        proc = self.run_oaw(
            "task",
            "note",
            "OAW-TSK-archived",
            "--note",
            "Reviewed independently.",
            "--checks",
            "python -m unittest",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Updated: Projects/Obsidian Agent Workflow/Tasks/Archived task.md", proc.stdout)
        self.assertIn("Status: archived", proc.stdout)
        self.assertIn("Board: unchanged", proc.stdout)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn("status: archived", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertIn("Reviewed independently.; checks: python -m unittest", task)
        self.assertEqual(before_board, board_path.read_text(encoding="utf-8"))

    def test_task_note_requires_session_id_unless_allowed(self):
        env = {
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        proc = self.run_oaw(
            "task",
            "note",
            "OAW-TSK-cli",
            "--note",
            "No session.",
            env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no stable session ID found", proc.stderr)

        allowed = self.run_oaw(
            "task",
            "note",
            "OAW-TSK-cli",
            "--note",
            "Accepted missing session.",
            "--allow-missing-session-id",
            env=env,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("session_id=unavailable", task)

    def test_task_backlog_updates_status_board_and_session(self):
        proc = self.run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text()
        card = "- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli"
        self.assertIn("status: backlog", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertIn("## Backlog", board)
        self.assertEqual(board.count(card), 1)
        self.assertLess(board.index(card), board.index("## Active"))

    def test_task_promote_updates_status_and_moves_card_to_todo(self):
        self.run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
        proc = self.run_oaw("task", "promote", "OAW-TSK-cli", "--note", "Selected next.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text()
        card = "- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli"
        self.assertIn("status: todo", task)
        self.assertEqual(board.count(card), 1)
        self.assertGreater(board.index(card), board.index("## Todo"))
        self.assertLess(board.index(card), board.index("## Done"))

    def test_list_tasks_preserves_archived_rows(self):
        proc = self.run_oaw("list", "--project", "Obsidian Agent Workflow")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OAW-TSK-cli", proc.stdout)
        self.assertIn("OAW-TSK-archived", proc.stdout)

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

    def test_board_add_writes_linked_card_to_column(self):
        proc = self.run_oaw(
            "board",
            "add",
            "--column",
            "Queued",
            "--link",
            "Projects/Obsidian Agent Workflow/Tasks/Archived task.md",
            "--title",
            "Archived task",
            "--why",
            "review later",
            "--id",
            "OAW-TSK-archived",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        board = (self.vault / "Projects/Next steps.md").read_text()
        self.assertIn("## Queued", board)
        self.assertIn(
            "- [ ] [[Projects/Obsidian Agent Workflow/Tasks/Archived task|Archived task]] - review later (OAW-TSK-archived)",
            board,
        )
        self.assertIn("%% kanban:settings", board)

    def test_board_move_preserves_card_text_and_removes_original(self):
        proc = self.run_oaw(
            "board",
            "move",
            "OAW-TSK-cli",
            "--column",
            "Now (current session)",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        board = (self.vault / "Projects/Next steps.md").read_text()
        card = "- [ ] [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|Resolver CLI]] - finish lifecycle work (OAW-TSK-cli)"
        self.assertEqual(board.count(card), 1)
        self.assertLess(board.index(card), board.index("## Next session(s)"))

    def test_board_done_moves_to_done_and_checks_card(self):
        proc = self.run_oaw("board", "done", "OAW-TSK-cli")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        board = (self.vault / "Projects/Next steps.md").read_text()
        card = "- [x] [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|Resolver CLI]] - finish lifecycle work (OAW-TSK-cli)"
        self.assertIn(card, board)
        self.assertGreater(board.index(card), board.index("## Done"))

    def test_board_move_fails_on_ambiguous_match(self):
        path = self.vault / "Projects/Next steps.md"
        path.write_text(
            path.read_text()
            + "- [ ] [[Other|Other]] - duplicate reminder (OAW-TSK-cli)\n",
            encoding="utf-8",
        )
        proc = self.run_oaw("board", "move", "OAW-TSK-cli", "--column", "Queued")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("multiple board cards match", proc.stderr)

    def test_board_ensure_backlog_adds_column_before_todo(self):
        proc = self.run_oaw(
            "board",
            "ensure-backlog",
            "--project",
            "Obsidian Agent Workflow",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Backlog: added", proc.stdout)
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text()
        self.assertLess(board.index("## Backlog"), board.index("## Active"))

    def test_board_ensure_backlog_adds_blank_line_when_appending(self):
        project = self.vault / "Projects/Archive Only"
        write(project / "Board.md", "# Archive board\n\n## Archive\n")

        proc = self.run_oaw(
            "board",
            "ensure-backlog",
            "--project",
            "Archive Only",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        board = (project / "Board.md").read_text(encoding="utf-8")
        self.assertIn("## Archive\n\n## Backlog\n", board)

    def test_board_ensure_backlog_is_idempotent(self):
        self.run_oaw("board", "ensure-backlog", "--project", "Obsidian Agent Workflow")
        proc = self.run_oaw(
            "board",
            "ensure-backlog",
            "--project",
            "Obsidian Agent Workflow",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Backlog: present", proc.stdout)
        board = (self.vault / "Projects/Obsidian Agent Workflow/Board.md").read_text()
        self.assertEqual(board.count("## Backlog"), 1)

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
        self.assertIn("ACCEPT safe.md [export-scope: personal] -> Imports/Handoff/safe.md; dry-run", proc.stdout)
        self.assertIn("ACCEPT legacy.md [tag: safe-export-personal] -> Imports/Handoff/legacy.md; dry-run", proc.stdout)
        self.assertIn("REJECT unsafe.md [missing safe export marker] -> quarantine; dry-run", proc.stdout)
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

        self.assertNotEqual(proc.returncode, 0)
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

    def test_session_snapshot_copies_artifacts_and_writes_manifest(self):
        session_id = "73550790-5af5-4efc-828c-72e6e1053d8f"
        codex_thread = "019f3e73-029f-7ea2-9772-fdfa1e25fb8f"
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
            claude_root
            / "-tmp-project"
            / session_id
            / "subagents/agent-a8fbf333b1df5e1e9.jsonl",
            '{"timestamp":"2026-07-07T21:19:00.413Z","content":"delegated"}\n',
        )
        matching_rollout = (
            codex_root
            / "2026/07/07"
            / f"rollout-2026-07-07T23-19-12-{codex_thread}.jsonl"
        )
        write(matching_rollout, '{"event":"turn_aborted"}\n')
        grep_rollout = (
            codex_root
            / "2026/07/07"
            / "rollout-2026-07-07T23-48-09-019f3e8d-8307-7052-b367-57e78f3316ae.jsonl"
        )
        write(grep_rollout, '{"content":"session-inspection-claude-codex"}\n')
        write(
            plugin_root
            / "codex-openai-codex/state/example/jobs/task-mrb5j4y9-7k3yjy.log",
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
        self.assertTrue((snapshot / "codex" / matching_rollout.name).exists())
        self.assertTrue((snapshot / "codex" / grep_rollout.name).exists())
        self.assertTrue((snapshot / "plugin-logs/task-mrb5j4y9-7k3yjy.log").exists())
        self.assertIn(f"Manifest: {manifest_path}", proc.stdout)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "oaw-session-snapshot-v1")
        self.assertEqual(manifest["session_id"], session_id)
        self.assertEqual(manifest["snapshot"]["parent_completeness"], "partial")
        sources = {entry["source"] for entry in manifest["files"]}
        self.assertIn(str(parent), sources)
        self.assertIn(str(matching_rollout), sources)
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
        first = self.run_oaw(*base_args)
        self.assertEqual(first.returncode, 0, first.stderr)
        snapshot = output_root / "2026-07-08-refresh-test"
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
        second = self.run_oaw(*base_args)
        self.assertEqual(second.returncode, 0, second.stderr)

        parent_copy = snapshot / "claude/parent-019f3ed8-PARTIAL.jsonl"
        self.assertIn("second", parent_copy.read_text(encoding="utf-8"))
        self.assertTrue((snapshot / "claude/agent-new.jsonl").exists())
        self.assertFalse(stale.exists())
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        destinations = {entry["destination"] for entry in manifest["files"]}
        self.assertIn("claude/agent-new.jsonl", destinations)

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


if __name__ == "__main__":
    unittest.main()
