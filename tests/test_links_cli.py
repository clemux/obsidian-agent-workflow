import stat
import subprocess
from collections.abc import Callable
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import NamedTuple

import pytest

from oaw import cli, links
from oaw.errors import OawError
from tests import support
from tests.support import (
    run_record_for,
    snapshot_tree_without_following_symlinks,
    write,
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault for the link/materialization CLI suite.

    Builds the three notes nearly every test here touches: the ``OAW-TSK-cli``
    project task (with the ``## Agent sessions`` section lifecycle writers append
    to and the ``tags:`` block one test rewrites), the ``OAW-TSK-archived`` task
    that ``obs:OAW-TSK-archived`` materializes into, and the project index that
    ``obs:OAW`` resolves to. Tests needing more (agent task or templates)
    add them inline.
    """
    root = support.make_vault(tmp_path)
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
    support.add_project_index(root, "Obsidian Agent Workflow", "OAW-index")
    return root


@pytest.fixture
def run_oaw(vault: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return ``run(*args, env=None)`` bound to the minimal ``vault``."""
    return support.make_runner(vault)


@pytest.mark.parametrize("writer", ["pause", "priority", "preparedness", "relation"])
def test_remaining_lifecycle_note_writers_materialize_obs_references(run_oaw, vault, writer):
    if writer == "pause":
        started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started for pause.")
        assert started.returncode == 0, started.stderr

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

    result = run_oaw(*arguments[writer])

    assert result.returncode == 0, result.stderr
    durable_note = (
        "Trace [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]."
    )
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    assert durable_note in task_path.read_text(encoding="utf-8")
    if writer == "pause":
        assert durable_note in run_record_for(vault, "test-thread").read_text(encoding="utf-8")


@pytest.mark.parametrize("writer", ["pause", "priority", "preparedness", "relation"])
def test_remaining_lifecycle_note_materialization_fails_before_any_write(run_oaw, vault, writer):
    if writer == "pause":
        started = run_oaw("task", "start", "OAW-TSK-cli", "--note", "Started for pause.")
        assert started.returncode == 0, started.stderr

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
    before = snapshot_tree_without_following_symlinks(vault)

    result = run_oaw(*arguments[writer])

    assert result.returncode == 1
    assert "no note with frontmatter id or alias 'OAW-TSK-does-not-exist'" in result.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_link_check_and_list_handle_escaped_pipe_in_table(run_oaw, vault):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Linked task.md",
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

    check = run_oaw("link", "check", "OAW-TSK-linked", "OAW-TSK-cli")
    assert check.returncode == 0, check.stderr
    assert "Left links right: yes" in check.stdout
    assert "Right links left: no" in check.stdout

    listed = run_oaw("link", "list", "OAW-TSK-linked")
    assert listed.returncode == 0, listed.stderr
    assert "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|CLI]]" in listed.stdout
    assert (
        "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli" in listed.stdout
    )
    assert "alias: CLI" in listed.stdout


def test_link_ensure_dry_run_and_write_append_only(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"

    dry = run_oaw(
        "link",
        "ensure",
        "OAW-TSK-cli",
        "OAW-TSK-archived",
        "--section",
        "Related",
        "--label",
        "OAW-TSK-archived",
    )
    assert dry.returncode == 0, dry.stderr
    assert "Dry-run: would update" in dry.stdout
    assert (
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"
        not in task_path.read_text(encoding="utf-8")
    )

    written = run_oaw(
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
    assert written.returncode == 0, written.stderr
    assert "Updated: Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md" in written.stdout
    task = task_path.read_text(encoding="utf-8")
    assert "## Related" in task
    assert (
        task.count("[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]") == 1
    )

    again = run_oaw(
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
    assert again.returncode == 0, again.stderr
    assert "Link: present" in again.stdout
    assert (
        task_path.read_text(encoding="utf-8").count(
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"
        )
        == 1
    )


def test_link_materialize_previews_writes_and_is_idempotent(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
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

    preview = run_oaw("link", "materialize", "OAW-TSK-cli")
    assert preview.returncode == 0, preview.stderr
    assert (
        "obs:OAW-TSK-cli -> [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"
        in preview.stdout
    )
    assert "Dry-run: would update" in preview.stdout
    assert task_path.read_text(encoding="utf-8") == source

    written = run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
    assert written.returncode == 0, written.stderr
    materialized = task_path.read_text(encoding="utf-8")
    assert (
        "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]], "
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]!" in materialized
    )
    assert "\\obs:OAW-TSK-cli literal" in materialized
    assert "[[OAW-TSK-cli|existing obs:OAW-TSK-archived]]" in materialized
    assert "`obs:OAW-TSK-cli`" in materialized
    assert "[obs:OAW-TSK-cli](https://example.test/obs:OAW-TSK-cli)" in materialized
    assert "![[Existing embed|obs:OAW-TSK-cli]]" in materialized
    assert "![obs:OAW-TSK-cli](image.png)" in materialized
    assert "[obs:OAW-TSK-cli][reference]" in materialized
    assert "<https://example.test/obs:OAW-TSK-cli>" in materialized
    assert "[reference]: https://example.test/obs:OAW-TSK-cli" in materialized
    assert "prefixobs:OAW-TSK-cli" in materialized
    assert "/obs:OAW-TSK-cli" in materialized
    assert "obs:OAW-TSK-cli/path" in materialized
    assert "obs:OAW-TSK-cli.md" in materialized
    assert "Bare OAW-TSK-cli stays bare" in materialized
    assert "[[Projects/Obsidian Agent Workflow/Index|OAW]]" in materialized
    assert (
        "| [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]] |" in materialized
    )
    assert "materialize-example: obs:DOES-NOT-EXIST" in materialized
    assert "```text\nobs:OAW-TSK-cli\n```" in materialized

    again = run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
    assert again.returncode == 0, again.stderr
    assert "References: none" in again.stdout
    assert task_path.read_text(encoding="utf-8") == materialized


def test_link_materialize_errors_without_writing_for_missing_or_ambiguous_ids(run_oaw, vault):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8")
        + "\nValid obs:OAW-TSK-archived then missing obs:OAW-TSK-nope.\n",
        encoding="utf-8",
    )
    before_missing = task_path.read_bytes()
    missing = run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
    assert missing.returncode == 1
    assert "no note with frontmatter id or alias 'OAW-TSK-nope'" in missing.stderr
    assert task_path.read_bytes() == before_missing

    write(
        vault / "Projects/Other/Tasks/Duplicate.md",
        "---\nid: OAW-TSK-archived\n---\n\n# Duplicate\n",
    )
    task_path.write_text(
        "---\nid: OAW-TSK-materialize-source\n---\n\n# Source\n\nobs:OAW-TSK-archived\n"
    )
    before_ambiguous = task_path.read_bytes()
    ambiguous = run_oaw("link", "materialize", "OAW-TSK-materialize-source", "--write")
    assert ambiguous.returncode == 1
    assert "id 'OAW-TSK-archived' is not unique" in ambiguous.stderr
    assert task_path.read_bytes() == before_ambiguous


def test_link_materialize_rejects_malformed_reference_and_rolls_back(run_oaw, vault, monkeypatch):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8") + "\nMalformed obs: stays literal.\n",
        encoding="utf-8",
    )
    malformed_before = task_path.read_bytes()
    malformed = run_oaw("link", "materialize", "OAW-TSK-cli", "--write")
    assert malformed.returncode == 1
    assert "malformed obs reference" in malformed.stderr
    assert task_path.read_bytes() == malformed_before

    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            "Malformed obs: stays literal.", "Valid obs:OAW-TSK-archived."
        ),
        encoding="utf-8",
    )
    rollback_before = task_path.read_bytes()

    def fail_commit(_self):
        raise OawError("simulated transaction failure")

    monkeypatch.setenv("OAW_VAULT", str(vault))
    monkeypatch.setattr(links.VaultTransaction, "commit", fail_commit)
    stderr = StringIO()
    with redirect_stderr(stderr):
        returncode = cli.main(["link", "materialize", "OAW-TSK-cli", "--write"])

    assert returncode == 1
    assert "simulated transaction failure" in stderr.getvalue()
    assert task_path.read_bytes() == rollback_before


