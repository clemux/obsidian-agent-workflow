import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oaw import cli
from tests.support import write


def task_text(
    note_id: str,
    title: str,
    status: str = "todo",
    extra_frontmatter: str = "",
) -> str:
    extra = extra_frontmatter
    if extra and not extra.endswith("\n"):
        extra += "\n"
    return f"""---
type: task
project: example
status: {status}
preparedness: prepared
{extra}id: {note_id}
aliases:
  - {note_id}
---

# {title}

## Agent sessions

"""


@pytest.fixture
def relation_vault(tmp_path: Path) -> Path:
    write(
        tmp_path / "Projects/Example/Tasks/Source.md",
        task_text("EXP-TSK-source", "Source"),
    )
    write(
        tmp_path / "Projects/Example/Tasks/Target.md",
        task_text("EXP-TSK-target", "Target"),
    )
    write(
        tmp_path / "Projects/Example/Tasks/Third.md",
        task_text("EXP-TSK-third", "Third"),
    )
    return tmp_path


def invoke(vault: Path, *arguments: str):
    return CliRunner().invoke(
        cli.app,
        list(arguments),
        env={"OAW_VAULT": str(vault), "CODEX_THREAD_ID": "relation-thread"},
    )


@pytest.mark.parametrize("relation_type", ["blocked-by", "follows", "follow-up-to"])
def test_relation_add_list_and_remove_round_trip(relation_vault: Path, relation_type: str) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"

    added = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        relation_type,
        "EXP-TSK-target",
        "--note",
        "Recorded dependency semantics.",
    )

    assert added.exit_code == 0, added.stderr
    assert f"Relation: {relation_type}" in added.stdout
    text = source.read_text(encoding="utf-8")
    assert (f'{relation_type}:\n  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n') in text
    assert "Added " + relation_type + " relationship to EXP-TSK-target." in text
    assert not (relation_vault / "Agents/Runs").exists()

    listed = invoke(relation_vault, "task", "relation", "list", "EXP-TSK-source", "--json")
    assert listed.exit_code == 0, listed.stderr
    payload = json.loads(listed.stdout)
    assert payload["direction"] == "outgoing"
    assert payload["relations"][0]["type"] == relation_type
    expected_state = "blocked" if relation_type == "blocked-by" else "informational"
    assert payload["relations"][0]["state"] == expected_state

    incoming = invoke(
        relation_vault,
        "task",
        "relation",
        "list",
        "EXP-TSK-target",
        "--incoming",
        "--json",
    )
    assert incoming.exit_code == 0, incoming.stderr
    incoming_payload = json.loads(incoming.stdout)
    assert incoming_payload["relations"][0]["source"] == "EXP-TSK-source"

    removed = invoke(
        relation_vault,
        "task",
        "relation",
        "remove",
        "EXP-TSK-source",
        relation_type,
        "EXP-TSK-target",
        "--note",
        "No longer required.",
    )
    assert removed.exit_code == 0, removed.stderr
    text = source.read_text(encoding="utf-8")
    assert "[[Projects/Example/Tasks/Target|EXP-TSK-target]]" not in text
    assert "Removed " + relation_type + " relationship to EXP-TSK-target." in text


def test_relation_add_is_idempotent_without_duplicate_trace(relation_vault: Path) -> None:
    arguments = (
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "Add once.",
    )
    first = invoke(relation_vault, *arguments)
    assert first.exit_code == 0, first.stderr
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    before = source.read_bytes()

    second = invoke(relation_vault, *arguments)

    assert second.exit_code == 0, second.stderr
    assert "State: present" in second.stdout
    assert source.read_bytes() == before


def test_relation_add_does_not_hide_an_invalid_duplicate(relation_vault: Path) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n'
            '  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n',
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()

    result = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "Must not mask invalid state.",
    )

    assert result.exit_code == 1
    assert "duplicate relationship target" in result.stderr
    assert source.read_bytes() == before


def test_relation_remove_can_repair_noncanonical_alias(relation_vault: Path) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/Target|Wrong label]]"\n',
        ),
        encoding="utf-8",
    )

    removed = invoke(
        relation_vault,
        "task",
        "relation",
        "remove",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "Repair malformed relationship.",
    )

    assert removed.exit_code == 0, removed.stderr
    assert "Wrong label" not in source.read_text(encoding="utf-8")


def test_relation_add_rejects_cycle_without_writing(relation_vault: Path) -> None:
    first = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "First edge.",
    )
    assert first.exit_code == 0, first.stderr
    target = relation_vault / "Projects/Example/Tasks/Target.md"
    before = target.read_bytes()

    cycle = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-target",
        "blocked-by",
        "EXP-TSK-source",
        "--note",
        "Must fail.",
    )

    assert cycle.exit_code == 1
    assert "would create a cycle" in cycle.stderr
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        (
            'blocked-by: "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n',
            "must use a YAML block list",
        ),
        (
            'blocked-by:\n  - "[[Projects/Example/Tasks/Missing|EXP-TSK-missing]]"\n',
            "does not resolve",
        ),
        (
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/Missing|EXP-TSK-missing]]"\n'
            '  - "[[Projects/Example/Tasks/Missing|EXP-TSK-missing]]"\n',
            "duplicate relationship target",
        ),
        (
            'blocked-by:\n  - "[[Projects/Example/Tasks/Target|Wrong]]"\n',
            "canonical durable path",
        ),
        (
            'blocked-by:\n  - "[[Projects/Example/Tasks/Source|EXP-TSK-source]]"\n',
            "cannot relate to itself",
        ),
        (
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n'
            '  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n',
            "duplicate relationship target",
        ),
    ],
)
def test_relation_validate_reports_invalid_relationships(
    relation_vault: Path, frontmatter: str, message: str
) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n", "preparedness: prepared\n" + frontmatter
        ),
        encoding="utf-8",
    )

    result = invoke(relation_vault, "task", "relation", "validate", "EXP-TSK-source")

    assert result.exit_code == 1
    assert message in result.stdout
    assert "validation found" in result.stderr


