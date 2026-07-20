import json
from pathlib import Path

import pytest

from tests import support
from tests.support import write


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Vault with the "Obsidian Agent Workflow" project (index + a live and an
    archived task) plus its capture notes -- the data most tests in this file
    exercise. Tests needing other projects (Ranking, Ties, No Index, ...) seed
    their own notes on top of this bare project.
    """
    root = support.make_vault(tmp_path)
    support.add_project_index(root, "Obsidian Agent Workflow", "OAW-index")
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Archived task.md",
        "OAW-TSK-archived",
        project="obsidian-agent-workflow",
        status="archived",
        body="# Archived task\n",
    )
    support.add_captures(root)
    return root


@pytest.fixture
def run_oaw(vault: Path):
    return support.make_runner(vault)


def seed_ranked_tasks(vault: Path) -> None:
    tasks = vault / "Projects/Ranking/Tasks"
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


def test_list_tasks_preserves_archived_rows(run_oaw, vault):
    proc = run_oaw("list", "--project", "Obsidian Agent Workflow")
    assert proc.returncode == 0, proc.stderr
    assert "OAW-TSK-cli" in proc.stdout
    assert "OAW-TSK-archived" in proc.stdout


def test_list_default_output_unchanged_by_new_flags(run_oaw, vault):
    proc = run_oaw("list", "--project", "Obsidian Agent Workflow")
    assert proc.returncode == 0, proc.stderr
    for line in proc.stdout.splitlines():
        assert len(line.split("\t")) == 4, line
    assert (
        "OAW-TSK-cli\ttodo\tResolver CLI\tProjects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    ) in proc.stdout


def test_list_sort_priority_orders_by_rank_then_effort_then_title(run_oaw, vault):
    seed_ranked_tasks(vault)
    proc = run_oaw(
        "list",
        "--project",
        "Ranking",
        "--sort",
        "priority",
        "--fields",
        "id",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["RNK-TSK-high", "RNK-TSK-mid", "RNK-TSK-untriaged"]


def test_list_sort_priority_tie_breaks_on_effort_and_title(run_oaw, vault):
    tasks = vault / "Projects/Ties/Tasks"
    write(
        tasks / "A.md",
        "---\ntype: task\nstatus: todo\npriority: 1\neffort: L\nid: TIE-TSK-a\n---\n\n# Aardvark\n",
    )
    write(
        tasks / "B.md",
        "---\ntype: task\nstatus: todo\npriority: 1\neffort: S\nid: TIE-TSK-b\n---\n\n# Zebra\n",
    )
    write(
        tasks / "C.md",
        "---\ntype: task\nstatus: todo\npriority: 1\neffort: S\nid: TIE-TSK-c\n---\n\n# Antelope\n",
    )
    proc = run_oaw("list", "--project", "Ties", "--sort", "priority", "--fields", "id")
    assert proc.returncode == 0, proc.stderr
    # effort S before L; within equal effort, title Antelope before Zebra.
    assert proc.stdout.splitlines() == ["TIE-TSK-c", "TIE-TSK-b", "TIE-TSK-a"]


def test_list_field_projection_adds_frontmatter_columns(run_oaw, vault):
    seed_ranked_tasks(vault)
    proc = run_oaw(
        "list",
        "--project",
        "Ranking",
        "--sort",
        "priority",
        "--fields",
        "id,priority,effort,title",
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "RNK-TSK-high\t1\tM\tHigh leverage task"
    # Missing priority/effort project as empty columns and sort last.
    assert lines[-1] == "RNK-TSK-untriaged\t\t\tUntriaged task"


def test_list_unknown_field_errors_clearly(run_oaw, vault):
    seed_ranked_tasks(vault)
    proc = run_oaw("list", "--project", "Ranking", "--fields", "id,bogus")
    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "unknown list field: bogus" in proc.stderr


def test_list_goal_column_snippets_problem_section(run_oaw, vault):
    seed_ranked_tasks(vault)
    proc = run_oaw("list", "--project", "Ranking", "--fields", "id", "--goal")
    assert proc.returncode == 0, proc.stderr
    assert "RNK-TSK-high\tHigh priority work that must ship the ranked view first." in proc.stdout


def test_list_json_emits_sorted_projected_records(run_oaw, vault):
    seed_ranked_tasks(vault)
    proc = run_oaw(
        "list",
        "--project",
        "Ranking",
        "--sort",
        "priority",
        "--fields",
        "id,priority,goal",
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert [row["id"] for row in payload] == [
        "RNK-TSK-high",
        "RNK-TSK-mid",
        "RNK-TSK-untriaged",
    ]
    assert payload[0]["priority"] == "1"
    assert payload[-1]["priority"] == ""
    assert payload[0]["goal"] == "High priority work that must ship the ranked view first."


def test_list_invalid_sort_choice_is_usage_error(run_oaw, vault):
    proc = run_oaw("list", "--project", "Obsidian Agent Workflow", "--sort", "nope")
    assert proc.returncode == 2
    assert "usage: oaw list" in proc.stderr
    assert "invalid choice: 'nope'" in proc.stderr


@pytest.mark.parametrize(
    "alias",
    [
        pytest.param("OAW", id="bare-alias"),
        pytest.param("obs:OAW", id="obs-prefixed-alias"),
    ],
)
def test_list_accepts_project_aliases(run_oaw, vault, alias):
    expected = run_oaw("list", "--project", "Obsidian Agent Workflow")
    assert expected.returncode == 0, expected.stderr
    proc = run_oaw("list", "--project", alias)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected.stdout


def test_list_prefers_exact_project_folder_over_bare_alias(run_oaw, vault):
    task = vault / "Projects/OAW/Tasks/Exact folder task.md"
    write(
        task,
        "---\nid: EXACT-TSK-folder\nstatus: todo\ntype: task\n---\n\n# Exact folder task\n",
    )

    exact = run_oaw("list", "--project", "OAW", "--fields", "id")
    explicit_alias = run_oaw("list", "--project", "obs:OAW", "--fields", "id")

    assert exact.returncode == 0, exact.stderr
    assert exact.stdout.splitlines() == ["EXACT-TSK-folder"]
    assert explicit_alias.returncode == 0, explicit_alias.stderr
    assert "OAW-TSK-cli" in explicit_alias.stdout.splitlines()
    assert "EXACT-TSK-folder" not in explicit_alias.stdout.splitlines()


def test_list_accepts_project_folder_without_index_note(run_oaw, vault):
    task = vault / "Projects/No Index/Tasks/Loose task.md"
    write(
        task,
        "---\nid: NOIDX-TSK-loose\nstatus: todo\ntype: task\n---\n\n# Loose task\n",
    )

    proc = run_oaw("list", "--project", "No Index")

    assert proc.returncode == 0, proc.stderr
    assert "NOIDX-TSK-loose" in proc.stdout


def test_list_rejects_unknown_project_alias(run_oaw, vault):
    proc = run_oaw("list", "--project", "obs:BOGUS")
    assert proc.returncode != 0
    assert "project not found: obs:BOGUS" in proc.stderr


def test_list_capture_hides_archived_by_default(run_oaw, vault):
    proc = run_oaw(
        "list",
        "--project",
        "Obsidian Agent Workflow",
        "--type",
        "capture",
    )
    assert proc.returncode == 0, proc.stderr
    assert "OAW-CAP-active" in proc.stdout
    assert "OAW-CAP-archived" not in proc.stdout


def test_list_capture_can_include_or_select_archived(run_oaw, vault):
    proc = run_oaw(
        "list",
        "--project",
        "Obsidian Agent Workflow",
        "--type",
        "capture",
        "--include-archived",
    )
    assert proc.returncode == 0, proc.stderr
    assert "OAW-CAP-active" in proc.stdout
    assert "OAW-CAP-archived" in proc.stdout

    archived = run_oaw(
        "list",
        "--project",
        "Obsidian Agent Workflow",
        "--type",
        "capture",
        "--status",
        "archived",
    )
    assert archived.returncode == 0, archived.stderr
    assert "OAW-CAP-active" not in archived.stdout
    assert "OAW-CAP-archived" in archived.stdout
