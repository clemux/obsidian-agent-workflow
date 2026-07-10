import json
import hashlib
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
        self.assertIn("Reviewed resolver policy.", note)
        self.assertIn("Updated: Agents/Tasks/Resolve vault-wide Obsidian task IDs.md", proc.stdout)

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
        self.assertFalse(
            (self.vault / "Agents/Retrospectives/2026-07-09 duplicate id.md").exists()
        )

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
        proc = self.run_oaw(
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
        self.assertEqual(manifest["artifacts"][0]["path"], artifact_path.relative_to(bundle).as_posix())

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

        failed = self.run_oaw(
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
        retried = self.run_oaw(
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
                self.assertRegex(proc.stderr, r"manifest path (escapes bundle|must be bundle-relative)")

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
            "lookup-thread",
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
        rollout = (
            codex_root
            / "2026/07/09"
            / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
        )
        parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
        subagent = (
            claude_root
            / "-tmp-project"
            / "parent-session/subagents"
            / f"agent-{session_id}.jsonl"
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
        self.assertIn("Updated: Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md", written.stdout)
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

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not allowed with argument", proc.stderr)
        self.assertEqual(before, task_path.read_text(encoding="utf-8"))

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
            task.read_text(encoding="utf-8")
            + "\n```markdown\n[[OAW-TSK-cli]]\n```\n",
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
            claude_root
            / "-tmp-project"
            / session_id
            / "subagents/agent-a8fbf333b1df5e1e9.jsonl",
            '{"timestamp":"2026-07-07T21:19:00.413Z","content":"delegated"}\n',
        )
        write(
            claude_root
            / "-tmp-project"
            / session_id
            / "subagents/nested/agent-nested.jsonl",
            '{"content":"nested delegated transcript"}\n',
        )
        write(
            claude_root / "-tmp-project" / session_id / "tasks/background.output",
            f"background transcript references codex_thread={task_codex_thread}\n",
        )
        write(
            claude_root
            / "-tmp-project"
            / session_id
            / "subagents/workflows/wf-123/run.jsonl",
            '{"content":"workflow run journal"}\n',
        )
        write(
            claude_root
            / "-tmp-project"
            / session_id
            / "workflows/scripts/nightly.md",
            "# Workflow script\n",
        )
        fork_parent = claude_root / "-tmp-project" / f"{fork_session_id}.jsonl"
        write(
            fork_parent,
            f'{{"timestamp":"2026-07-07T22:00:00.000Z","sessionId":"{fork_session_id}",'
            '"content":"forked context"}}\n',
        )
        matching_rollout = (
            codex_root
            / "2026/07/07"
            / f"rollout-2026-07-07T23-19-12-{codex_thread}.jsonl"
        )
        write(matching_rollout, '{"event":"turn_aborted"}\n')
        task_rollout = (
            codex_root
            / "2026/07/07"
            / f"rollout-2026-07-07T23-30-00-{task_codex_thread}.jsonl"
        )
        write(task_rollout, '{"content":"referenced from task output"}\n')
        grep_rollout = (
            codex_root
            / "2026/07/07"
            / "rollout-2026-07-07T23-48-09-019f3e8d-8307-7052-b367-57e78f3316ae.jsonl"
        )
        write(grep_rollout, '{"content":"session-inspection-claude-codex other"}\n')
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
            claude_root
            / "-tmp-project"
            / session_id
            / "subagents/nested/agent-nested.jsonl"
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
        first = self.run_oaw(*base_args)
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
        second = self.run_oaw(*base_args)
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
        codex_root = self.vault / "harness/codex/sessions"
        plugin_root = self.vault / "harness/claude/plugins/data"
        output_root = self.vault / "attachments"
        rollout = (
            codex_root
            / "2026/07/10"
            / f"rollout-2026-07-10T00-00-00-{thread_id}.jsonl"
        )
        child_rollout = (
            codex_root
            / "2026/07/10"
            / f"rollout-2026-07-10T00-05-00-{child_thread}.jsonl"
        )
        write(
            rollout,
            '{"timestamp":"2026-07-10T00:00:00.000Z",'
            f'"content":"codex_thread={child_thread}; plugin task-abcd1234-efgh5678"}}\n',
        )
        write(child_rollout, '{"timestamp":"2026-07-10T00:05:00.000Z"}\n')
        write(
            plugin_root / "example/jobs/task-abcd1234-efgh5678.log",
            "complete\n",
        )

        proc = self.run_oaw(
            "session",
            "snapshot",
            thread_id,
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
        self.assertTrue(
            (snapshot / "plugin-logs/task-abcd1234-efgh5678.log").exists()
        )
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["snapshot"]["mode"], "codex-only")
        self.assertIsNone(manifest["snapshot"]["parent_transcript"])
        self.assertEqual(manifest["snapshot"]["parent_completeness"], "partial")
        codex_entries = [
            entry for entry in manifest["files"] if entry["category"] == "codex-rollout"
        ]
        self.assertTrue(
            all(entry["completeness"] == "partial" for entry in codex_entries)
        )
        self.assertIn("Transcript: partial", proc.stdout)

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


if __name__ == "__main__":
    unittest.main()
