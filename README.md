# obsidian-agent-workflow

Local-first tooling for resolving Obsidian reference IDs and recording agent work on project task notes.

## Warning

This repository is tooling tailored for my personal Obsidian and agent workflow. It is not intended as reusable software, and it probably does not make sense to install or use as-is. Some paths are hard-coded for my local machine and vault layout.

## `oaw`

`oaw` resolves vault IDs from note frontmatter instead of asking agents to search broad local state:

```bash
oaw resolve obs:AGT-TSK-obsidian-task-ids
oaw resolve --path OAW-TSK-cli
oaw resolve --json SR-index
```

It can list project notes by frontmatter type. Task listing is the default; capture listing hides `status: archived` notes unless explicitly requested:

```bash
oaw list --project Fable
oaw list --project Fable --type capture
oaw list --project Fable --type capture --include-archived
```

It also supports a conservative task lifecycle for project tasks under `Projects/*/Tasks`:

```bash
oaw task start OAW-TSK-cli --note "Implemented resolver and lifecycle CLI."
oaw task complete OAW-TSK-cli --note "Verified end-to-end." --checks "python -m unittest"
oaw task note OAW-TSK-cli --note "Reviewed a related session." --checks "python -m unittest"
```

Lifecycle commands update task frontmatter, append an `## Agent sessions` trace, and move the matching card on the project `Board.md` when one exists. They never invent a session ID; pass a real ID through a known harness env var such as `CODEX_THREAD_ID`, or use `--allow-missing-session-id` explicitly.

Use `oaw task note` when you need to append a dated `## Agent sessions` entry without changing `status` or moving any board card. It uses the same session-id handling as `start` and `complete`, accepts optional `--checks`, and works on task notes regardless of current status.

The cross-project Next steps board is a hand-curated priority layer at `Projects/Next steps.md`. Use `oaw board` commands for routine card edits instead of manually moving kanban lines:

```bash
oaw board add \
  --column "Next session(s)" \
  --link "Projects/Obsidian Agent Workflow/Tasks/Next steps board integration" \
  --title "Next steps board integration" \
  --why "document conventions and wire wrap-up handling" \
  --id OAW-TSK-next-board

oaw board move OAW-TSK-next-board --column "Now (current session)"
oaw board done OAW-TSK-next-board
```

`move` and `done` require the token to match exactly one card. `done` moves the card to `Done` and marks it `[x]`; other moves preserve the existing card text and keep the checkbox open.

Session snapshots copy transient harness artifacts into the vault's retrospective attachments folder:

```bash
oaw session snapshot 73550790-5af5-4efc-828c-72e6e1053d8f \
  --slug sr-dogfood-zombie-codex \
  --partial \
  --codex-thread 019f3e73-029f-7ea2-9772-fdfa1e25fb8f \
  --codex-thread 019f3e8d-8307-7052-b367-57e78f3316ae
```

The command finds the Claude parent transcript plus `subagents/*.jsonl`, copies discoverable Codex rollouts by referenced or explicit thread ID, includes referenced plugin job logs, and writes `manifest.json` with source paths, copy time, file hashes, and parent completeness. Use `--codex-rollout` for an exact rollout filename or path. Use `--grep` only for a literal that identifies one rollout; ambiguous grep matches fail and should be replaced with explicit `--codex-thread` or `--codex-rollout` flags. Re-run the same command to refresh a partial parent transcript, pick up new subagents, and remove stale files listed in the previous manifest.

## Examples from agent sessions

Recent Codex sessions used `oaw` for a few recurring jobs that are hard to do reliably with plain text search.

User prompts that should trigger `oaw` resolution include direct task IDs, `obs:` references, and project-scoped aliases:

```text
clarify in AGT-TSK-obsidian-task-ids that the pmx helper is here: ...
```

```bash
oaw resolve AGT-TSK-obsidian-task-ids
```

```text
read AGT-TSK-obsidian-task-ids, brainstorm with me to lock the decisions before we start a goal session...
```

```bash
oaw resolve AGT-TSK-obsidian-task-ids
```

```text
is cc-multi-cli mentioned anywhere else in obs:CDX?
```

```bash
oaw resolve obs:CDX     # currently reports a missing short project alias
oaw resolve --path CDX-index
```

```text
Read obs:FAB-index and obs:FAB-REF-next-session-packet.
```

