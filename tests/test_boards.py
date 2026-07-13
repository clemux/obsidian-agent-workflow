from pathlib import Path

from oaw.boards import (
    next_steps_card,
    project_card_line,
    render_next_steps_board,
    render_project_board,
)


def test_project_board_card_format_and_rendering_contract():
    text = """---
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

"""

    rendered = render_project_board(
        text,
        task_path=Path("/vault/Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"),
        project_root=Path("/vault/Projects/Obsidian Agent Workflow"),
        title="Resolver CLI",
        note_id="OAW-TSK-cli",
        status="active",
    )

    assert (
        rendered
        == """---
kanban-plugin: board
type: board
project: obsidian-agent-workflow
id: OAW-board
aliases:
  - OAW-board
---

## Active

- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli
## Todo


## Done

"""
    )


def test_next_steps_board_card_format_and_rendering_contract():
    text = """---
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
{\"kanban-plugin\":\"board\"}
```
%%
"""

    rendered = render_next_steps_board(
        text,
        column="Now (current session)",
        token="OAW-TSK-cli",
        card=None,
        done=False,
    )

    assert (
        rendered
        == """---
kanban-plugin: board
type: board
id: NEXT-board
aliases:
  - NEXT-board
---

# Next steps board

## Now (current session)

- [ ] [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|Resolver CLI]] - finish lifecycle work (OAW-TSK-cli)
## Next session(s)


## Done

%% kanban:settings
```
{\"kanban-plugin\":\"board\"}
```
%%
"""
    )


def test_project_board_new_card_and_duplicate_target_heading_contract():
    task_path = Path("/vault/Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md")
    project_root = Path("/vault/Projects/Obsidian Agent Workflow")
    card = project_card_line(task_path, project_root, "Resolver CLI", "OAW-TSK-cli")
    assert card == "- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli"

    rendered = render_project_board(
        "## Active\n\n- [ ] [[Tasks/First|First]] - OAW-TSK-first\n\n## Active\n\n",
        task_path=task_path,
        project_root=project_root,
        title="Resolver CLI",
        note_id="OAW-TSK-cli",
        status="active",
    )

    assert (
        rendered
        == """## Active

- [ ] [[Tasks/First|First]] - OAW-TSK-first

## Active

- [ ] [[Tasks/Resolver CLI|Resolver CLI]] - OAW-TSK-cli
"""
    )


def test_next_steps_new_card_format_contract():
    card = next_steps_card(
        "Projects/Obsidian Agent Workflow/Tasks/Archived task.md",
        "Archived task",
        "review later",
        "OAW-TSK-archived",
    )

    assert card == (
        "- [ ] [[Projects/Obsidian Agent Workflow/Tasks/Archived task|Archived task]] "
        "- review later (OAW-TSK-archived)"
    )
