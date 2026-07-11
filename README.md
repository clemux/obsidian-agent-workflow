# obsidian-agent-workflow

Local-first tooling for agent-driven Obsidian workflows: the `oaw` CLI resolves vault reference IDs from note frontmatter and records agent work — task lifecycle, board moves, session traces — on project notes.

## Status and caveats

This project has been written entirely by AI. The repository owner has not read or reviewed any of the code. Use it at your own risk.

This repository is tooling tailored for a local Obsidian and agent workflow. It is not intended as reusable software, and it probably does not make sense to install or use as-is. Some paths are machine-specific legacy debt; prefer `OAW_VAULT` for any new automation.

## Install

Install with `uv` from the repo checkout:

```bash
cd /path/to/obsidian-agent-workflow
uv tool install .
```

This builds a snapshot into a uv-managed tool environment, so the installed
`oaw` is decoupled from the checkout: switching branches or editing `bin/oaw`
does not change the installed command. After merging changes, refresh its recorded
local source and verify the installed command surface with:

```bash
uv tool upgrade oaw
python scripts/check_cli_parity.py
```

If `uv tool upgrade oaw` does not rebuild the local checkout source, use
`uv tool install --reinstall .` and rerun the parity check.

During development, run the checkout directly with `python bin/oaw ...`
(preferably against a temp vault via `OAW_VAULT`).

The default vault path is machine-specific legacy debt; override with `OAW_VAULT`.

## Table of contents

