import datetime as dt
import json
import os
import shutil
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