```bash
oaw resolve obs:FAB-index
oaw resolve obs:FAB-REF-next-session-packet
```

Resolve an Obsidian reference before editing or searching around it. In one session, a short project reference like `obs:CDX` was misread as a literal folder; the follow-up OAW task now resolves by frontmatter ID instead:

```bash
oaw resolve OAW-TSK-project-alias-resolution
oaw resolve --path CDX-index
```

Example output:

```text
ID: OAW-TSK-project-alias-resolution
Path: /path/to/vault/Projects/Obsidian Agent Workflow/Tasks/Project alias resolution for obs references.md
Title: Project alias resolution for obs references
Matched by: id

Frontmatter:
type: task
project: obsidian-agent-workflow
status: todo
...

Outline:
2: # Project alias resolution for obs references
4: ## Problem
10: ## Impact
14: ## Desired improvement
```

`--path` prints only the resolved file:

```text
/path/to/vault/Projects/Codex Delegation/Index.md
```

Survey a project queue without opening every note body. This is useful for status checks and planning the next agent action:

```bash
oaw list --project "Obsidian Agent Workflow"
oaw list --project Fable
```

Example output is tab-separated: `id`, `status`, `title`, `vault-relative path`.

```text
OAW-TSK-audit-research-handoffs	todo	Audit existing research prompt handoffs	Projects/Obsidian Agent Workflow/Tasks/Audit existing research prompt handoffs.md
OAW-TSK-skill-rollout	done	Cross-harness skill rollout	Projects/Obsidian Agent Workflow/Tasks/Cross-harness skill rollout.md
OAW-TSK-dogfood-evaluation	done	Dogfood evaluation	Projects/Obsidian Agent Workflow/Tasks/Dogfood evaluation.md
OAW-TSK-project-alias-resolution	todo	Project alias resolution for obs references	Projects/Obsidian Agent Workflow/Tasks/Project alias resolution for obs references.md
```

List lightweight capture notes separately from tasks. Agent sessions used this to inspect active evidence/inbox items while hiding archived captures by default:

```bash
oaw list --project Fable --type capture
oaw list --project Fable --type capture --include-archived
```

Example output:

```text
FAB-CAP-cdx-evidence-inbox-created-first	active	CDX evidence inbox created first	Projects/Fable/Inbox/2026-07-07 - CDX evidence inbox created first.md
FAB-CAP-sous-chef-delegation-routing	active	Investigate sous-chef for delegation routing	Projects/Fable/Inbox/2026-07-07 - investigate sous-chef for delegation routing.md
FAB-CAP-reframe-wrap-up-receipt	active	Wrap-up receipt: reframe session	Projects/Fable/Inbox/2026-07-07 - wrap-up receipt reframe session.md
```

Record implementation provenance on the task itself. Lifecycle commands capture the current agent session, update task status, and keep the project board in sync:

```bash
CODEX_THREAD_ID=019f3b71-14db-7480-b0c5-8836714deacc \
  oaw task start OAW-TSK-project-alias-resolution \
  --note "Captured a project alias resolution failure."

CODEX_THREAD_ID=019f3e36-ee4d-7220-8e5c-c26aa5d38893 \
  oaw task complete OAW-TSK-cli \
  --note "Added capture listing and updated skill docs." \
  --checks "python -m unittest discover -s tests"
```

Example lifecycle output:

```text
Updated: /path/to/vault/Projects/Obsidian Agent Workflow/Tasks/Resolver and lifecycle CLI.md
Status: done
Board: updated
```

## Install

Install with `uv` from the repo checkout:

```bash
cd /path/to/obsidian-agent-workflow
uv tool install .
```

This builds a snapshot into a uv-managed tool environment, so the installed
`oaw` is decoupled from the checkout: switching branches or editing `bin/oaw`
does not change the installed command. After merging changes, refresh with:

```bash
uv tool install --reinstall .
```

During development, run the checkout directly with `python bin/oaw ...`
(preferably against a temp vault via `OAW_VAULT`).

The default vault path is machine-specific legacy debt; override with `OAW_VAULT`.

## Development worktrees

This repo keeps `git gtr` worktrees under `.worktrees/` via `.gtrconfig`.
That path is ignored by Git and stays inside the repository checkout, so
sandboxed agents can use isolated worktrees without creating a sibling checkout
outside the repo writable root.