def test_link_materialize_refuses_to_overwrite_a_concurrent_edit(vault, monkeypatch):
    task_path = vault / "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8") + "\nValid obs:OAW-TSK-archived.\n",
        encoding="utf-8",
    )
    concurrent = task_path.read_bytes() + b"Concurrent edit remains.\n"
    original_commit = links.VaultTransaction.commit

    def commit_after_concurrent_edit(transaction):
        task_path.write_bytes(concurrent)
        original_commit(transaction)

    monkeypatch.setenv("OAW_VAULT", str(vault))
    monkeypatch.setattr(links.VaultTransaction, "commit", commit_after_concurrent_edit)
    stderr = StringIO()
    with redirect_stderr(stderr):
        returncode = cli.main(["link", "materialize", "OAW-TSK-cli", "--write"])

    assert returncode == 1
    assert "note changed on disk since it was read" in stderr.getvalue()
    assert task_path.read_bytes() == concurrent


def test_link_materialize_write_preserves_crlf_bytes(run_oaw, vault):
    path = vault / "Projects/Obsidian Agent Workflow/Tasks/CRLF source.md"
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

    proc = run_oaw("link", "materialize", "OAW-TSK-crlf-source", "--write")

    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    written = path.read_bytes()
    assert b"\n" not in written.replace(b"\r\n", b"")
    assert b"[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].\r\n" in written


