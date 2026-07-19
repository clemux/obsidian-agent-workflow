import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from oaw import cli, lifecycle, resolver
from oaw.errors import OawError

from .assertions import Assertions

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "oaw"
FIXTURES = ROOT / "tests" / "fixtures"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
        merged = self.env.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, str(BIN), *args],
            env=merged,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_record_for(self, session_id: str) -> Path:
        for path in (self.vault / "Agents/Runs").glob("*.md"):
            if f'agent_session_id: "{session_id}"' in path.read_text(encoding="utf-8"):
                return path
        raise AssertionError(f"run record not found for {session_id}")

    def test_cli_main_accepts_argv_and_returns_status_code(self, monkeypatch):
        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        stdout = StringIO()

        with redirect_stdout(stdout):
            returncode = cli.main(["resolve", "--path", "OAW-TSK-cli"])

        self.assertEqual(returncode, 0)
        self.assertEqual(
            stdout.getvalue(),
            f"{self.vault / 'Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md'}\n",
        )

    def test_cli_main_translates_usage_exit_to_status_code(self):
        stderr = StringIO()

        with redirect_stderr(stderr):
            returncode = cli.main([])

        self.assertEqual(returncode, 2)
        self.assertIn("the following arguments are required: command", stderr.getvalue())

    def test_no_command_is_usage_error_on_stderr(self):
        proc = self.run_oaw()

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("usage: oaw", proc.stderr)
        self.assertIn("the following arguments are required: command", proc.stderr)

    def test_resolve_obs_prefix_to_json(self):
        proc = self.run_oaw("resolve", "--json", "obs:AGT-TSK-obsidian-task-ids")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["id"], "AGT-TSK-obsidian-task-ids")
        self.assertEqual(data["matched_by"], "id")
        self.assertIn("Agents/Tasks", data["relative_path"])

    def test_project_create_renders_native_template_and_frontmatter(self):
        proc = self.run_oaw(
            "project",
            "create",
            "--name",
            "Agent Tooling",
            "--alias",
            "AGT",
            "--goal",
            "Maintain shared cross-harness skills.",
            "--repo",
            "~/dev/agent-skills:main",
            "--tag",
            "agent-tooling",
            "--tag",
            "workflow",
            "--tag",
            "workflow",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Created: Projects/Agent Tooling/Index.md\nID: AGT-index\nStatus: active\n",
        )
        note = (self.vault / "Projects/Agent Tooling/Index.md").read_text(encoding="utf-8")
        self.assertIn('project: "agent-tooling"', note)
        self.assertIn('repo: "~/dev/agent-skills:main"', note)
        self.assertIn('id: "AGT-index"', note)
        self.assertIn('  - "AGT-index"', note)
        self.assertIn('  - "projects"\n  - "agent-tooling"\n  - "workflow"', note)
        self.assertIn('  - "test-thread"', note)
        self.assertIn("# Agent Tooling", note)
        self.assertIn("## Goal\n\nMaintain shared cross-harness skills.", note)
        self.assertIn("- Status: active", note)
        self.assertIn("- Repo: ~/dev/agent-skills:main", note)
        self.assertIn("- Next action: create or select the first task when work is selected.", note)
        self.assertIn("![[Templates/Project workspace.base#Work queue]]", note)
        self.assertNotIn("{{", note)

    def test_project_create_omits_optional_repo_and_missing_session_provenance(self):
        proc = self.run_oaw(
            "project",
            "create",
            "--name",
            "Notebook",
            "--alias",
            "NB",
            "--goal",
            "Keep useful notes.",
            "--allow-missing-session-id",
            env={
                "CODEX_THREAD_ID": "",
                "CLAUDE_SESSION_ID": "",
                "CLAUDE_CODE_SESSION_ID": "",
                "OPENCODE_SESSION_ID": "",
                "GEMINI_SESSION_ID": "",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Projects/Notebook/Index.md").read_text(encoding="utf-8")
        self.assertNotIn("repo:", note.lower())
        self.assertNotIn("session-ids:", note)

    def test_project_create_supports_custom_template_and_native_date(self):
        write(
            self.vault / "Templates/Custom project.md",
            """---
created: {{date}}
---

# Workspace - {{title}}

## Goal

Placeholder.

## Current state

Placeholder.

## Custom section

Retained.
""",
        )
        proc = self.run_oaw(
            "project",
            "create",
            "--name",
            "Custom Project",
            "--alias",
            "CP",
            "--goal",
            "Use the custom shape.",
            "--template",
            "Templates/Custom project.md",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = (self.vault / "Projects/Custom Project/Index.md").read_text(encoding="utf-8")
        self.assertIn("# Workspace - Custom Project", note)
        self.assertIn("## Custom section\n\nRetained.", note)
        self.assertRegex(note, r"created: \d{4}-\d{2}-\d{2}")
        self.assertNotIn("{{date}}", note)

    def test_project_create_rejects_unsafe_inputs_without_writing(self):
        cases = [
            (("--name", "../Unsafe", "--alias", "OK", "--goal", "Goal"), "--name"),
            (("--name", "Unsafe", "--alias", "bad", "--goal", "Goal"), "--alias"),
            (("--name", "Unsafe", "--alias", "OK", "--goal", "line\nbreak"), "--goal"),
            (
                (
                    "--name",
                    "Unsafe",
                    "--alias",
                    "OK",
                    "--goal",
                    "Goal",
                    "--tag",
                    "bad tag",
                ),
                "--tag",
            ),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                proc = self.run_oaw("project", "create", *arguments)
                self.assertEqual(proc.returncode, 1)
                self.assertIn(expected, proc.stderr)
                self.assertFalse((self.vault / "Projects/Unsafe").exists())

    def test_project_create_rejects_malformed_or_unresolved_templates(self):
        template = self.vault / "Templates/Small project index.md"
        variants = [
            ("# {{title}}", "# No title token", "exactly one H1"),
            ("## Goal", "### Goal", "exactly one '## Goal'"),
            ("## Agent notes", "## Agent notes\n\n{{unknown}}", "unresolved template"),
        ]
        original = template.read_text(encoding="utf-8")
        for old, new, expected in variants:
            with self.subTest(expected=expected):
                template.write_text(original.replace(old, new), encoding="utf-8")
                proc = self.run_oaw(
                    "project",
                    "create",
                    "--name",
                    "Broken Project",
                    "--alias",
                    "BP",
                    "--goal",
                    "Goal",
                )
                self.assertEqual(proc.returncode, 1)
                self.assertIn(expected, proc.stderr)
                self.assertFalse((self.vault / "Projects/Broken Project").exists())
        template.write_text(original, encoding="utf-8")

    def test_project_create_rejects_duplicate_id_and_existing_folder(self):
        duplicate = self.run_oaw(
            "project",
            "create",
            "--name",
            "Another OAW",
            "--alias",
            "OAW",
            "--goal",
            "Goal",
        )
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("id 'OAW-index' is already in use", duplicate.stderr)
        self.assertFalse((self.vault / "Projects/Another OAW").exists())

        (self.vault / "Projects/Existing").mkdir()
        existing = self.run_oaw(
            "project",
            "create",
            "--name",
            "Existing",
            "--alias",
            "EX",
            "--goal",
            "Goal",
        )
        self.assertEqual(existing.returncode, 1)
        self.assertIn("project folder already exists", existing.stderr)

    def test_project_create_removes_empty_folder_after_transaction_failure(self, monkeypatch):
        class FailingTransaction:
            def __init__(self):
                self.destination = None

            def stage(self, path, _text):
                self.destination = path

            def commit(self):
                assert self.destination is not None
                self.destination.parent.mkdir(parents=True)
                raise OawError("simulated transaction failure")

        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        monkeypatch.setenv("CODEX_THREAD_ID", "test-thread")
        monkeypatch.setattr(lifecycle, "VaultTransaction", FailingTransaction)
        result = cli.main(
            [
                "project",
                "create",
                "--name",
                "Rollback Project",
                "--alias",
                "RP",
                "--goal",
                "Verify cleanup.",
            ]
        )
        self.assertEqual(result, 1)
        self.assertFalse((self.vault / "Projects/Rollback Project").exists())

    def test_research_scaffold_renders_template_with_audience_boundary(self):
        proc = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "architecture/provider-choice",
            "--title",
            "Provider choice",
            "--date",
            "2026-07-12",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Created: Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Prompt.md\n"
            "Synthesis: Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Synthesis.md\n"
            "Base: Bases/Research packet.base\n"
            "Template: Templates/Research packet.md\n"
            "Deep research prompt: self-contained provider-visible body\n",
        )
        prompt = (
            self.vault
            / "Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Prompt.md"
        ).read_text(encoding="utf-8")
        local, provider = prompt.split("## Deep research prompt", 1)
        self.assertIn("project: obsidian-agent-workflow", local)
        self.assertIn("track: architecture/provider-choice", local)
        self.assertIn("created: 2026-07-12", local)
        self.assertIn("# Prompt - Provider choice", local)
        self.assertIn("Research Provider choice", provider)
        self.assertNotIn("obsidian-agent-workflow", provider)
        self.assertNotIn("architecture/provider-choice", provider)
        self.assertIn("```text\nResearch Provider choice", provider)
        synthesis = (
            self.vault
            / "Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Synthesis.md"
        )
        synthesis_text = synthesis.read_text(encoding="utf-8")
        self.assertIn("type: research-synthesis", synthesis_text)
        self.assertIn("![[Bases/Research packet.base#Source reports]]", synthesis_text)
        self.assertTrue((self.vault / "Bases/Research packet.base").is_file())

    def test_research_scaffold_refuses_existing_prompt_without_force(self):
        args = (
            "research",
            "scaffold",
            "--project",
            "Obsidian Agent Workflow",
            "--track",
            "provider-choice",
            "--title",
            "Provider choice",
        )
        self.assertEqual(self.run_oaw(*args).returncode, 0)
        proc = self.run_oaw(*args)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("research prompt already exists", proc.stderr)

    def test_research_scaffold_rejects_template_that_leaks_local_metadata(self):
        template = self.vault / "Templates/Research packet.md"
        template.write_text(
            template.read_text(encoding="utf-8") + "\nLocal track: {{track}}\n",
            encoding="utf-8",
        )
        proc = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "Obsidian Agent Workflow",
            "--track",
            "provider-choice",
            "--title",
            "Provider choice",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("places local-only fields", proc.stderr)

    def test_research_scaffold_requires_exact_provider_boundary_heading(self):
        template = self.vault / "Templates/Research packet.md"
        template.write_text(
            template.read_text(encoding="utf-8").replace(
                "## Deep research prompt", "### Deep research prompt"
            ),
            encoding="utf-8",
        )
        proc = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "Obsidian Agent Workflow",
            "--track",
            "provider-choice",
            "--title",
            "Provider choice",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("must contain exactly one '## Deep research prompt' heading", proc.stderr)

    def test_research_scaffold_rejects_rendered_metadata_after_boundary(self):
        template = self.vault / "Templates/Research packet.md"
        template.write_text(
            template.read_text(encoding="utf-8").replace(
                "Research {{title}}", "Research obsidian-agent-workflow"
            ),
            encoding="utf-8",
        )
        proc = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "Obsidian Agent Workflow",
            "--track",
            "provider-choice",
            "--title",
            "Provider choice",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("rendered research prompt places local-only metadata", proc.stderr)
        self.assertIn("project", proc.stderr)

    def test_research_scaffold_allows_short_metadata_characters_inside_words(self):
        write(
            self.vault / "Projects/X/Index.md",
            """---
type: project
id: X-index
---

# X
""",
        )
        proc = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "X",
            "--track",
            "a/b",
            "--title",
            "T",
            "--date",
            "2026-07-12",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        prompt = self.vault / "Projects/X/Research/a/b/Prompt.md"
        self.assertTrue(prompt.is_file())
        self.assertIn("expected output format", prompt.read_text(encoding="utf-8"))

    def test_research_scaffold_force_preserves_existing_synthesis(self):
        args = (
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "topic",
            "--title",
            "Topic",
            "--date",
            "2026-07-12",
        )
        self.assertEqual(self.run_oaw(*args).returncode, 0)
        synthesis = self.vault / "Projects/Obsidian Agent Workflow/Research/topic/Synthesis.md"
        synthesis.write_text("irreplaceable synthesis\n", encoding="utf-8")
        proc = self.run_oaw(*args, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(synthesis.read_text(encoding="utf-8"), "irreplaceable synthesis\n")

    def test_research_start_creates_one_running_result_and_updates_prompt(self):
        scaffold = self.run_oaw(
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "topic",
            "--title",
            "Topic",
            "--date",
            "2026-07-12",
        )
        self.assertEqual(scaffold.returncode, 0, scaffold.stderr)
        proc = self.run_oaw(
            "research",
            "start",
            "--project",
            "obs:OAW",
            "--track",
            "topic",
            "--source",
            "ChatGPT Pro",
            "--url",
            "https://chatgpt.com/share/example",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        packet = self.vault / "Projects/Obsidian Agent Workflow/Research/topic"
        results = sorted(packet.glob("Results - *.md"))
        self.assertEqual([path.name for path in results], ["Results - ChatGPT Pro.md"])
        result = results[0].read_text(encoding="utf-8")
        self.assertIn('source: "ChatGPT Pro"', result)
        self.assertIn('url: "https://chatgpt.com/share/example"', result)
        self.assertIn("status: running", result)
        prompt = (packet / "Prompt.md").read_text(encoding="utf-8")
        self.assertIn("- ChatGPT Pro: [running](https://chatgpt.com/share/example)", prompt)

    def test_research_start_rejects_unsafe_duplicate_and_non_http_sources(self):
        self.assertEqual(
            self.run_oaw(
                "research",
                "scaffold",
                "--project",
                "obs:OAW",
                "--track",
                "topic",
                "--title",
                "Topic",
            ).returncode,
            0,
        )
        common = ("research", "start", "--project", "obs:OAW", "--track", "topic")
        unsafe = self.run_oaw(*common, "--source", "../ChatGPT", "--url", "https://example.com")
        self.assertEqual(unsafe.returncode, 1)
        self.assertIn("safe --source label", unsafe.stderr)
        reserved = self.run_oaw(*common, "--source", "ChatGPT: Pro", "--url", "https://example.com")
        self.assertEqual(reserved.returncode, 1)
        self.assertIn("safe --source label", reserved.stderr)
        bad_url = self.run_oaw(*common, "--source", "ChatGPT", "--url", "file:///tmp/report")
        self.assertEqual(bad_url.returncode, 1)
        self.assertIn("HTTP(S)", bad_url.stderr)
        first = self.run_oaw(*common, "--source", "ChatGPT", "--url", "https://example.com")
        self.assertEqual(first.returncode, 0, first.stderr)
        duplicate = self.run_oaw(*common, "--source", "ChatGPT", "--url", "https://other.test")
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("source already exists", duplicate.stderr)

    def test_research_start_rejects_malformed_packet_without_partial_write(self):
        packet = self.vault / "Projects/Obsidian Agent Workflow/Research/topic"
        write(packet / "Prompt.md", "---\ntitle: Topic\n---\n\n## Running research sessions\n")
        proc = self.run_oaw(
            "research",
            "start",
            "--project",
            "obs:OAW",
            "--track",
            "topic",
            "--source",
            "ChatGPT",
            "--url",
            "https://example.com",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertFalse((packet / "Results - ChatGPT.md").exists())
        self.assertFalse((packet / "Synthesis.md").exists())

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

    def test_resolve_prefilters_unrelated_frontmatter_before_parsing(self, monkeypatch):
        for index in range(50):
            write(
                self.vault / f"Noise/{index}.md",
                f"""---
id: NOISE-{index}
aliases:
  - OTHER-{index}
---

# PERF-TARGET body decoy
""",
            )
        write(
            self.vault / "Target.md",
            """---
id: PERF-TARGET
aliases:
  - PERF-ALIAS
---

# Performance target
""",
        )
        original = resolver.parse_frontmatter
        parsed: list[str] = []

        def recording_parse(frontmatter: str):
            parsed.append(frontmatter)
            return original(frontmatter)

        monkeypatch.setattr(resolver, "parse_frontmatter", recording_parse)

        match = cli.resolve_id("PERF-TARGET", self.vault)

        self.assertEqual(match.title, "Performance target")
        self.assertEqual(len(parsed), 1)
        self.assertIn("id: PERF-TARGET", parsed[0])

    def test_task_start_updates_status_and_session_without_touching_legacy_board(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        proc = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started work.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("status: active", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertIn('session-ids:\n  - "test-thread"\n', task)
        self.assertNotIn("Board:", proc.stdout)
        self.assertEqual(board_before, board_path.read_bytes())

    def test_task_start_is_idempotent_for_same_identity(self):
        first = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "First start.")
        second = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Refresh start.")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        records = list((self.vault / "Agents/Runs").glob("*.md"))
        self.assertEqual(len(records), 1)
        text = records[0].read_text(encoding="utf-8")
        self.assertIn(" — start — First start.", text)
        self.assertIn(" — refresh — Refresh start.", text)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("execution: agent", task)

    def test_claude_refresh_uses_provider_and_session_identity_and_preserves_env(self):
        cleared = {
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        first = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Claude Code env start.",
            env={**cleared, "CLAUDE_CODE_SESSION_ID": "shared-claude-session"},
        )
        second = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Claude env refresh.",
            env={**cleared, "CLAUDE_SESSION_ID": "shared-claude-session"},
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        records = list((self.vault / "Agents/Runs").glob("*.md"))
        self.assertEqual(len(records), 1)
        run_text = records[0].read_text(encoding="utf-8")
        self.assertIn("agent_session_env: CLAUDE_CODE_SESSION_ID", run_text)
        self.assertNotIn("agent_session_env: CLAUDE_SESSION_ID\n", run_text)
        self.assertIn(" — start — Claude Code env start.", run_text)
        self.assertIn(" — refresh — Claude env refresh.", run_text)
        task_text = (
            self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`CLAUDE_CODE_SESSION_ID=shared-claude-session`", task_text)
        self.assertIn("`CLAUDE_SESSION_ID=shared-claude-session`", task_text)

    @pytest.mark.parametrize(
        ("command", "extra"),
        [
            ("start", []),
            ("review", ["--checks", "pytest"]),
            ("complete", ["--checks", "pytest"]),
        ],
    )
    def test_lifecycle_rejects_corrupt_deterministic_run_before_writing(self, command, extra):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        run_path.write_text(
            run_path.read_text(encoding="utf-8").replace(
                "agent_session_env: CODEX_THREAD_ID",
                "agent_session_env: CLAUDE_SESSION_ID",
            ),
            encoding="utf-8",
        )
        before = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }

        result = self.run_oaw(
            "task",
            command,
            "OAW-TSK-cli",
            "--note",
            "Must reject corrupt record.",
            *extra,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("run record validation failed", result.stderr)
        self.assertIn("unsupported provider/session environment", result.stderr)
        after = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

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
    def test_refresh_rejects_misplaced_deterministic_task_scope(self, old, new, expected):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        run_path.write_text(
            run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
        )
        before = run_path.read_bytes()

        refreshed = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Must not refresh.")

        self.assertEqual(refreshed.returncode, 1)
        self.assertIn(expected, refreshed.stderr)
        self.assertEqual(before, run_path.read_bytes())

    def test_multiple_sessions_run_same_task_and_review_conflicts_even_when_stale(self):
        first = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "First session.")
        second = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Second session.",
            env={"CODEX_THREAD_ID": "other-thread"},
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(list((self.vault / "Agents/Runs").glob("*.md"))), 2)

        other = self.run_record_for("other-thread")
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
        review = self.run_oaw(
            "task",
            "review",
            "OAW-TSK-cli",
            "--note",
            "Review handoff.",
            "--checks",
            "pytest",
        )
        self.assertEqual(review.returncode, 1)
        self.assertIn("another session remains running", review.stderr)
        self.assertIn(
            "status: active",
            (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text(),
        )
        listed = self.run_oaw("run", "list", "--json")
        rows = json.loads(listed.stdout)
        stale = next(row for row in rows if row["agent_session_id"] == "other-thread")
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["run_state"], "running")

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
    def test_transition_fails_closed_on_corrupt_sibling_record(self, command, old, new, expected):
        current = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Current.")
        sibling = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Sibling.",
            env={"CODEX_THREAD_ID": "other-thread"},
        )
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(sibling.returncode, 0, sibling.stderr)
        sibling_path = self.run_record_for("other-thread")
        sibling_path.write_text(
            sibling_path.read_text(encoding="utf-8").replace(old, new, 1),
            encoding="utf-8",
        )
        before = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }

        result = self.run_oaw(
            "task",
            command,
            "OAW-TSK-cli",
            "--note",
            "Must fail closed.",
            "--checks",
            "pytest",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(expected, result.stderr)
        after = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_legacy_direct_complete_refuses_another_running_session(self):
        started = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Other session owns the task.",
            env={"CODEX_THREAD_ID": "other-thread"},
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        before = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        completed = self.run_oaw(
            "task",
            "complete",
            "OAW-TSK-cli",
            "--note",
            "Cannot bypass concurrency.",
            "--checks",
            "pytest",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("another session remains running", completed.stderr)
        after = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_cross_task_run_does_not_block_one_shot_completion(self):
        started = self.run_oaw(
            "task",
            "start",
            "OAW-TSK-cli",
            "--note",
            "Unrelated task run.",
            env={"CODEX_THREAD_ID": "other-thread"},
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        write(
            self.vault / "Tasks/Independent.md",
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
        completed = self.run_oaw(
            "task",
            "complete",
            "ROOT-TSK-independent",
            "--note",
            "Completed independently.",
            "--checks",
            "pytest",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task = (self.vault / "Tasks/Independent.md").read_text(encoding="utf-8")
        self.assertIn("status: done", task)
        self.assertNotIn("execution:", task)
        run = self.run_record_for("test-thread").read_text(encoding="utf-8")
        self.assertIn("run_state: completed", run)
        self.assertIn("verification: pytest", run)

    def test_pause_changes_only_callers_run_and_preserves_task_status(self):
        for session in ("test-thread", "other-thread"):
            started = self.run_oaw(
                "task",
                "start",
                "OAW-TSK-cli",
                "--note",
                f"Start {session}.",
                env={"CODEX_THREAD_ID": session},
            )
            self.assertEqual(started.returncode, 0, started.stderr)
        paused = self.run_oaw("task", "pause", "OAW-TSK-cli", "--note", "Pausing caller.")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        self.assertIn("run_state: paused", self.run_record_for("test-thread").read_text())
        self.assertIn("run_state: running", self.run_record_for("other-thread").read_text())
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("status: active", task)

    @pytest.mark.parametrize("execution", [None, "invalid"])
    def test_pause_requires_explicit_agent_or_hybrid_execution(self, execution):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        if execution:
            task_path.write_text(
                task_path.read_text(encoding="utf-8").replace(
                    "status: todo", f"status: todo\nexecution: {execution}"
                ),
                encoding="utf-8",
            )
        before = task_path.read_bytes()
        paused = self.run_oaw("task", "pause", "OAW-TSK-cli", "--note", "No run.")
        self.assertEqual(paused.returncode, 1)
        self.assertIn("requires execution: agent or hybrid", paused.stderr)
        self.assertEqual(before, task_path.read_bytes())

    def test_human_task_refuses_agent_start_before_any_write(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: todo", "status: todo\nexecution: human"
            ),
            encoding="utf-8",
        )
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before = (task_path.read_bytes(), board_path.read_bytes())
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Must refuse.")
        self.assertEqual(started.returncode, 1)
        self.assertIn("managed in Obsidian UI", started.stderr)
        self.assertEqual(before, (task_path.read_bytes(), board_path.read_bytes()))
        self.assertFalse((self.vault / "Agents/Runs").exists())

    @pytest.mark.parametrize("command", ["backlog", "promote"])
    def test_queue_transition_refuses_while_any_run_is_running(self, command):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Run remains active.")
        self.assertEqual(started.returncode, 0, started.stderr)
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        run_path = self.run_record_for("test-thread")
        before = (task_path.read_bytes(), board_path.read_bytes(), run_path.read_bytes())

        moved = self.run_oaw("task", command, "OAW-TSK-cli", "--note", "Must wait.")

        self.assertEqual(moved.returncode, 1)
        self.assertIn("while an agent run is running", moved.stderr)
        self.assertEqual(
            before, (task_path.read_bytes(), board_path.read_bytes(), run_path.read_bytes())
        )

    def test_task_note_refreshes_only_existing_matching_running_run(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        noted = self.run_oaw(
            "task", "note", "OAW-TSK-cli", "--note", "Progress.", "--checks", "pytest"
        )
        self.assertEqual(noted.returncode, 0, noted.stderr)
        run_text = self.run_record_for("test-thread").read_text(encoding="utf-8")
        self.assertIn(" — note — Progress. — verification: pytest", run_text)

        write(
            self.vault / "Tasks/Unstarted.md",
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
        count = len(list((self.vault / "Agents/Runs").glob("*.md")))
        unstarted = self.run_oaw("task", "note", "ROOT-TSK-unstarted", "--note", "Trace only.")
        self.assertEqual(unstarted.returncode, 0, unstarted.stderr)
        self.assertEqual(len(list((self.vault / "Agents/Runs").glob("*.md"))), count)

    def test_run_close_records_closer_without_changing_task(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_before = task_path.read_bytes()
        run_path = self.run_record_for("test-thread")
        identifier = run_path.stem

        closed = self.run_oaw(
            "run",
            "close",
            identifier,
            "--reason",
            "true",
            env={"CODEX_THREAD_ID": "closer-thread"},
        )

        self.assertEqual(closed.returncode, 0, closed.stderr)
        text = run_path.read_text(encoding="utf-8")
        self.assertIn('agent_session_id: "test-thread"', text)
        self.assertIn('  - "test-thread"', text)
        self.assertIn('  - "closer-thread"', text)
        self.assertIn("run_state: closed", text)
        self.assertIn('ended_reason: "true"', text)
        self.assertEqual(task_before, task_path.read_bytes())

    def test_run_close_rejects_an_unsafe_run_id_without_writes(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        before = task_path.read_bytes()

        closed = self.run_oaw(
            "run",
            "close",
            "../../Projects/Obsidian Agent Workflow/Tasks/Resolver CLI",
            "--reason",
            "unsafe lookup",
        )

        self.assertEqual(closed.returncode, 1)
        self.assertIn("invalid run id", closed.stderr)
        self.assertEqual(before, task_path.read_bytes())

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
    def test_run_close_rejects_forged_identity_or_task_scope(self, old, new, expected):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        run_path.write_text(
            run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
        )
        before = run_path.read_bytes()

        closed = self.run_oaw(
            "run",
            "close",
            run_path.stem,
            "--reason",
            "Must reject forged record.",
            env={"CODEX_THREAD_ID": "closer-thread"},
        )

        self.assertEqual(closed.returncode, 1)
        self.assertIn(expected, closed.stderr)
        self.assertEqual(before, run_path.read_bytes())

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
    def test_run_close_rejects_malformed_mutable_schema_without_writes(self, old, new, expected):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        run_path.write_text(
            run_path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
        )
        before = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }

        closed = self.run_oaw(
            "run",
            "close",
            run_path.stem,
            "--reason",
            "must not mutate",
            env={"CODEX_THREAD_ID": "closer-thread"},
        )

        self.assertEqual(closed.returncode, 1)
        self.assertIn("run record validation failed", closed.stderr)
        self.assertIn(expected, closed.stderr)
        after = {
            path.relative_to(self.vault): path.read_bytes()
            for path in self.vault.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    @pytest.mark.parametrize("operation", ["list", "audit", "close", "start", "create-start"])
    def test_symlinked_run_directory_fails_closed_without_writes(self, operation):
        with tempfile.TemporaryDirectory() as outside_raw:
            outside = Path(outside_raw)
            marker = outside / "outside-marker.txt"
            marker.write_text("outside must remain untouched\n", encoding="utf-8")
            registry = self.vault / "Agents/Runs"
            registry.symlink_to(outside, target_is_directory=True)
            before_vault = snapshot_tree_without_following_symlinks(self.vault)
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

            result = self.run_oaw(*arguments)

            self.assertEqual(result.returncode, 1)
            self.assertIn("run registry directory must not be a symlink", result.stderr)
            self.assertEqual(before_vault, snapshot_tree_without_following_symlinks(self.vault))
            self.assertEqual(before_outside, snapshot_tree_without_following_symlinks(outside))

    @pytest.mark.parametrize("operation", ["list", "audit", "close", "start", "create-start"])
    def test_canonical_run_symlink_entry_fails_closed_without_writes(self, operation):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        canonical = self.run_record_for("test-thread")
        with tempfile.TemporaryDirectory() as outside_raw:
            outside = Path(outside_raw)
            outside_run = outside / canonical.name
            outside_run.write_bytes(canonical.read_bytes())
            canonical.unlink()
            canonical.symlink_to(outside_run)
            before_vault = snapshot_tree_without_following_symlinks(self.vault)
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

            result = self.run_oaw(*arguments)

            self.assertEqual(result.returncode, 1)
            if operation == "audit":
                self.assertIn("noncanonical registry artifact", result.stdout)
            else:
                self.assertIn("run registry contains symlink entries", result.stderr)
            self.assertEqual(before_vault, snapshot_tree_without_following_symlinks(self.vault))
            self.assertEqual(before_outside, snapshot_tree_without_following_symlinks(outside))

    def test_run_audit_reports_clean_registry(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 0, audited.stderr)
        self.assertEqual(audited.stdout, "Run audit: clean\n")

    def test_run_audit_reports_id_and_timestamp_inconsistencies(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
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

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 1)
        self.assertIn("id/filename mismatch", audited.stdout)
        self.assertIn("run-id/identity mismatch", audited.stdout)
        self.assertIn("malformed last_event_at", audited.stdout)

    def test_run_audit_reports_malformed_schema_and_terminal_metadata(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        run_path.write_text(
            run_path.read_text(encoding="utf-8")
            .replace("type: agent-run", "type: other")
            .replace('  - "test-thread"', '  - "other-thread"\n  - "other-thread"')
            .replace("run_state: running", "run_state: completed"),
            encoding="utf-8",
        )

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 1)
        self.assertIn("malformed type other", audited.stdout)
        self.assertIn("duplicate session-ids", audited.stdout)
        self.assertIn("agent_session_id missing from session-ids", audited.stdout)
        self.assertIn("terminal run missing valid ended_at", audited.stdout)
        self.assertIn("terminal run missing ended_reason", audited.stdout)

    def test_run_audit_reports_record_under_noncanonical_filename(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        canonical = self.run_record_for("test-thread")
        write(self.vault / "Agents/Runs/misplaced.md", canonical.read_text(encoding="utf-8"))

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 1)
        self.assertIn("misplaced.md: noncanonical registry artifact", audited.stdout)

    def test_run_audit_reports_nested_and_non_markdown_artifacts(self):
        directory = self.vault / "Agents/Runs"
        write(directory / "README.txt", "not a run\n")
        write(directory / "nested/AGT-RUN-hidden.md", "hidden run\n")

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 1)
        self.assertIn("README.txt: noncanonical registry artifact", audited.stdout)
        self.assertIn("nested/AGT-RUN-hidden.md: noncanonical registry artifact", audited.stdout)

    def test_run_audit_rejects_extra_keys_project_shape_and_end_ordering(self):
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Start.")
        self.assertEqual(started.returncode, 0, started.stderr)
        completed = self.run_oaw(
            "task",
            "complete",
            "OAW-TSK-cli",
            "--note",
            "Complete.",
            "--checks",
            "pytest",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_path = self.run_record_for("test-thread")
        text = run_path.read_text(encoding="utf-8")
        ended_line = next(line for line in text.splitlines() if line.startswith("ended_at:"))
        run_path.write_text(
            text.replace('project: "obsidian-agent-workflow"', "project: [invalid]")
            .replace("run_state: completed", "unexpected: value\nrun_state: completed")
            .replace(ended_line, 'ended_at: "2000-01-01T00:00:00Z"'),
            encoding="utf-8",
        )

        audited = self.run_oaw("run", "audit")

        self.assertEqual(audited.returncode, 1)
        self.assertIn("noncanonical schema keys: unexpected", audited.stdout)
        self.assertIn("malformed project", audited.stdout)
        self.assertIn("ended_at precedes started_at", audited.stdout)
        self.assertIn("ended_at precedes last_event_at", audited.stdout)

    def test_task_review_requires_checks(self):
        missing = self.run_oaw("task", "review", "OAW-TSK-cli", "--note", "Ready for review.")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("--checks", missing.stderr)

    def test_review_does_not_default_missing_execution(self):
        started = self.run_oaw(
            "task", "start", "OAW-TSK-cli", "--note", "Established historical run."
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace("execution: agent\n", ""),
            encoding="utf-8",
        )

        reviewed = self.run_oaw(
            "task",
            "review",
            "OAW-TSK-cli",
            "--note",
            "Historical run ready.",
            "--checks",
            "pytest",
        )

        self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
        self.assertNotIn("execution:", task_path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        ("note", "checks", "expected"),
        [
            ("", "pytest", "non-empty --note"),
            (" \t", "pytest", "non-empty --note"),
            ("Ready for review.", "", "non-empty --checks"),
            ("Ready for review.", " \t", "non-empty --checks"),
        ],
    )
    def test_task_review_rejects_blank_note_or_checks_without_writes(self, note, checks, expected):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before = (task_path.read_bytes(), board_path.read_bytes())
        proc = self.run_oaw("task", "review", "OAW-TSK-cli", "--note", note, "--checks", checks)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(expected, proc.stderr)
        self.assertEqual(before, (task_path.read_bytes(), board_path.read_bytes()))

    def test_task_review_domain_rejects_blank_values_before_transaction(self, monkeypatch):
        match = resolver.resolve_id("OAW-TSK-cli", self.vault)

        class UnexpectedTransaction:
            def __init__(self):
                raise AssertionError("validation must happen before transaction construction")

        monkeypatch.setattr(lifecycle, "VaultTransaction", UnexpectedTransaction)
        for note, checks, expected in (
            ("", "pytest", "non-empty --note"),
            (" \t", "pytest", "non-empty --note"),
            ("Ready for review.", "", "non-empty --checks"),
            ("Ready for review.", " \t", "non-empty --checks"),
        ):
            with pytest.raises(OawError, match=expected):
                lifecycle.update_task(match, self.vault, "review", note, checks, allow_missing=True)

    @pytest.mark.parametrize("initial_status", ["backlog", "todo", "active", "review", "done"])
    def test_task_review_accepts_every_lifecycle_source_status(self, initial_status):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Established caller run.")
        self.assertEqual(started.returncode, 0, started.stderr)
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: active", f"status: {initial_status}"
            ),
            encoding="utf-8",
        )
        proc = self.run_oaw(
            "task",
            "review",
            "OAW-TSK-cli",
            "--note",
            "Ready for review.",
            "--checks",
            "pytest",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn("status: review", task)
        self.assertIn("Ready for review.; checks: pytest", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertNotIn("Board:", proc.stdout)
        self.assertEqual(board_before, board_path.read_bytes())

    def test_task_review_transaction_failure_restores_task_run_and_legacy_board_bytes(
        self, monkeypatch
    ):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Established caller run.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = next((self.vault / "Agents/Runs").glob("*.md"))
        before = (task_path.read_bytes(), board_path.read_bytes(), run_path.read_bytes())
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

        monkeypatch.setenv("OAW_VAULT", str(self.vault))
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

        self.assertEqual(result, 1)
        self.assertIn("transaction failed and was rolled back", stderr.getvalue())
        self.assertEqual(
            before, (task_path.read_bytes(), board_path.read_bytes(), run_path.read_bytes())
        )

    def test_task_lifecycle_resolves_once_per_write(self, monkeypatch):
        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        monkeypatch.setenv("CODEX_THREAD_ID", "test-thread")
        original = cli.resolve_id
        resolved: list[str] = []

        def recording_resolve(raw_id: str, root: Path):
            resolved.append(raw_id)
            return original(raw_id, root)

        monkeypatch.setattr(cli, "resolve_id", recording_resolve)

        cli.main(["task", "start", "OAW-TSK-cli", "--note", "Started once."])
        self.assertEqual(resolved, ["OAW-TSK-cli"])

        resolved.clear()
        cli.main(["task", "note", "OAW-TSK-cli", "--note", "Noted once."])
        self.assertEqual(resolved, ["OAW-TSK-cli"])

        resolved.clear()
        cli.main(["note", "session", "OAW-TSK-cli", "--note", "Traced once."])
        self.assertEqual(resolved, ["OAW-TSK-cli"])

    def test_task_create_from_capture_walks_vault_once(self, monkeypatch):
        monkeypatch.setenv("OAW_VAULT", str(self.vault))
        monkeypatch.setenv("CODEX_THREAD_ID", "test-thread")
        original = resolver.iter_markdown
        walks: list[Path] = []

        def recording_walk(root: Path):
            walks.append(root)
            yield from original(root)

        monkeypatch.setattr(resolver, "iter_markdown", recording_walk)

        result = cli.main(["task", "create", "--from-capture", "OAW-CAP-active"])

        self.assertEqual(result, 0)
        self.assertEqual(walks, [self.vault])

    def test_complete_requires_checks(self):
        proc = self.run_oaw("task", "complete", "OAW-TSK-cli", "--note", "Done.")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("required: --checks", proc.stderr)

    @pytest.mark.parametrize("priority", [1, 2, 3])
    def test_task_priority_updates_metadata_trace_and_preserves_task_state(self, priority):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: todo\n",
                "status: todo\npriority: 3 # retained comment\neffort: M\n",
            ),
            encoding="utf-8",
        )
        before_board = board_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "priority",
            "OAW-TSK-cli",
            "--priority",
            str(priority),
            "--note",
            "Re-ranked against the cross-project queue.",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"Priority: {priority}", proc.stdout)
        self.assertIn("Status: todo", proc.stdout)
        self.assertNotIn("Board:", proc.stdout)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn(f"status: todo\npriority: {priority} # retained comment\neffort: M\n", task)
        self.assertIn('session-ids:\n  - "test-thread"\n', task)
        self.assertIn("`CODEX_THREAD_ID=test-thread`", task)
        self.assertIn("Re-ranked against the cross-project queue.", task)
        self.assertEqual(before_board, board_path.read_bytes())
        self.assertFalse((self.vault / "Agents/Runs").exists())

    @pytest.mark.parametrize(
        "relative_path",
        [
            "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md",
            "Tasks/Root priority task.md",
        ],
    )
    def test_task_priority_supports_non_project_task_locations(self, relative_path):
        path = self.vault / relative_path
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

        proc = self.run_oaw(
            "task",
            "priority",
            note_id,
            "--priority",
            "2",
            "--note",
            "Ranked task.",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("priority: 2", path.read_text(encoding="utf-8"))
        self.assertNotIn("Board:", proc.stdout)

    def test_task_priority_allows_explicit_missing_session_trace(self):
        cleared = {
            "CODEX_THREAD_ID": "",
            "CLAUDE_SESSION_ID": "",
            "CLAUDE_CODE_SESSION_ID": "",
            "OPENCODE_SESSION_ID": "",
            "GEMINI_SESSION_ID": "",
        }
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"

        proc = self.run_oaw(
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

        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn("priority: 2", task)
        self.assertIn("`session_id=unavailable`", task)
        self.assertNotIn("session-ids:", task)

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
    def test_task_priority_rejects_malformed_priority_without_writing(self, replacement, expected):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: todo\n", "status: todo\n" + replacement
            ),
            encoding="utf-8",
        )
        before = task_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "priority",
            "OAW-TSK-cli",
            "--priority",
            "1",
            "--note",
            "Must fail.",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn(expected, proc.stderr)
        self.assertEqual(before, task_path.read_bytes())

    def test_task_priority_rejects_unsupported_location_without_writing(self):
        path = self.vault / "Projects/Obsidian Agent Workflow/Archive/Tasks/Outside task.md"
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
        before = path.read_bytes()

        proc = self.run_oaw(
            "task",
            "priority",
            "OAW-TSK-outside",
            "--priority",
            "2",
            "--note",
            "Must fail.",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("lifecycle writes are supported", proc.stderr)
        self.assertEqual(before, path.read_bytes())

    def test_task_priority_domain_rejects_unclosed_frontmatter_before_transaction(
        self, monkeypatch
    ):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        match = resolver.resolve_id("OAW-TSK-cli", self.vault)
        malformed = task_path.read_text(encoding="utf-8").replace(
            "\n---\n\n# Resolver CLI", "\n\n# Resolver CLI"
        )
        task_path.write_text(malformed, encoding="utf-8")
        before = task_path.read_bytes()

        class UnexpectedTransaction:
            def __init__(self):
                raise AssertionError("validation must happen before transaction construction")

        monkeypatch.setattr(lifecycle, "VaultTransaction", UnexpectedTransaction)

        with pytest.raises(OawError, match="frontmatter is not closed"):
            lifecycle.update_task_priority(match, self.vault, 2, "Must fail.", False)
        self.assertEqual(before, task_path.read_bytes())

    def test_task_priority_rejects_non_task_frontmatter_without_writing(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace("type: task", "type: capture"),
            encoding="utf-8",
        )
        before = task_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "priority",
            "OAW-TSK-cli",
            "--priority",
            "2",
            "--note",
            "Must fail.",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("requires frontmatter type: task", proc.stderr)
        self.assertEqual(before, task_path.read_bytes())

    def test_task_priority_rejects_blank_note_without_writing(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        before = task_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "priority",
            "OAW-TSK-cli",
            "--priority",
            "2",
            "--note",
            "  ",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("requires non-empty --note", proc.stderr)
        self.assertEqual(before, task_path.read_bytes())

    @pytest.mark.parametrize("state", ["needs-triage", "needs-design", "prepared"])
    def test_task_preparedness_updates_metadata_trace_without_changing_run(self, state):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        started = self.run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started implementation.")
        self.assertEqual(started.returncode, 0, started.stderr)
        run_path = self.run_record_for("test-thread")
        before_run = run_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "preparedness",
            "OAW-TSK-cli",
            "--state",
            state,
            "--note",
            "Assessed design sufficiency.",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"Preparedness: {state}", proc.stdout)
        self.assertIn("Status: active", proc.stdout)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn(f"preparedness: {state}", task)
        self.assertIn("status: active", task)
        self.assertIn("Assessed design sufficiency.", task)
        self.assertEqual(before_run, run_path.read_bytes())

    def test_task_preparedness_rejects_malformed_existing_value_without_writing(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: todo\n", "status: todo\npreparedness:\n  - prepared\n"
            ),
            encoding="utf-8",
        )
        before = task_path.read_bytes()

        proc = self.run_oaw(
            "task",
            "preparedness",
            "OAW-TSK-cli",
            "--state",
            "prepared",
            "--note",
            "Must fail.",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("must be a scalar", proc.stderr)
        self.assertEqual(before, task_path.read_bytes())

    def test_task_note_appends_session_without_status_or_legacy_board_change(self):
        task_path = self.vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        before_board = board_path.read_text(encoding="utf-8")
        task_path.write_text(
            task_path.read_text(encoding="utf-8").replace(
                "status: archived\n",
                'status: archived\nsession-ids:\n  - "old,with-comma"\n'
                "  - earlier-thread # prior run\n",
            ),
            encoding="utf-8",
        )

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
        self.assertIn(
            "Updated: Projects/Obsidian Agent Workflow/Tasks/Archived task.md", proc.stdout
        )
        self.assertIn("Status: archived", proc.stdout)
        self.assertNotIn("Board:", proc.stdout)
        task = task_path.read_text(encoding="utf-8")
        self.assertIn("status: archived", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertIn("Reviewed independently.; checks: python -m unittest", task)
        self.assertIn(
            'session-ids:\n  - "old,with-comma"\n'
            "  - earlier-thread # prior run\n"
            '  - "test-thread"\n',
            task,
        )
        self.assertEqual(before_board, board_path.read_text(encoding="utf-8"))

        repeated = self.run_oaw(
            "task",
            "note",
            "OAW-TSK-archived",
            "--note",
            "Same session again.",
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        task = task_path.read_text(encoding="utf-8")
        self.assertEqual(task.count('  - "old,with-comma"\n'), 1)
        self.assertEqual(task.count("  - earlier-thread # prior run\n"), 1)
        self.assertEqual(task.count('  - "test-thread"\n'), 1)

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
        self.assertNotIn("session-ids:", task)

    def test_task_backlog_updates_status_and_session_without_touching_legacy_board(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        proc = self.run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("status: backlog", task)
        self.assertIn("CODEX_THREAD_ID=test-thread", task)
        self.assertNotIn("Board:", proc.stdout)
        self.assertEqual(board_before, board_path.read_bytes())

    def test_task_promote_updates_status_without_touching_legacy_board(self):
        board_path = self.vault / "Projects/Obsidian Agent Workflow/Board.md"
        board_before = board_path.read_bytes()
        self.run_oaw("task", "backlog", "OAW-TSK-cli", "--note", "Parked for later.")
        proc = self.run_oaw("task", "promote", "OAW-TSK-cli", "--note", "Selected next.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = (self.vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md").read_text()
        self.assertIn("status: todo", task)
        self.assertNotIn("Board:", proc.stdout)
        self.assertEqual(board_before, board_path.read_bytes())

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
