from pathlib import Path

from oaw.boards import render_next_steps_board, render_project_board


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