def test_multiline_code_spans_are_protected_by_shared_automatic_materialization(run_oaw, vault):
    note = (
        "`single line break\nobs:OAW-TSK-cli\nclosing` then obs:OAW-TSK-cli.\n"
        "``multi line break\nobs:OAW-TSK-archived\nclosing`` then "
        "obs:OAW-TSK-archived."
    )
    rendered, replacements = links.materialize_obs_references(note, vault)

    assert "`single line break\nobs:OAW-TSK-cli\nclosing` then [[" in rendered
    assert "``multi line break\nobs:OAW-TSK-archived\nclosing`` then [[" in rendered
    assert len(replacements) == 2

    created = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Multiline materialization",
        "--note",
        note,
    )
    assert created.returncode == 0, created.stderr
    created_text = (
        vault / "Projects/Obsidian Agent Workflow/Tasks/Multiline materialization.md"
    ).read_text(encoding="utf-8")
    assert "`single line break\nobs:OAW-TSK-cli\nclosing` then [[" in created_text
    assert "``multi line break\nobs:OAW-TSK-archived\nclosing`` then [[" in created_text


def test_automatic_materialization_failures_do_not_partially_write(run_oaw, vault):
    support.add_agent_task(
        vault,
        "Resolve vault-wide Obsidian task IDs.md",
        "AGT-TSK-obsidian-task-ids",
        status="open",
        body="# Resolve vault-wide Obsidian task IDs\n\n## Problem\n\nText.\n",
    )
    missing_task = run_oaw(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Missing materialized target",
        "--note",
        "See obs:OAW-TSK-does-not-exist.",
    )
    assert missing_task.returncode == 1
    assert not (
        vault / "Projects/Obsidian Agent Workflow/Tasks/Missing materialized target.md"
    ).exists()

    target = vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
    before_target = target.read_bytes()
    missing_observation = run_oaw(
        "note",
        "observe",
        "AGT-TSK-obsidian-task-ids",
        "--title",
        "Missing target",
        "--body",
        "See obs:OAW-TSK-does-not-exist.",
    )
    assert missing_observation.returncode == 1
    assert target.read_bytes() == before_target


# The durable wikilink that ``obs:OAW-TSK-cli`` materializes into. Shared by most
# rows in the capability table below, so it is named once here.
DURABLE = "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class DurableProseCase(NamedTuple):
    """One prose-writing command and its cross-command materialization guarantee.

    Adding a new writer means adding a row: name it, describe any prerequisite
    state via ``setup``, give the argv carrying the ``obs:`` reference, point at
    the note that must end up materialized, and list the fragments that must (and
    must not) appear. Each case runs against its own fresh ``vault``, so
    ``setup`` arranges every prerequisite from scratch (parametrized cases must be
    independent under xdist).
    """

    id: str
    # setup(run, vault) arranges prerequisite state against a fresh vault.
    setup: Callable[[Runner, Path], None]
    # argv (after the program name) containing the obs: reference under test.
    command: tuple[str, ...]
    # target(vault) -> the note whose materialized content is inspected.
    target: Callable[[Path], Path]
    # Fragments that must appear in the target: the materialized wikilink plus any
    # arguments that deliberately stay literal (checks, titles, command metadata).
    expected: tuple[str, ...]
    # Fragments that must NOT appear in the target.
    forbidden: tuple[str, ...] = ()


def _no_setup(run: Runner, vault: Path) -> None:
    """Row has no prerequisite state."""