- [Command overview](#command-overview)
- [Resolving references](#resolving-references)
- [Listing notes](#listing-notes)
- [Task lifecycle](#task-lifecycle)
- [Boards](#boards)
- [Sessions](#sessions)
  - [Session lookup](#session-lookup)
  - [Session snapshots](#session-snapshots)
- [Notes and retrospectives](#notes-and-retrospectives)
- [Import and export](#import-and-export)
  - [Safe export ingestion](#safe-export-ingestion)
  - [Outbound export bundles](#outbound-export-bundles)
- [Link hygiene](#link-hygiene)
- [Installed vs checkout CLI](#installed-vs-checkout-cli)
- [Examples from agent sessions](#examples-from-agent-sessions)
- [Development worktrees](#development-worktrees)

## Command overview

| Command | Purpose |
| --- | --- |
| [`oaw resolve`](#resolving-references) | Resolve a reference ID or `obs:` alias to a vault note |
| [`oaw list`](#listing-notes) | List a project's tasks or captures by frontmatter type |
| [`oaw task`](#task-lifecycle) | Update task lifecycle status with an agent-session trace |
| [`oaw board`](#boards) | Add, move, and complete kanban cards on project boards |
| [`oaw session`](#sessions) | Look up a session ID across notes and artifacts; snapshot session artifacts |
| [`oaw note` / `oaw retro`](#notes-and-retrospectives) | Append session traces or observations; create retrospective drafts |
| [`oaw ingest` / `oaw export`](#import-and-export) | Ingest marked-safe handoff files; export marked-safe note bundles |
| [`oaw link`](#link-hygiene) | Check, add, and lint durable wikilinks between notes |

## Resolving references

`oaw resolve` resolves vault IDs from note frontmatter instead of asking agents to search broad local state:

```bash
oaw resolve obs:AGT-TSK-obsidian-task-ids
oaw resolve obs:CDX
oaw resolve --path OAW-TSK-cli
oaw resolve --json SR-index
```

Short uppercase project aliases such as `obs:CDX` resolve to a matching `Projects/<Project>/Index.md` note whose ID is `CDX-index` when there is no exact frontmatter `id` or `aliases` match. Ambiguous project aliases fail with candidate paths instead of falling back to a literal folder.

## Listing notes

`oaw list` lists project notes by frontmatter type. Task listing is the default; capture listing hides `status: archived` notes unless explicitly requested:

```bash
oaw list --project Fable
oaw list --project Fable --type capture
oaw list --project Fable --type capture --include-archived
oaw list --project Fable --type capture --status archived
```

Use `--status <value>` to select one exact frontmatter status. When `--status`
is omitted, `--include-archived` adds archived notes to the normal listing;
otherwise archived notes are hidden.

## Task lifecycle

`oaw task` provides a conservative task lifecycle for project tasks under `Projects/*/Tasks`:

```bash
oaw task backlog OAW-TSK-cli --note "Parked until the dependency is ready."
oaw task promote OAW-TSK-cli --note "Selected for the next session."
oaw task start OAW-TSK-cli --note "Implemented resolver and lifecycle CLI."
oaw task complete OAW-TSK-cli --note "Verified end-to-end." --checks "python -m unittest"
oaw task note OAW-TSK-cli --note "Reviewed a related session." --checks "python -m unittest"
```

Lifecycle commands update task frontmatter, append an `## Agent sessions` trace, and move the matching card on the project `Board.md` when one exists. `backlog` sets `status: backlog`, `promote` sets `status: todo`, `start` sets `status: active`, and `complete` sets `status: done`. They never invent a session ID; pass a real ID through a known harness env var such as `CODEX_THREAD_ID`, or use `--allow-missing-session-id` explicitly.

Use `oaw task note` when you need to append a dated `## Agent sessions` entry without changing `status` or moving any board card. It uses the same session-id handling as `start` and `complete`, accepts optional `--checks`, and works on task notes regardless of current status.

## Boards

Project boards use the column convention `Backlog` -> `Todo` -> `Active` -> `Done`. `Todo` is for near-term chosen work; `Backlog` is for unscheduled known work. When a session decides what should happen next, promote the matching task so the board reflects that decision.

Use `oaw board ensure-backlog --project "Project Name"` to add the `Backlog` column to an existing project board before `Todo` without rewriting cards.

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

`ensure-backlog` and task lifecycle commands target a project-local
`Projects/<Project>/Board.md`. The `add`, `move`, and `done` commands target the
cross-project `Projects/Next steps.md` board.

The aggregate cross-project task Base lives at `Projects/Cross-project tasks.base`. Use it when choosing what to work on next across OAW and adjacent agent-tooling queues: its open-task view includes `Projects/*/Tasks` and `Agents/Tasks`, keeps `backlog`, `todo`, `active`, and legacy `open` tasks visible, and excludes terminal `done` and `superseded` work. Priority is a vault-wide 1/2/3 scale: `1` is urgent, blocking, or unusually high-leverage; `2` is normal next-session work with clear value; `3` is useful backlog work. Cross-project usefulness can raise priority, and the Base sorts by priority, then effort (`S`, `M`, `L`), then title.

## Sessions

### Session lookup

`oaw session lookup` looks up a session/thread ID across vault notes and session artifacts:

```bash
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc --codex-root /tmp/example-codex-sessions --claude-root /tmp/example-claude-projects
```

`oaw session lookup <id>` follows a two-step resolution strategy:

- First it scans the vault and prints matching note paths and frontmatter IDs for the literal ID.
- If no vault match is found, it scans artifact roots and prints a synopsis of discovered session artifacts.
- If no match is found anywhere, it exits `0` and prints a clear *not logged* message.

### Session snapshots

Session snapshots copy transient harness artifacts into the vault's retrospective attachments folder:

```bash
oaw session snapshot 73550790-5af5-4efc-828c-72e6e1053d8f \
  --slug sr-dogfood-zombie-codex \
  --partial \
  --codex-thread 019f3e73-029f-7ea2-9772-fdfa1e25fb8f \
  --codex-thread 019f3e8d-8307-7052-b367-57e78f3316ae \
  --claude-session 019f3ef0-1111-7222-8333-c26aa5d38893

# A Codex-only session with no Claude parent transcript:
oaw session snapshot "$CODEX_THREAD_ID" \
  --codex-only \
  --partial \
  --slug codex-dogfood
```

By default the command finds the Claude parent transcript plus nested subagent transcripts, task outputs under `tasks/`, workflow run artifacts under `subagents/workflows/`, persisted workflow scripts under `workflows/scripts/`, discoverable Codex rollouts, referenced plugin job logs, and fork parents referenced by explicit Claude/fork markers or `--claude-session`. Use `--codex-only` when the positional ID is a Codex thread with no Claude parent transcript; the command requires that primary rollout even when extra discovery options are supplied. Bare JSON `sessionId` fields do not trigger fork discovery. It writes `manifest.json` with source paths, copy time, file hashes, category, snapshot mode, and transcript completeness. Use `--codex-rollout` for an exact rollout filename or path. Use `--grep` only for a literal that identifies one rollout; ambiguous grep matches fail and should be replaced with explicit `--codex-thread` or `--codex-rollout` flags. Re-run the same command to refresh a partial transcript, preserve nested artifacts, pick up new artifacts, and remove stale files listed in the previous manifest.

Use `--partial` or `--complete` to override automatic transcript completeness,
and `--date` to override the folder date. Test or demo runs can override the
destination with `--output-root` and artifact roots with `--codex-root`,
`--claude-root`, and `--plugin-data-root`. Lookup and snapshot commands share
the `OAW_CODEX_SESSIONS_ROOT` and `OAW_CLAUDE_PROJECTS_ROOT` environment
overrides; their fallback roots are `~/.codex/sessions` and
`~/.claude/projects`.

## Notes and retrospectives

For non-project notes, append the same session trace or a dated observation block without hand-editing headings:

```bash
oaw note session AGT-TSK-session-retrospectives --note "Reviewed retrospective habit."
oaw note observe CDX-RES-routing-evidence \
  --section "Observations" \
  --title "Wrap-up format gap" \
  --body "The evidence note needs a mechanical append path."
```

Create retrospective drafts from a stable template:

```bash
oaw retro create \
  --title "Resolver dogfood" \
  --summary "Captured the resolver workflow and follow-ups."
```

`oaw note session` and `oaw retro create` require a real session ID from a supported harness environment variable unless `--allow-missing-session-id` is explicitly accepted. `oaw note observe` does not require a session ID.
`note observe --section` defaults to `Observations`. Retrospective creation also
accepts `--date` and `--id` overrides; `--force` is required to replace an
existing generated path.

## Import and export

### Safe export ingestion

`oaw ingest safe-export` scans a handoff directory for markdown files and only accepts those marked as safe for import. It is conservative by default and performs a dry-run unless `--write` is passed.

```bash
OAW_INGESTION_ROOT=/path/to/ingestion-root oaw ingest safe-export
OAW_INGESTION_ROOT=/path/to/ingestion-root oaw ingest safe-export --write
oaw ingest safe-export --ingestion-root /path/to/handoff --destination "Imports/Reviewed"
```

Scanned files are accepted when frontmatter indicates one of:

- `export-scope: personal` (preferred)
- `export-approved: personal` (compatibility)
- `safe-export-personal: true` (compatibility)
- `safe-export-personal` tag

Safety evaluation reads frontmatter only. In `--write` mode, accepted files are ingested to `Imports/Safe export` (vault-relative) and rejected files are quarantined under `.rejected/`.

Default handoff path is `OAW_INGESTION_ROOT` (if unset,
`~/obsidian-ingestion`). `--ingestion-root` overrides it. Default destination
is `Imports/Safe export`; `--destination` accepts a vault-relative override.
`--dry-run` is explicit but optional because preview mode is the default.

### Outbound export bundles

Outbound exports require explicit note frontmatter before anything leaves the vault:

```yaml
export-scope: work
return_ingest: true
export_artifacts:
  - scripts/run.sh
```

Export a marked-safe note bundle and validate it on the receiving side:

```bash
oaw export note OAW-TSK-export-example --target work --output-root ~/obsidian-export
oaw export validate ~/obsidian-export/OAW-TSK-export-example --target work
```

The bundle contains `note.md`, optional copied artifacts, and `manifest.json` with vault-relative source paths and checksums. Unmarked notes are refused; validation also refuses paths outside the bundle, tampered notes, wrong targets, missing artifacts, and checksum mismatches. Legacy `safe_for_export: true` plus `export_target: work` remains accepted for existing notes, but new notes should use `export-scope: work` so inbound and outbound safety markers share one schema.
An existing bundle is not replaced unless `export note --force` is supplied.
For validation, `--target` is optional and otherwise defaults to the manifest's
target.

## Link hygiene

`oaw link` supports durable Obsidian wikilink hygiene:

```bash
oaw link check OAW-TSK-cli OAW-TSK-session-lookup
oaw link list OAW-TSK-cli
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --label "Session lookup" \
  --write
oaw link ensure-bidirectional OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link lint
```

`ensure` and `ensure-bidirectional` default to a dry-run preview and append a `[[vault/path|ID]]` link only when the target path is missing. Pass `--write` to apply the append-only section edit.
For one-way `ensure`, `--label` overrides the target ID used as the wikilink
display text.

## Installed vs checkout CLI

Use installed `oaw ...` commands for operational vault writes such as task lifecycle updates, board moves, and session snapshots. Reserve `python bin/oaw ...` for development checks against this checkout, preferably with temp vaults. This keeps approval prompts scoped to stable commands instead of broad interpreter entrypoints; see `AGT-FDBK-allow-listed-skill-scripts`.

After refreshing the uv-managed installation, run `python scripts/check_cli_parity.py`.
It recursively compares every checkout and installed `--help` surface, including
nested commands and options, and fails with a diff when the installed artifact is stale.

## Examples from agent sessions

<details>
<summary><strong>From the 2026-07-10 integration session</strong></summary>

The following commands and outputs come from the session that reviewed, fixed, and integrated the overnight branches. Because the checkout itself was changing, the session deliberately ran `python bin/oaw ...` to dogfood the active version before it reached `main`.

Trace the current Codex thread back to every vault note that recorded it:

```bash
python bin/oaw session lookup "$CODEX_THREAD_ID"
```

```text
Session: <codex-thread-id>
Vault matches:
- Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md | id: OAW-TSK-overnight-branch-review
- Projects/Obsidian Agent Workflow/Tasks/Session artifact snapshot command.md | id: OAW-TSK-session-snapshot
```

Inspect task metadata without opening the full note body:

```bash
python bin/oaw resolve --meta OAW-TSK-overnight-branch-review
```

```text
type: task
status: done
project: Obsidian Agent Workflow
id: OAW-TSK-overnight-branch-review
aliases:
  - OAW-TSK-overnight-branch-review
priority: 1
effort: M
```

Check whether two notes already link to each other before applying an append-only repair:

```bash
python bin/oaw link check \
  OAW-TSK-overnight-branch-review \
  OAW-TSK-session-snapshot
```

```text
Left: Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md | id: OAW-TSK-overnight-branch-review
Right: Projects/Obsidian Agent Workflow/Tasks/Session artifact snapshot command.md | id: OAW-TSK-session-snapshot
Left links right: no
Right links left: no
```

Refuse an outbound export when the source note has not explicitly opted in:

```bash
python bin/oaw export note OAW-TSK-export-notes-to-work \
  --target work \
  --output-root /tmp/oaw-dogfood-export
```

```text
oaw: export requires export-scope: work in note frontmatter (legacy safe_for_export: true plus export_target: work is also accepted)
```

Snapshot a completed multi-agent review into a manifest-backed bundle. The real session UUID was redacted here; the run copied 81 parent, workflow, and rollout artifacts:

```bash
python bin/oaw session snapshot <claude-session-id> \
  --slug overnight-review-dogfood \
  --output-root /tmp/oaw-dogfood-snapshot \
  --complete
```

```text
Snapshot: /tmp/oaw-dogfood-snapshot/2026-07-09-overnight-review-dogfood
Manifest: /tmp/oaw-dogfood-snapshot/2026-07-09-overnight-review-dogfood/manifest.json
Copied: 81
Transcript: complete
```

Record an integration checkpoint without changing task status or moving its board card:

```bash
python bin/oaw task note OAW-TSK-overnight-branch-review \
  --note "Second integration batch ready." \
  --checks "python -m unittest (52 tests OK)"
```

```text
Updated: Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md
Status: active
Board: unchanged
```

Complete the task and then move its cross-project priority card to Done:

```bash
python bin/oaw task complete OAW-TSK-overnight-branch-review \
  --note "Merged and verified the reviewed overnight branches." \
  --checks "python -m unittest (59 tests OK)"
python bin/oaw board done OAW-TSK-overnight-branch-review
```

```text
Updated: Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md
Status: done
Board: updated
Board: Projects/Next steps.md
Column: Done
Matched: OAW-TSK-overnight-branch-review
```

</details>

<details>
<summary><strong>Recovering an interrupted review without overstating it</strong></summary>

Start from durable task identity and read-only evidence, not the last agent's summary:

```bash
python bin/oaw resolve --full OAW-TSK-overnight-branch-review
git status --short
git log --oneline --decorate -12
```

Then inspect the preserved workflow report and journal at their documented
placeholder paths, such as `/path/to/workflow/report.md` and
`/path/to/workflow/journal.jsonl`. A killed workflow is not a completed
workflow, and a completed review phase is not evidence that the merge or final
verification phase ran. An interrupted worker can also consume substantial
tokens without producing journaled results; report that absence as uncertainty,
not success.

Keep unrelated dirty state out of the recovery branch. After confirming who
owns each changed path, preserve those edits on their own branch and commit,
then create the recovery worktree from an explicitly clean ref:

```bash
git switch -c preserved-dirty-state
git add -- path/to/confirmed-file
git commit -m "chore: preserve interrupted session state"
git switch main
git gtr new recovered-review --from main --no-fetch --yes
cd "$(git gtr go recovered-review)"
git status --short
```

Resolve the existing related task before creating a new one, then record only
the checkpoint actually reached:

```bash
python bin/oaw resolve --full OAW-TSK-session-snapshot
oaw task note OAW-TSK-session-snapshot \
  --note "Recovered the review evidence; merge and verification remain pending." \
  --checks "git status --short; inspected workflow report and journal"
```

After independent review and verification, use `task complete` with the checks
that really ran. Preserve the session and verify or append the durable SR link
with installed operational commands rather than embedding transcript content:

```bash
oaw session snapshot "$SESSION_ID" --partial --slug aborted-review-recovery
oaw link check OAW-TSK-session-snapshot SR-TSK-oaw-aborted-review-recovery-retro
oaw link ensure-bidirectional \
  OAW-TSK-session-snapshot SR-TSK-oaw-aborted-review-recovery-retro \
  --section Related --write
```

</details>

<details>
<summary><strong>Earlier examples</strong></summary>

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
oaw resolve obs:CDX     # resolves the matching Projects/<Project>/Index.md note
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
Updated: Projects/Obsidian Agent Workflow/Tasks/Resolver and lifecycle CLI.md
Status: done
Board: updated
```

</details>

## Development worktrees

This repo keeps `git gtr` worktrees under `.worktrees/` via `.gtrconfig`.
That path is ignored by Git and stays inside the repository checkout, so
sandboxed agents can use isolated worktrees without creating a sibling checkout
outside the repo writable root.
