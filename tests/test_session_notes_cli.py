import re

from tests import support


def test_lifecycle_supports_agents_task_without_board_output(run_oaw, legacy_vault):
    proc = run_oaw(
        "task",
        "start",
        "AGT-TSK-obsidian-task-ids",
        "--note",
        "Should fail.",
    )
    assert proc.returncode == 0, proc.stderr
    note = (legacy_vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md").read_text()
    assert "status: active" in note
    assert "execution: agent" in note
    assert "Board:" not in proc.stdout


def test_note_session_appends_agent_session_to_non_project_note(run_oaw, legacy_vault):
    proc = run_oaw(
        "note",
        "session",
        "AGT-TSK-obsidian-task-ids",
        "--note",
        "Reviewed resolver policy.",
    )
    assert proc.returncode == 0, proc.stderr
    note = (legacy_vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md").read_text()
    assert "## Agent sessions" in note
    assert "CODEX_THREAD_ID=test-thread" in note
    assert 'session-ids:\n  - "test-thread"\n' in note
    assert "Reviewed resolver policy." in note
    assert "Updated: Agents/Tasks/Resolve vault-wide Obsidian task IDs.md" in proc.stdout


def test_note_session_leaves_blank_line_before_following_heading(run_oaw, legacy_vault):
    path = legacy_vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
    support.write(
        path,
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

## Agent sessions

- 2026-07-13 - Claude Code - `CLAUDE_CODE_SESSION_ID=old-thread` - Existing entry.

## Decisions

Keep this decision.
""",
    )

    proc = run_oaw(
        "note",
        "session",
        "AGT-TSK-obsidian-task-ids",
        "--note",
        "Reviewed resolver policy.",
    )

    assert proc.returncode == 0, proc.stderr
    note = path.read_text(encoding="utf-8")
    assert "Reviewed resolver policy." in note
    after_entry = note.split("Reviewed resolver policy.", 1)[1]
    before_heading = after_entry.split("## Decisions", 1)[0]
    assert before_heading == "\n\n"


def test_note_session_refuses_unsupported_session_ids_without_writing(run_oaw, legacy_vault):
    path = legacy_vault / "Agents/Tasks/Resolve vault-wide Obsidian task IDs.md"
    baseline = path.read_text(encoding="utf-8")
    for session_ids in (
        'session-ids: ["old,with-comma", earlier-thread]\n',
        "session-ids:\n  owner: earlier-thread\n",
        "session-ids:\n  - null\n",
    ):
        before = baseline.replace("status: open\n", "status: open\n" + session_ids)
        path.write_text(before, encoding="utf-8")

        proc = run_oaw(
            "note",
            "session",
            "AGT-TSK-obsidian-task-ids",
            "--note",
            "Must not corrupt session metadata.",
        )

        assert proc.returncode != 0
        assert "session-ids must" in proc.stderr
        assert path.read_text(encoding="utf-8") == before


def test_note_observe_appends_block_under_target_section(run_oaw, legacy_vault):
    support.write(
        legacy_vault / "Projects/Obsidian Agent Workflow/Research/Evidence.md",
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
    proc = run_oaw(
        "note",
        "observe",
        "OAW-RES-evidence",
        "--title",
        "Lint gap",
        "--body",
        "Provider-visible text needs a mechanical check.",
    )
    assert proc.returncode == 0, proc.stderr
    note = (legacy_vault / "Projects/Obsidian Agent Workflow/Research/Evidence.md").read_text()
    assert re.search(r"### \d{4}-\d{2}-\d{2} - Lint gap", note)
    assert "Provider-visible text needs a mechanical check." in note
    assert note.index("Lint gap") < note.index("## Decisions")


def test_note_observe_ignores_headings_inside_fenced_code(run_oaw, legacy_vault):
    path = legacy_vault / "Projects/Obsidian Agent Workflow/Research/Fenced.md"
    support.write(
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

    proc = run_oaw(
        "note",
        "observe",
        "OAW-RES-fenced",
        "--title",
        "Fence-safe append",
        "--body",
        "This block belongs after the fence.",
    )

    assert proc.returncode == 0, proc.stderr
    note = path.read_text(encoding="utf-8")
    assert note.index("Fence-safe append") > note.index("Keep this conclusion.")
    assert note.index("Fence-safe append") < note.index("## Decisions")


def test_retro_create_writes_dated_template(run_oaw, legacy_vault):
    proc = run_oaw(
        "retro",
        "create",
        "--title",
        "Resolver dogfood",
        "--summary",
        "Captured the resolver workflow and follow-ups.",
        "--date",
        "2026-07-09",
    )
    assert proc.returncode == 0, proc.stderr
    path = legacy_vault / "Agents/Retrospectives/2026-07-09 resolver dogfood.md"
    assert path.exists()
    note = path.read_text(encoding="utf-8")
    assert "type: retrospective" in note
    assert "status: draft" in note
    assert "id: AGT-RETRO-2026-07-09-resolver-dogfood" in note
    assert "session-ids:" in note
    assert "  - test-thread" in note
    assert "# 2026-07-09 - Resolver dogfood" in note
    assert "Captured the resolver workflow and follow-ups." in note
    assert "Created: Agents/Retrospectives/2026-07-09 resolver dogfood.md" in proc.stdout


def test_retro_create_rejects_duplicate_id(run_oaw, legacy_vault):
    proc = run_oaw(
        "retro",
        "create",
        "--title",
        "Duplicate ID",
        "--date",
        "2026-07-09",
        "--id",
        "AGT-TSK-obsidian-task-ids",
    )

    assert proc.returncode != 0
    assert "id 'AGT-TSK-obsidian-task-ids' is already in use" in proc.stderr
    assert not (legacy_vault / "Agents/Retrospectives/2026-07-09 duplicate id.md").exists()


def test_retro_create_rejects_whitespace_only_id(run_oaw):
    proc = run_oaw(
        "retro",
        "create",
        "--title",
        "Whitespace ID",
        "--date",
        "2026-07-09",
        "--id",
        "   ",
    )

    assert proc.returncode != 0
    assert "requires a non-empty --id" in proc.stderr


def test_retro_create_normalizes_accented_title_slug(legacy_vault, base_env):
    proc = support.run_oaw_subprocess(
        [
            "retro",
            "create",
            "--title",
            "Révision générale",
            "--date",
            "2026-07-09",
        ],
        base_env,
    )

    assert proc.returncode == 0, proc.stderr
    path = legacy_vault / "Agents/Retrospectives/2026-07-09 revision generale.md"
    assert path.exists()
    note = path.read_text(encoding="utf-8")
    assert "id: AGT-RETRO-2026-07-09-revision-generale" in note