def _add_agent_task(run: Runner, vault: Path) -> None:
    support.add_agent_task(
        vault,
        "Resolve vault-wide Obsidian task IDs.md",
        "AGT-TSK-obsidian-task-ids",
        status="open",
        body="# Resolve vault-wide Obsidian task IDs\n\n## Problem\n\nText.\n",
    )


def _add_project_template(run: Runner, vault: Path) -> None:
    support.add_project_template(vault)


def _add_research_template(run: Runner, vault: Path) -> None:
    support.add_research_template(vault)


def _create_materialized_prose_task(run: Runner, vault: Path) -> None:
    created = run(
        "task",
        "create",
        "--project",
        "obs:OAW",
        "--title",
        "Materialized prose",
        "--note",
        "Start from obs:OAW-TSK-cli.",
    )
    assert created.returncode == 0, created.stderr


def _create_and_start_materialized_prose_task(run: Runner, vault: Path) -> None:
    _create_materialized_prose_task(run, vault)
    started = run(
        "task",
        "start",
        "OAW-TSK-materialized-prose",
        "--note",
        "Continue with obs:OAW-TSK-archived.",
    )
    assert started.returncode == 0, started.stderr


DURABLE_PROSE_CASES: tuple[DurableProseCase, ...] = (
    DurableProseCase(
        id="task-create",
        setup=_no_setup,
        command=(
            "task",
            "create",
            "--project",
            "obs:OAW",
            "--title",
            "Materialized prose",
            "--note",
            "Start from obs:OAW-TSK-cli.",
        ),
        target=lambda v: v / "Projects/Obsidian Agent Workflow/Tasks/Materialized prose.md",
        expected=(f"Start from {DURABLE}.",),
    ),
    DurableProseCase(
        id="task-start",
        setup=_create_materialized_prose_task,
        command=(
            "task",
            "start",
            "OAW-TSK-materialized-prose",
            "--note",
            "Continue with obs:OAW-TSK-archived.",
        ),
        target=lambda v: v / "Projects/Obsidian Agent Workflow/Tasks/Materialized prose.md",
        expected=(
            "Continue with "
            "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]].",
        ),
    ),
    DurableProseCase(
        id="task-note",
        setup=_create_and_start_materialized_prose_task,
        command=(
            "task",
            "note",
            "OAW-TSK-materialized-prose",
            "--note",
            "Task note obs:OAW-TSK-cli.",
            "--checks",
            "obs:OAW-TSK-archived",
        ),
        target=lambda v: v / "Projects/Obsidian Agent Workflow/Tasks/Materialized prose.md",
        # --checks stays literal; only the prose --note materializes.
        expected=(f"Task note {DURABLE}.", "checks: obs:OAW-TSK-archived"),
    ),
    DurableProseCase(
        id="project-create",
        setup=_add_project_template,
        command=(
            "project",
            "create",
            "--name",
            "Materialized Project",
            "--alias",
            "MAT",
            "--goal",
            "Build from obs:OAW-TSK-cli.",
        ),
        target=lambda v: v / "Projects/Materialized Project/Index.md",
        expected=(f"Build from {DURABLE}.",),
    ),
    DurableProseCase(
        id="note-session",
        setup=_add_agent_task,
        command=(
            "note",
            "session",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Session note obs:OAW-TSK-cli.",
        ),
        target=lambda v: v / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md",
        expected=(f"Session note {DURABLE}.",),
    ),
    DurableProseCase(
        id="note-observe",
        setup=_add_agent_task,
        command=(
            "note",
            "observe",
            "AGT-TSK-obsidian-task-ids",
            "--title",
            "Literal title obs:OAW-TSK-cli",
            "--body",
            "Observation body obs:OAW-TSK-cli.",
        ),
        target=lambda v: v / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md",
        # --title stays literal; only the --body materializes.
        expected=("Literal title obs:OAW-TSK-cli", f"Observation body {DURABLE}."),
    ),
    DurableProseCase(
        id="feedback-create",
        setup=_no_setup,
        command=(
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
        ),
        target=lambda v: next((v / "Agents/Feedback").glob("*Materialized feedback.md")),
        # --command metadata stays literal; only the --body materializes.
        expected=(f"Feedback body {DURABLE}.", 'command: "obs:OAW-TSK-archived"'),
    ),
    DurableProseCase(
        id="retro-create",
        setup=_no_setup,
        command=(
            "retro",
            "create",
            "--title",
            "Materialized retrospective",
            "--summary",
            "Summary obs:OAW-TSK-cli.",
        ),
        target=lambda v: next((v / "Agents/Retrospectives").glob("*materialized retrospective.md")),
        expected=(f"Summary {DURABLE}.",),
    ),
    DurableProseCase(
        id="research-scaffold",
        setup=_add_research_template,
        command=(
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "materialization-exclusion",
            "--title",
            "Research obs:OAW-TSK-cli",
        ),
        target=lambda v: (
            v / "Projects/Obsidian Agent Workflow/Research/materialization-exclusion/Prompt.md"
        ),
        # The research title deliberately stays literal.
        expected=("Research obs:OAW-TSK-cli",),
    ),
)


