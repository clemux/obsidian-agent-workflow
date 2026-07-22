from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from oaw import task_rename
from oaw.notes import VaultTransaction
from tests.support import (
    add_project_index,
    add_task,
    make_runner,
    make_vault,
    snapshot_tree_without_following_symlinks,
    write,
)

TASK_ID = "EX-TSK-rename"
OLD_PATH = "Projects/Example/Tasks/Old Title.md"
NEW_PATH = "Projects/Example/Tasks/New Title.md"
OLD_TARGET = OLD_PATH.removesuffix(".md")
NEW_TARGET = NEW_PATH.removesuffix(".md")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = make_vault(tmp_path / "vault")
    add_project_index(root, "Example", "EX-index")
    add_task(
        root,
        "Example",
        "Old Title.md",
        TASK_ID,
        project="example",
        tags=("task",),
        body=(f"# Old Title\n\n## Related\n\n- [[{OLD_TARGET}|{TASK_ID}]]\n\n## Agent sessions\n"),
    )
    return root


@pytest.fixture
def run_oaw(vault: Path):
    return make_runner(vault)


def plan_token(output: str) -> str:
    match = re.search(r"^Plan: (sha256:[0-9a-f]{64})$", output, re.MULTILINE)
    assert match, output
    return match.group(1)


def preview(run_oaw, title: str = "New Title"):
    return run_oaw(
        "task",
        "rename",
        TASK_ID,
        "--title",
        title,
        "--note",
        "Rename for clearer navigation.",
    )


def apply(run_oaw, token: str, title: str = "New Title"):
    return run_oaw(
        "task",
        "rename",
        TASK_ID,
        "--title",
        title,
        "--note",
        "Rename for clearer navigation.",
        "--write",
        "--expect-plan",
        token,
    )


def seed_linked_vault(vault: Path, run_oaw) -> Path:
    referring = add_task(
        vault,
        "Example",
        "Referring.md",
        "EX-TSK-referring",
        project="example",
        body=(
            "# Referring\n\n"
            f"Normal [[{OLD_TARGET}|label]] and ![[{OLD_TARGET}#Section|embed]].\n"
            f"Block [[{OLD_TARGET}^block|block]].\n"
            f"Inline `[[{OLD_TARGET}|code]]`.\n"
            f"%% [[{OLD_TARGET}|Obsidian comment]] %%\n"
            f"<!-- [[{OLD_TARGET}|HTML comment]] -->\n"
            "```md\n"
            f"[[{OLD_TARGET}|fenced]]\n"
            "```\n\n"
            "## Agent sessions\n"
        ),
    )
    text = referring.read_text(encoding="utf-8")
    referring.write_text(
        text.replace(
            "---\n\n# Referring",
            f'follow-up-to:\n  - "[[{OLD_TARGET}|{TASK_ID}]]"\n---\n\n# Referring',
        ),
        encoding="utf-8",
    )
    write(
        vault / "Captures/Entries/Linked capture.md",
        "---\n"
        "type: capture\n"
        "id: CAP-linked\n"
        "destinations:\n"
        f'  - "[[{OLD_TARGET}|{TASK_ID}]]"\n'
        "---\n\n"
        "# Linked capture\n\n"
        f"- [[{OLD_TARGET}|{TASK_ID}]]\n",
    )
    write(
        vault / "Notes/Generic.md",
        f"# Generic\n\n[[{OLD_TARGET}.md#Heading|kept alias]]\n",
    )
    started = run_oaw("task", "start", TASK_ID, "--note", "Start fixture run.")
    assert started.returncode == 0, started.stderr
    paused = run_oaw("task", "pause", TASK_ID, "--note", "Pause fixture run.")
    assert paused.returncode == 0, paused.stderr
    return referring


