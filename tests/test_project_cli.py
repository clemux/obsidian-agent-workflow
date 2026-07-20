import re

from oaw import cli, lifecycle
from oaw.errors import OawError
from tests.support import write


def test_project_create_renders_native_template_and_frontmatter(run_oaw, legacy_vault):
    proc = run_oaw(
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
    assert proc.returncode == 0, proc.stderr
    assert (
        proc.stdout == "Created: Projects/Agent Tooling/Index.md\nID: AGT-index\nStatus: active\n"
    )
    note = (legacy_vault / "Projects/Agent Tooling/Index.md").read_text(encoding="utf-8")
    assert 'project: "agent-tooling"' in note
    assert 'repo: "~/dev/agent-skills:main"' in note
    assert 'id: "AGT-index"' in note
    assert '  - "AGT-index"' in note
    assert '  - "projects"\n  - "agent-tooling"\n  - "workflow"' in note
    assert '  - "test-thread"' in note
    assert "# Agent Tooling" in note
    assert "## Goal\n\nMaintain shared cross-harness skills." in note
    assert "- Status: active" in note
    assert "- Repo: ~/dev/agent-skills:main" in note
    assert "- Next action: create or select the first task when work is selected." in note
    assert "![[Templates/Project workspace.base#Work queue]]" in note
    assert "{{" not in note


def test_project_create_omits_optional_repo_and_missing_session_provenance(run_oaw, legacy_vault):
    proc = run_oaw(
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
    assert proc.returncode == 0, proc.stderr
    note = (legacy_vault / "Projects/Notebook/Index.md").read_text(encoding="utf-8")
    assert "repo:" not in note.lower()
    assert "session-ids:" not in note


def test_project_create_supports_custom_template_and_native_date(run_oaw, legacy_vault):
    write(
        legacy_vault / "Templates/Custom project.md",
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
    proc = run_oaw(
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
    assert proc.returncode == 0, proc.stderr
    note = (legacy_vault / "Projects/Custom Project/Index.md").read_text(encoding="utf-8")
    assert "# Workspace - Custom Project" in note
    assert "## Custom section\n\nRetained." in note
    assert re.search(r"created: \d{4}-\d{2}-\d{2}", note)
    assert "{{date}}" not in note


def test_project_create_rejects_unsafe_inputs_without_writing(run_oaw, legacy_vault):
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
        proc = run_oaw("project", "create", *arguments)
        assert proc.returncode == 1
        assert expected in proc.stderr
        assert not (legacy_vault / "Projects/Unsafe").exists()


def test_project_create_rejects_malformed_or_unresolved_templates(run_oaw, legacy_vault):
    template = legacy_vault / "Templates/Small project index.md"
    variants = [
        ("# {{title}}", "# No title token", "exactly one H1"),
        ("## Goal", "### Goal", "exactly one '## Goal'"),
        ("## Agent notes", "## Agent notes\n\n{{unknown}}", "unresolved template"),
    ]
    original = template.read_text(encoding="utf-8")
    for old, new, expected in variants:
        template.write_text(original.replace(old, new), encoding="utf-8")
        proc = run_oaw(
            "project",
            "create",
            "--name",
            "Broken Project",
            "--alias",
            "BP",
            "--goal",
            "Goal",
        )
        assert proc.returncode == 1
        assert expected in proc.stderr
        assert not (legacy_vault / "Projects/Broken Project").exists()
    template.write_text(original, encoding="utf-8")


def test_project_create_rejects_duplicate_id_and_existing_folder(run_oaw, legacy_vault):
    duplicate = run_oaw(
        "project",
        "create",
        "--name",
        "Another OAW",
        "--alias",
        "OAW",
        "--goal",
        "Goal",
    )
    assert duplicate.returncode == 1
    assert "id 'OAW-index' is already in use" in duplicate.stderr
    assert not (legacy_vault / "Projects/Another OAW").exists()

    (legacy_vault / "Projects/Existing").mkdir()
    existing = run_oaw(
        "project",
        "create",
        "--name",
        "Existing",
        "--alias",
        "EX",
        "--goal",
        "Goal",
    )
    assert existing.returncode == 1
    assert "project folder already exists" in existing.stderr


def test_project_create_removes_empty_folder_after_transaction_failure(monkeypatch, legacy_vault):
    class FailingTransaction:
        def __init__(self):
            self.destination = None

        def stage(self, path, _text):
            self.destination = path

        def commit(self):
            assert self.destination is not None
            self.destination.parent.mkdir(parents=True)
            raise OawError("simulated transaction failure")

    monkeypatch.setenv("OAW_VAULT", str(legacy_vault))
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
    assert result == 1
    assert not (legacy_vault / "Projects/Rollback Project").exists()