@pytest.mark.parametrize("case", DURABLE_PROSE_CASES, ids=[case.id for case in DURABLE_PROSE_CASES])
def test_durable_prose_writes_share_obs_materialization(run_oaw, vault, case):
    case.setup(run_oaw, vault)

    result = run_oaw(*case.command)

    assert result.returncode == 0, result.stderr
    text = case.target(vault).read_text(encoding="utf-8")
    for fragment in case.expected:
        assert fragment in text, fragment
    for fragment in case.forbidden:
        assert fragment not in text, fragment


def test_link_ensure_bidirectional_writes_missing_reciprocal_links(run_oaw, vault):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Alpha.md",
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
        vault / "Projects/Obsidian Agent Workflow/Tasks/Beta.md",
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

    proc = run_oaw(
        "link",
        "ensure-bidirectional",
        "OAW-TSK-alpha",
        "OAW-TSK-beta",
        "--write",
    )

    assert proc.returncode == 0, proc.stderr
    alpha = (vault / "Projects/Obsidian Agent Workflow/Tasks/Alpha.md").read_text()
    beta = (vault / "Projects/Obsidian Agent Workflow/Tasks/Beta.md").read_text()
    assert "[[Projects/Obsidian Agent Workflow/Tasks/Beta|OAW-TSK-beta]]" in alpha
    assert "[[Projects/Obsidian Agent Workflow/Tasks/Alpha|OAW-TSK-alpha]]" in beta


def test_link_lint_suggests_durable_opaque_id_replacements(run_oaw, vault):
    task = vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
    task.write_text(
        task.read_text(encoding="utf-8")
        + "\n## Related\n\n- [[OAW-TSK-cli]]\n- [[PMX-UNKNOWN]]\n- [[Projects/Elsewhere|durable]]\n",
        encoding="utf-8",
    )

    proc = run_oaw("link", "lint")

    assert proc.returncode == 0, proc.stderr
    assert (
        "Archived task.md: [[OAW-TSK-cli]] -> [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]"
        in proc.stdout
    )
    assert "Archived task.md: [[PMX-UNKNOWN]] -> (unresolved)" in proc.stdout
    assert "[[Projects/Elsewhere|durable]]" not in proc.stdout


def test_link_lint_skips_non_utf8_notes(run_oaw, vault):
    bad = vault / "Projects/Obsidian Agent Workflow/Tasks/Binary.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"---\nid: OAW-TSK-binary\n---\n\xff\xfe")
    task = vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
    task.write_text(
        task.read_text(encoding="utf-8") + "\n- [[OAW-TSK-cli]]\n",
        encoding="utf-8",
    )

    proc = run_oaw("link", "lint")

    assert proc.returncode == 0, proc.stderr
    assert "Archived task.md: [[OAW-TSK-cli]]" in proc.stdout


def test_link_commands_ignore_wikilinks_inside_fenced_code(run_oaw, vault):
    task = vault / "Projects/Obsidian Agent Workflow/Tasks/Archived task.md"
    task.write_text(
        task.read_text(encoding="utf-8") + "\n```markdown\n[[OAW-TSK-cli]]\n```\n",
        encoding="utf-8",
    )

    listed = run_oaw("link", "list", "OAW-TSK-archived")
    linted = run_oaw("link", "lint")

    assert listed.returncode == 0, listed.stderr
    assert "[[OAW-TSK-cli]]" not in listed.stdout
    assert linted.returncode == 0, linted.stderr
    assert "Archived task.md: [[OAW-TSK-cli]]" not in linted.stdout