def test_task_rename_preview_write_and_no_op_preserve_semantics(vault, run_oaw):
    referring = seed_linked_vault(vault, run_oaw)
    before = snapshot_tree_without_following_symlinks(vault)

    first = preview(run_oaw)
    second = preview(run_oaw)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == ""
    assert snapshot_tree_without_following_symlinks(vault) == before
    token = plan_token(first.stdout)
    assert f"Old path: {OLD_PATH}" in first.stdout
    assert f"New path: {NEW_PATH}" in first.stdout
    assert "Dry-run: no changes written" in first.stdout

    written = apply(run_oaw, token)

    assert written.returncode == 0, written.stderr
    assert not (vault / OLD_PATH).exists()
    renamed = vault / NEW_PATH
    renamed_text = renamed.read_text(encoding="utf-8")
    assert "# New Title\n" in renamed_text
    assert f"id: {TASK_ID}" in renamed_text
    assert f"  - {TASK_ID}" in renamed_text
    assert "status: active" in renamed_text
    assert "Rename for clearer navigation." in renamed_text
    assert OLD_TARGET not in "\n".join(
        line for line in renamed_text.splitlines() if not line.startswith("- 2026-")
    )

    referring_text = referring.read_text(encoding="utf-8")
    assert f"[[{NEW_TARGET}|label]]" in referring_text
    assert f"![[{NEW_TARGET}#Section|embed]]" in referring_text
    assert f"[[{NEW_TARGET}^block|block]]" in referring_text
    assert f'follow-up-to:\n  - "[[{NEW_TARGET}|{TASK_ID}]]"' in referring_text
    assert f"`[[{OLD_TARGET}|code]]`" in referring_text
    assert f"%% [[{OLD_TARGET}|Obsidian comment]] %%" in referring_text
    assert f"<!-- [[{OLD_TARGET}|HTML comment]] -->" in referring_text
    assert f"[[{OLD_TARGET}|fenced]]" in referring_text
    capture = (vault / "Captures/Entries/Linked capture.md").read_text(encoding="utf-8")
    assert capture.count(NEW_TARGET) == 2
    assert f"[[{NEW_TARGET}#Heading|kept alias]]" in (vault / "Notes/Generic.md").read_text(
        encoding="utf-8"
    )
    run_text = next((vault / "Agents/Runs").glob("*.md")).read_text(encoding="utf-8")
    assert f'task: "[[{NEW_TARGET}|{TASK_ID}]]"' in run_text
    assert "state: paused" in run_text

    after_write = snapshot_tree_without_following_symlinks(vault)
    no_op = preview(run_oaw, "New Title")
    assert no_op.returncode == 0, no_op.stderr
    assert "No-op: task already has the requested path and H1" in no_op.stdout
    no_op_write = apply(run_oaw, plan_token(no_op.stdout), "New Title")
    assert no_op_write.returncode == 0, no_op_write.stderr
    assert snapshot_tree_without_following_symlinks(vault) == after_write


def test_task_rename_requires_matching_plan_without_writing(vault, run_oaw):
    before = snapshot_tree_without_following_symlinks(vault)

    missing = run_oaw(
        "task",
        "rename",
        TASK_ID,
        "--title",
        "New Title",
        "--note",
        "reason",
        "--write",
    )
    mismatch = apply(run_oaw, "sha256:" + "0" * 64)
    expect_without_write = run_oaw(
        "task",
        "rename",
        TASK_ID,
        "--title",
        "New Title",
        "--note",
        "reason",
        "--expect-plan",
        "sha256:" + "0" * 64,
    )

    assert missing.returncode == mismatch.returncode == expect_without_write.returncode == 1
    assert "--write requires --expect-plan" in missing.stderr
    assert "plan mismatch" in mismatch.stderr
    assert "--expect-plan requires --write" in expect_without_write.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


@pytest.mark.parametrize(
    "title",
    ["", " padded", "line\nbreak", ".hidden", "trailing.", "bad/name", "CON.txt"],
)
def test_task_rename_rejects_unsafe_titles_without_writing(vault, run_oaw, title):
    before = snapshot_tree_without_following_symlinks(vault)

    result = preview(run_oaw, title)

    assert result.returncode != 0
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_task_rename_refuses_alias_collision_and_ambiguous_h1(vault, run_oaw):
    source = vault / OLD_PATH
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            f"  - {TASK_ID}\n", f"  - {TASK_ID}\n  - EX-TSK-alias\n"
        ),
        encoding="utf-8",
    )
    alias = run_oaw(
        "task",
        "rename",
        "EX-TSK-alias",
        "--title",
        "New Title",
        "--note",
        "reason",
    )
    assert alias.returncode == 1
    assert "canonical frontmatter id" in alias.stderr

    write(vault / NEW_PATH, "occupied\n")
    collision = preview(run_oaw)
    assert collision.returncode == 1
    assert "collides with existing sibling" in collision.stderr
    (vault / NEW_PATH).unlink()

    source.write_text(source.read_text(encoding="utf-8") + "\n# Second H1\n", encoding="utf-8")
    ambiguous = preview(run_oaw)
    assert ambiguous.returncode == 1
    assert "exactly one non-empty H1" in ambiguous.stderr