def test_relation_validate_detects_existing_cycle(relation_vault: Path) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    target = relation_vault / "Projects/Example/Tasks/Target.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "follows:\n"
            '  - "[[Projects/Example/Tasks/Target|EXP-TSK-target]]"\n',
        ),
        encoding="utf-8",
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "follows:\n"
            '  - "[[Projects/Example/Tasks/Source|EXP-TSK-source]]"\n',
        ),
        encoding="utf-8",
    )

    result = invoke(relation_vault, "task", "relation", "validate", "--json")

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert any(item["code"] == "cycle" for item in payload)
    assert "follows cycle" in result.stdout

    listed = invoke(relation_vault, "task", "relation", "list", "EXP-TSK-source", "--json")
    assert listed.exit_code == 0, listed.stderr
    listed_payload = json.loads(listed.stdout)
    assert listed_payload["relations"][0]["state"] == "invalid"
    assert "relationship participates in a cycle" in listed_payload["relations"][0]["issues"]


def test_relation_validate_rejects_non_task_and_missing_target_id(
    relation_vault: Path,
) -> None:
    write(
        relation_vault / "Projects/Example/Tasks/No ID.md",
        "---\ntype: task\nproject: example\nstatus: todo\n---\n# No ID\n",
    )
    write(
        relation_vault / "Projects/Example/Tasks/Not Task.md",
        "---\ntype: note\nid: EXP-note\n---\n# Not task\n",
    )
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/No ID]]"\n'
            '  - "[[Projects/Example/Tasks/Not Task|EXP-note]]"\n',
        ),
        encoding="utf-8",
    )

    result = invoke(relation_vault, "task", "relation", "validate", "EXP-TSK-source")

    assert result.exit_code == 1
    assert "stable frontmatter id" in result.stdout
    assert "not a supported task note" in result.stdout


def test_task_start_reports_blocker_and_review_refuses_until_done(
    relation_vault: Path,
) -> None:
    added = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "Target must finish first.",
    )
    assert added.exit_code == 0, added.stderr

    started = invoke(
        relation_vault,
        "task",
        "start",
        "EXP-TSK-source",
        "--note",
        "Prepare while blocked.",
    )
    assert started.exit_code == 0, started.stderr
    assert "Dependency state: blocked" in started.stdout
    assert "Blocked by: EXP-TSK-target (status: todo)" in started.stdout

    source = relation_vault / "Projects/Example/Tasks/Source.md"
    before = source.read_bytes()
    review = invoke(
        relation_vault,
        "task",
        "review",
        "EXP-TSK-source",
        "--note",
        "Ready.",
        "--checks",
        "focused tests",
    )
    assert review.exit_code == 1
    assert "refused by blocked-by relationships" in review.stderr
    assert source.read_bytes() == before

    target = relation_vault / "Projects/Example/Tasks/Target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("status: todo", "status: done"),
        encoding="utf-8",
    )
    review = invoke(
        relation_vault,
        "task",
        "review",
        "EXP-TSK-source",
        "--note",
        "Ready after dependency completion.",
        "--checks",
        "focused tests",
    )
    assert review.exit_code == 0, review.stderr
    assert "Status: review" in review.stdout


def test_superseded_dependency_remains_blocking(relation_vault: Path) -> None:
    target = relation_vault / "Projects/Example/Tasks/Target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("status: todo", "status: superseded"),
        encoding="utf-8",
    )
    added = invoke(
        relation_vault,
        "task",
        "relation",
        "add",
        "EXP-TSK-source",
        "blocked-by",
        "EXP-TSK-target",
        "--note",
        "Explicit repair required.",
    )
    assert added.exit_code == 0, added.stderr

    started = invoke(
        relation_vault,
        "task",
        "start",
        "EXP-TSK-source",
        "--note",
        "Inspect supersession.",
    )

    assert started.exit_code == 0, started.stderr
    assert "status: superseded" in started.stdout


def test_invalid_blocker_is_conservatively_reported_and_blocks_completion(
    relation_vault: Path,
) -> None:
    source = relation_vault / "Projects/Example/Tasks/Source.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "preparedness: prepared\n",
            "preparedness: prepared\n"
            "blocked-by:\n"
            '  - "[[Projects/Example/Tasks/Missing|EXP-TSK-missing]]"\n',
        ),
        encoding="utf-8",
    )

    started = invoke(
        relation_vault,
        "task",
        "start",
        "EXP-TSK-source",
        "--note",
        "Investigate invalid blocker.",
    )
    assert started.exit_code == 0, started.stderr
    assert "Dependency state: invalid" in started.stdout
    assert "does not resolve" in started.stdout

    completed = invoke(
        relation_vault,
        "task",
        "complete",
        "EXP-TSK-source",
        "--note",
        "Must fail.",
        "--checks",
        "focused tests",
    )
    assert completed.exit_code == 1
    assert "refused by blocked-by relationships" in completed.stderr
