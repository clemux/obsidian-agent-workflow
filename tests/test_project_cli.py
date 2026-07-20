import re
from pathlib import Path

import pytest

from oaw import cli, lifecycle
from oaw.errors import OawError
from tests import support
from tests.support import snapshot_tree_without_following_symlinks, write


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault: just the default project-index template.

    Every test in this file exercises ``project create`` and the CLI checks
    the template file before anything else, so all but the duplicate-id test
    need only this.
    """
    root = support.make_vault(tmp_path)
    support.add_project_template(root)
    return root


@pytest.fixture
def run_oaw(vault: Path):
    return support.make_runner(vault)


def test_project_create_renders_native_template_and_frontmatter(run_oaw, vault):
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
    assert "Created: Projects/Agent Tooling/Index.md" in proc.stdout
    assert "ID: AGT-index" in proc.stdout
    assert "Status: active" in proc.stdout
    note = (vault / "Projects/Agent Tooling/Index.md").read_text(encoding="utf-8")
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


def test_project_create_omits_optional_repo_and_missing_session_provenance(run_oaw, vault):
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
    note = (vault / "Projects/Notebook/Index.md").read_text(encoding="utf-8")
    assert "repo:" not in note.lower()
    assert "session-ids:" not in note


def test_project_create_supports_custom_template_and_native_date(run_oaw, vault):
    write(
        vault / "Templates/Custom project.md",
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
    note = (vault / "Projects/Custom Project/Index.md").read_text(encoding="utf-8")
    assert "# Workspace - Custom Project" in note
    assert "## Custom section\n\nRetained." in note
    assert re.search(r"created: \d{4}-\d{2}-\d{2}", note)
    assert "{{date}}" not in note


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param(
            ("--name", "../Unsafe", "--alias", "OK", "--goal", "Goal"),
            "--name",
            id="unsafe-name",
        ),
        pytest.param(
            ("--name", "Unsafe", "--alias", "bad", "--goal", "Goal"),
            "--alias",
            id="unsafe-alias",
        ),
        pytest.param(
            ("--name", "Unsafe", "--alias", "OK", "--goal", "line\nbreak"),
            "--goal",
            id="unsafe-goal",
        ),
        pytest.param(
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
            id="unsafe-tag",
        ),
    ],
)
def test_project_create_rejects_unsafe_inputs_without_writing(run_oaw, vault, arguments, expected):
    before = snapshot_tree_without_following_symlinks(vault)
    proc = run_oaw("project", "create", *arguments)
    assert proc.returncode == 1
    assert expected in proc.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param("# {{title}}", "# No title token", "exactly one H1", id="missing-h1-title"),
        pytest.param("## Goal", "### Goal", "exactly one '## Goal'", id="wrong-goal-heading-level"),
        pytest.param(
            "## Agent notes",
            "## Agent notes\n\n{{unknown}}",
            "unresolved template",
            id="unresolved-template-token",
        ),
    ],
)
def test_project_create_rejects_malformed_or_unresolved_templates(
    run_oaw, vault, old, new, expected
):
    template = vault / "Templates/Small project index.md"
    original = template.read_text(encoding="utf-8")
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
    assert not (vault / "Projects/Broken Project").exists()
    template.write_text(original, encoding="utf-8")


def test_project_create_rejects_duplicate_id_and_existing_folder(run_oaw, vault):
    support.add_project_index(vault, "Obsidian Agent Workflow", "OAW-index")
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
    assert not (vault / "Projects/Another OAW").exists()

    (vault / "Projects/Existing").mkdir()
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


def test_project_create_removes_empty_folder_after_transaction_failure(monkeypatch, vault):
    class FailingTransaction:
        def __init__(self):
            self.destination = None

        def stage(self, path, _text):
            self.destination = path

        def commit(self):
            assert self.destination is not None
            self.destination.parent.mkdir(parents=True)
            raise OawError("simulated transaction failure")

    monkeypatch.setenv("OAW_VAULT", str(vault))
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
    assert not (vault / "Projects/Rollback Project").exists()