def test_task_rename_refuses_running_run(vault, run_oaw):
    started = run_oaw("task", "start", TASK_ID, "--note", "Run remains active.")
    assert started.returncode == 0, started.stderr
    before = snapshot_tree_without_following_symlinks(vault)

    result = preview(run_oaw)

    assert result.returncode == 1
    assert "agent run is running" in result.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


@pytest.mark.parametrize(
    ("filename", "h1", "title"),
    [
        ("New Title.md", "Old Title", "New Title"),
        ("Old Filename.md", "New Title", "New Title"),
        ("Case Title.md", "Case Title", "case title"),
    ],
)
def test_task_rename_repairs_title_path_and_case_only_variants(tmp_path, filename, h1, title):
    vault = make_vault(tmp_path / "vault")
    add_project_index(vault, "Example", "EX-index")
    add_task(
        vault,
        "Example",
        filename,
        TASK_ID,
        project="example",
        body=f"# {h1}\n\n## Agent sessions\n",
    )
    run_oaw = make_runner(vault)

    planned = preview(run_oaw, title)
    assert planned.returncode == 0, planned.stderr
    written = apply(run_oaw, plan_token(planned.stdout), title)

    assert written.returncode == 0, written.stderr
    expected = vault / "Projects/Example/Tasks" / f"{title}.md"
    assert expected.exists()
    assert f"# {title}\n" in expected.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad_note", ["symlink", "utf8"])
def test_task_rename_refuses_unscannable_markdown(vault, run_oaw, bad_note):
    notes = vault / "Notes"
    notes.mkdir()
    if bad_note == "symlink":
        target = notes / "Target.md"
        target.write_text("# Target\n", encoding="utf-8")
        (notes / "Alias.md").symlink_to(target)
    else:
        (notes / "Unreadable.md").write_bytes(b"\xff")
    before = snapshot_tree_without_following_symlinks(vault)

    result = preview(run_oaw)

    assert result.returncode == 1
    assert "regular, non-symlink" in result.stderr or "not valid UTF-8" in result.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_task_rename_rolls_back_destination_and_backlinks_on_commit_failure(
    vault, run_oaw, monkeypatch
):
    seed_linked_vault(vault, run_oaw)
    planned = preview(run_oaw)
    assert planned.returncode == 0, planned.stderr
    before = snapshot_tree_without_following_symlinks(vault)
    original_commit = VaultTransaction.commit

    def failing_commit(self, replace=os.replace, *, postcondition=None):
        def fail_replace(source, destination):
            raise OSError("injected backlink publication failure")

        return original_commit(self, replace=fail_replace, postcondition=postcondition)

    monkeypatch.setattr(VaultTransaction, "commit", failing_commit)

    result = apply(run_oaw, plan_token(planned.stdout))

    assert result.returncode == 1
    assert "rolled back" in result.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_task_rename_rolls_back_when_postcondition_fails(vault, run_oaw, monkeypatch):
    planned = preview(run_oaw)
    before = snapshot_tree_without_following_symlinks(vault)

    def fail_postcondition(plan):
        raise RuntimeError("injected postcondition failure")

    monkeypatch.setattr(task_rename, "_assert_postconditions", fail_postcondition)

    result = apply(run_oaw, plan_token(planned.stdout))

    assert result.returncode == 1
    assert "rolled back" in result.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_task_rename_preserves_concurrent_backlink_edit(vault, run_oaw, monkeypatch):
    referring = seed_linked_vault(vault, run_oaw)
    planned = preview(run_oaw)
    original_commit = VaultTransaction.commit

    def racing_commit(self, replace=os.replace, *, postcondition=None):
        referring.write_text(
            referring.read_text(encoding="utf-8") + "\nConcurrent edit.\n",
            encoding="utf-8",
        )
        return original_commit(self, replace=replace, postcondition=postcondition)

    monkeypatch.setattr(VaultTransaction, "commit", racing_commit)

    result = apply(run_oaw, plan_token(planned.stdout))

    assert result.returncode == 1
    assert "changed on disk" in result.stderr
    assert "Concurrent edit." in referring.read_text(encoding="utf-8")
    assert (vault / OLD_PATH).exists()
    assert not (vault / NEW_PATH).exists()


def test_task_rename_preserves_concurrent_source_edit(vault, run_oaw, monkeypatch):
    planned = preview(run_oaw)
    source = vault / OLD_PATH
    original_commit = VaultTransaction.commit

    def racing_commit(self, replace=os.replace, *, postcondition=None):
        source.write_text(
            source.read_text(encoding="utf-8") + "\nConcurrent source edit.\n",
            encoding="utf-8",
        )
        return original_commit(self, replace=replace, postcondition=postcondition)

    monkeypatch.setattr(VaultTransaction, "commit", racing_commit)

    result = apply(run_oaw, plan_token(planned.stdout))

    assert result.returncode == 1
    assert "changed on disk" in result.stderr
    assert "Concurrent source edit." in source.read_text(encoding="utf-8")
    assert not (vault / NEW_PATH).exists()


def test_task_rename_does_not_clobber_a_racing_destination(vault, run_oaw, monkeypatch):
    planned = preview(run_oaw)
    destination = vault / NEW_PATH
    original_commit = VaultTransaction.commit

    def racing_commit(self, replace=os.replace, *, postcondition=None):
        destination.write_text("racing creator\n", encoding="utf-8")
        return original_commit(self, replace=replace, postcondition=postcondition)

    monkeypatch.setattr(VaultTransaction, "commit", racing_commit)

    result = apply(run_oaw, plan_token(planned.stdout))

    assert result.returncode == 1
    assert "destination already exists" in result.stderr
    assert destination.read_text(encoding="utf-8") == "racing creator\n"
    assert (vault / OLD_PATH).exists()


@pytest.mark.parametrize("malformed", ["source", "run", "relation"])
def test_task_rename_refuses_malformed_task_owned_state(vault, run_oaw, malformed):
    source = vault / OLD_PATH
    if malformed == "source":
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                f"id: {TASK_ID}\n", f"id: {TASK_ID}\nid: duplicate\n"
            ),
            encoding="utf-8",
        )
    elif malformed == "run":
        assert run_oaw("task", "start", TASK_ID, "--note", "start").returncode == 0
        assert run_oaw("task", "pause", TASK_ID, "--note", "pause").returncode == 0
        run = next((vault / "Agents/Runs").glob("*.md"))
        run.write_text(
            run.read_text(encoding="utf-8").replace(OLD_TARGET, "Tasks/Wrong"),
            encoding="utf-8",
        )
    else:
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "---\n\n# Old Title",
                "blocked-by: not-a-list\n---\n\n# Old Title",
            ),
            encoding="utf-8",
        )
    before = snapshot_tree_without_following_symlinks(vault)

    result = preview(run_oaw)

    assert result.returncode == 1
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_rewrite_active_wikilinks_preserves_suffix_alias_embed_and_protected_spans():
    source = (
        f"![[{OLD_TARGET}.md#Heading|Alias]] [[{OLD_TARGET}^block]]\n"
        f"`[[{OLD_TARGET}|inline]]` %% [[{OLD_TARGET}|comment]] %%\n"
        f"<!-- [[{OLD_TARGET}|html]] -->\n"
        "```\n"
        f"[[{OLD_TARGET}|fenced]]\n"
        "```\n"
        "> ~~~md\n"
        f"> [[{OLD_TARGET}|quoted fence]]\n"
        "> ~~~\n"
        "- ~~~md\n"
        f"  [[{OLD_TARGET}|list fence]]\n"
        "  ~~~\n"
        f"Unmatched ` is literal [[{OLD_TARGET}|active after unmatched tick]]\n\n"
        f"Paragraph ` [[{OLD_TARGET}|active before block break]]\n\nNext ` paragraph\n"
    )

    rendered, count = task_rename.rewrite_active_wikilink_targets(source, OLD_TARGET, NEW_TARGET)

    assert count == 4
    assert f"![[{NEW_TARGET}#Heading|Alias]]" in rendered
    assert f"[[{NEW_TARGET}^block]]" in rendered
    assert f"`[[{OLD_TARGET}|inline]]`" in rendered
    assert f"%% [[{OLD_TARGET}|comment]] %%" in rendered
    assert f"<!-- [[{OLD_TARGET}|html]] -->" in rendered
    assert f"[[{OLD_TARGET}|fenced]]" in rendered
    assert f"> [[{OLD_TARGET}|quoted fence]]" in rendered
    assert f"  [[{OLD_TARGET}|list fence]]" in rendered
    assert f"[[{NEW_TARGET}|active after unmatched tick]]" in rendered
    assert f"[[{NEW_TARGET}|active before block break]]" in rendered
