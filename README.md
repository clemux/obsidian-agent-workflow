# obsidian-agent-workflow

Local-first tooling for agent-driven Obsidian workflows: the `oaw` CLI resolves vault reference IDs from note frontmatter and records task lifecycle and session traces on project notes.

## Status and caveats

This project has been written entirely by AI. The repository owner has not read or reviewed any of the code. Use it at your own risk.

This repository is tooling tailored for a local Obsidian and agent workflow. It is not intended as reusable software, and it probably does not make sense to install or use as-is. Set `OAW_VAULT` to the vault root before running any vault command.

## Install

Install with `uv` from the repo checkout:

```bash
cd /path/to/obsidian-agent-workflow
uv tool install .
```

This builds a snapshot into a uv-managed tool environment, so the installed
`oaw` is decoupled from the checkout: switching branches or editing `src/oaw/`
does not change the installed command. After merging changes, refresh its recorded
local source and verify the installed command surface with:

```bash
uv tool upgrade oaw
uv run python scripts/check_cli_parity.py
```

If `uv tool upgrade oaw` does not rebuild the local checkout source, use
`uv tool install --reinstall .` and rerun the parity check.

During development, run the checkout through the project environment with
`uv run python bin/oaw ...` and set `OAW_VAULT` (preferably to a temporary vault).
The CLI depends on `typer` at runtime, so bare `python bin/oaw ...` fails with
`ModuleNotFoundError: No module named 'typer'`. The installed `oaw` command
carries its own dependencies and needs no prefix.

### Agent skills

The repository also contains five agent skills:

- `skills/obsidian-capture` preserves side observations in the vault capture workflow.
- `skills/oaw` documents ID resolution and lifecycle operations.
- `skills/oaw-task-execution` runs repository implementation and review with preflight checks, GTR isolation, and proportional delegation.
- `skills/oaw-task-review` runs a resumable, one-task-at-a-time project status review.
- `skills/oaw-research` handles provider-visible research handoff and finished-report intake.

Link them into the Codex, Claude, and neutral skill directories so edits in the
checkout remain live:

```bash
for skill_root in "${CODEX_HOME:-$HOME/.codex}/skills" "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  mkdir -p "$skill_root"
  for skill in obsidian-capture oaw oaw-task-execution oaw-task-review oaw-research; do
    ln -s "$(pwd)/skills/$skill" "$skill_root/$skill"
  done
done
```

Skip a link that already exists. Validate each package with the skill creator's
`quick_validate.py` before sharing changes.

### Shell completion

The CLI is built with Typer, so it ships shell completion:

```bash
oaw --install-completion          # install for the current shell
oaw --show-completion bash        # or print the script to inspect/copy
```

Completion covers commands and option names. It does not complete vault IDs or
project aliases; dynamic completion for those is tracked separately as
`OAW-TSK-add-dynamic-completion-for-oaw-ids-and-project-aliases`.

### CLI contract

The Typer frontend owns the command tree and its native contract tests. They pin
the complete command-path set, the exit classes (0 success, 1 domain error, 2
usage error), stdout/stderr routing, accepted choice values, option conflicts,
and no-write behavior when validation fails.

`scripts/check_cli_parity.py` is a separate maintenance check: it compares the
installed snapshot with this checkout's help surfaces and source bytes. It does
not compare against a historical frontend or filesystem golden.

## Development checks

The project uses a `src/` package layout. The executable `bin/oaw` is only a
checkout shim; installed commands use the `oaw.cli:main` entry point.
Shared error, note-boundary, and hand-rolled frontmatter helpers live in
`src/oaw/errors.py`, `src/oaw/notes.py`, and `src/oaw/frontmatter.py`; their
direct unit tests run without a CLI subprocess or vault fixture.

Run the complete local gate with:

```bash
ruff check
ruff format --check
pyrefly check
pytest
```

GitHub Actions runs the same four checks on pushes and pull requests. Tests
continue to exercise the CLI through subprocesses and isolated temporary vaults.

`OAW_VAULT` is required for commands that access the vault; there is no built-in
machine-specific default. An unset or blank value fails with exit code `1` and a
clear configuration error. For example:

```bash
export OAW_VAULT=~/vaults/example
```

## Table of contents

- [Command overview](#command-overview)
- [Agent skills](#agent-skills)
- [Resolving references](#resolving-references)
- [Listing notes](#listing-notes)
- [Task lifecycle](#task-lifecycle)
- [Task views](#task-views)
- [Sessions](#sessions)
  - [Session lookup](#session-lookup)
  - [Session snapshots](#session-snapshots)
- [Notes and retrospectives](#notes-and-retrospectives)
- [Agent feedback](#agent-feedback)
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
| [`oaw session`](#sessions) | Look up a session ID across notes and artifacts; snapshot session artifacts |
| [`oaw note` / `oaw retro`](#notes-and-retrospectives) | Append session traces or observations; create retrospective drafts |
| [`oaw feedback`](#agent-feedback) | Create dated, durable agent-feedback notes |
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

Rank, project extra columns, and emit a machine-readable view without a shell
loop over each note:

```bash
oaw list --project "Obsidian Agent Workflow" --status todo --sort priority
oaw list --project "Obsidian Agent Workflow" --fields id,priority,effort,title
oaw list --project "Obsidian Agent Workflow" --sort priority --goal
oaw list --project "Obsidian Agent Workflow" --sort priority \
  --fields id,priority,effort,goal --json
```

- `--sort {priority,effort,title}` orders rows by the vault-wide 1/2/3 priority
  rank, then effort rank (`S`, `M`, `L`), then title; missing priority or effort
  sorts last. Without `--sort`, rows stay in vault order.
- `--fields` replaces the default `id,status,title,path` columns with a
  comma-separated projection; unknown fields error clearly. Projectable fields
  are `id`, `status`, `title`, `path`, `goal`, `priority`, `effort`,
  `preparedness`, `type`, `project`, `created`, and `execution`.
- `--goal` adds a snippet column sourced from each note's `## Problem` section
  first content line, without opening the whole body in the caller.
- `--json` emits the same projected, sorted records as an array of objects.

## Project workspace creation

Create the minimal index for a first-class project from the vault's native template:

```bash
OAW_VAULT=~/vaults/example oaw project create \
  --name "Example Project" \
  --alias EXP \
  --goal "Maintain the example project's durable workspace." \
  --repo ~/dev/example-project \
  --tag example-project
```

The command creates only `Projects/Example Project/Index.md`; task creation adds a
`Tasks/` folder later when work is selected. It renders the vault-relative
`Templates/Small project index.md` by default, or one custom path supplied with
`--template`. The template must contain exactly one H1 with `{{title}}`, exactly one
`## Goal`, and exactly one `## Current state`; optional native `{{date}}` tokens are
also resolved. Any remaining template expression is rejected.

Generated frontmatter records the active project, `<ALIAS>-index` ID and alias,
deduplicated project tags, optional quoted `repo`, and current harness session ID.
Extra `--tag` values must be lowercase safe identifiers and retain first-seen order.
`--name`, `--alias`, `--goal`, optional `--repo`, and repeatable `--tag` values are
validated before writing. Existing project folders, duplicate IDs or aliases,
malformed templates, and unsafe paths fail without creating a partial project. A
stable harness session ID is required unless `--allow-missing-session-id` is explicit.
The command deliberately creates only the project index. It does not create a
project-local Base, task notes, bookmarks, or edits to `Projects/Index.md`; the
index embeds the shared project workspace Base supplied by the vault template.

## Task lifecycle

`oaw task` provides a conservative lifecycle for tasks under `Projects/*/Tasks`,
`Agents/Tasks`, and root `Tasks/`:

```bash
oaw task backlog OAW-TSK-cli --note "Parked until the dependency is ready."
oaw task promote OAW-TSK-cli --note "Selected for the next session."
oaw task start OAW-TSK-cli --note "Implemented resolver and lifecycle CLI."
oaw task pause OAW-TSK-cli --note "Paused this session's run."
oaw task review OAW-TSK-cli --note "Ready for review." --checks "pytest"
oaw task complete OAW-TSK-cli --note "Verified end-to-end." --checks "pytest"
oaw task note OAW-TSK-cli --note "Reviewed a related session." --checks "pytest"
oaw task priority OAW-TSK-cli --priority 1 --note "Raised after cross-project triage."
oaw task preparedness OAW-TSK-cli --state prepared \
  --note "Execution is designed and known blockers are recorded."
```

Lifecycle commands update task frontmatter and append an `## Agent sessions` trace. Task-note status is the single lifecycle source of truth, surfaced through project and cross-project Bases. The lifecycle order is `Backlog` -> `Todo` -> `Active` -> `Review` -> `Done`. Agent-run records live independently under `Agents/Runs/`: repeating `start` in the same provider/session refreshes one record, while another session gets a distinct record. `pause` changes only the caller's run to `paused`; task status remains active. `review` and `complete` refuse while another session is still running, including a run whose derived age is stale. Task and run changes are committed together.

Tasks may declare `execution: human`, `agent`, or `hybrid`. An absent value becomes `agent` only when `start` begins a run. Human tasks remain UI-managed and reject agent lifecycle transitions. `start`, `pause`, `review`, and `complete` require a real harness session ID and do not offer `--allow-missing-session-id`; `backlog`, `promote`, `task note`, `task priority`, `task preparedness`, and task-relation mutations retain the explicit missing-ID trace path. With a real ID, session-writing commands append it as a quoted string to a deduplicated `session-ids` block list while preserving existing entries, comments, and any legacy scalar `session-id`.

### Session phase titles

The OAW skill also keeps the owning agent session recognizable as
`[MARKER] CANONICAL-TASK-ID` when the client exposes an agent-callable rename
operation. The markers are `[DESIGN]` for design, `[I]` for implementation,
`[R]` for review or verification, `[W]` for wrap-up, and `[DONE]` for completed
work. Design and done stay long because `[D]` would be ambiguous; the middle
phases use compact markers to leave room for the task ID in narrow sidebars.

The title describes current session work, not durable task state. Design and
wrap-up do not change lifecycle status. `[R]` does not move a task to `review`;
only a successful `oaw task review` does that. `[DONE]` is set only after a
successful `oaw task complete`. Incidental references do not take title
ownership from the primary task, and resuming a session re-synchronizes a stale
title when task ownership and phase are clear.

This is capability-gated. Codex Desktop can use its callable task rename. Claude
Code exposes no rename to the running agent, so the skill leaves the title alone
there; `claude --name` is a launcher flag, not an in-session agent capability.
Clients without such a capability continue the OAW workflow unchanged and never
mutate task state to compensate for the missing UI operation.

A Claude Code title is instead set out of band by a hook, which reads the `oaw
task` commands the agent already runs and asks nothing of the skill. See
[docs/claude-code.md](docs/claude-code.md).

Use `oaw task note` when you need to append a dated `## Agent sessions` entry without changing `status`. It accepts optional `--checks`, works in any task status, and refreshes `last_event_at` only when the caller already has a matching running record; it never creates one.

Use `oaw task priority` to set an existing task's vault-wide priority to `1`, `2`,
or `3`. It preserves the task status, run records, unrelated
frontmatter formatting, and any inline priority comment while appending an agent
session trace. Unsupported task locations and malformed or duplicate priority
fields are rejected before writing.

Preparedness is independent from lifecycle status and dependency state. The canonical
values are `needs-triage` (the intended outcome, value, or scope is uncertain),
`needs-design` (the task is accepted but not sufficiently designed), and `prepared`
(execution is sufficiently designed and all known blockers are identified). A prepared
task may remain blocked or unscheduled. `oaw task preparedness` changes only this
property, appends provenance, and leaves status and run records unchanged. Existing
tasks without the property are unassessed; `unassessed` is not assignable.

Inspect and administer the registry without changing task lifecycle state:

```bash
oaw run list --task OAW-TSK-cli --state running
oaw run list --json
oaw run close AGT-RUN-OAW-TSK-cli-codex-0123456789ab --reason "superseded session"
oaw run audit
```

`run list` marks records older than 24 hours as `stale` without changing them or releasing concurrency. `run close` requires the real closer session, preserves the original `agent_session_id`, appends the closer to `session-ids`, and changes only the run. `run audit` reports registry inconsistencies.

New task notes are created with `oaw task create` instead of hand-writing frontmatter:

```bash
oaw task create --project obs:OAW --title "Example task" \
  --note "Initial problem statement." --priority 2 --effort M
oaw task create --project "Obsidian Agent Workflow" --title "Chosen task" \
  --status todo --preparedness prepared
oaw task create --project obs:OAW --title "Agent task" --execution agent --start
oaw task create --from-capture obs:OAW-CAP-routing-regression \
  --title "Investigate routing regression" --status todo
oaw task create --from-capture obs:OAW-CAP-urgent --title "Handle urgent request" --start
```

`--project` accepts a project alias (`obs:OAW`) or a folder name under `Projects/`. The note is created under the project's `Tasks/` folder with standard frontmatter and optional `priority`, `effort`, and `execution`. The task ID defaults to `<ALIAS>-TSK-<slug>`; status defaults to `backlog`, with `--status todo` for selected work. Preparedness defaults to `needs-triage`; `--preparedness` may select any canonical value explicitly. `--start` works with or without a capture and atomically creates the active task and running record; it defaults execution to `agent`, requires a real session, and rejects `--execution human`. Duplicate IDs and existing paths fail without writing anything.

The generated `created` value is a timezone-aware UTC datetime. Repeatable `--tag`
values must be lowercase safe identifiers, are deduplicated in first-seen order, and
precede `source-capture` on promoted tasks. Non-started creation records a session
trace and permits `--allow-missing-session-id` only when an untraceable trace is
explicitly accepted.

When an actionable capture becomes material work, pass its stable ID with `--from-capture`. The project and title default to the capture's project folder and heading, while explicit `--project` and `--title` still override them. The command preserves the capture note and body, records its ID as `source-capture` on the task, adds durable links in both directions, appends the task wikilink to the capture's `destinations` frontmatter, and only then changes the capture to `status: triaged`. Those writes commit together and roll back together on failure. The capture's `Outcome` remains an expected-next-shape statement; promotion does not replace it with completion prose. Choose backlog (default), `--status todo`, or `--start` for immediate `active` intent.

### Semantic task relationships

Task relationships use forward-only frontmatter lists of durable task links:

```yaml
blocked-by:
  - "[[Projects/Example/Tasks/Dependency|EXP-TSK-dependency]]"
follows:
  - "[[Projects/Example/Tasks/Earlier work|EXP-TSK-earlier-work]]"
follow-up-to:
  - "[[Projects/Example/Tasks/Original work|EXP-TSK-original-work]]"
```

Manage and inspect them through OAW rather than hand-maintaining inverse fields:

```bash
oaw task relation add EXP-TSK-source blocked-by EXP-TSK-dependency \
  --note "Dependency must finish first."
oaw task relation remove EXP-TSK-source blocked-by EXP-TSK-dependency \
  --note "Dependency was replaced explicitly."
oaw task relation list EXP-TSK-source
oaw task relation list EXP-TSK-dependency --incoming --json
oaw task relation validate EXP-TSK-source
oaw task relation validate --json
```

`blocked-by` is a hard dependency, `follows` is non-enforcing sequence guidance,
and `follow-up-to` records provenance. Only a target in `done` satisfies a hard
dependency; `superseded` remains blocking until the link is explicitly removed or
redirected. Broken targets, non-task targets, duplicates, self-links, and per-type
cycles are invalid. A blocked task may start for design or partial work and reports
its blockers prominently, but `review` and `complete` refuse unresolved or invalid
hard blockers. Incoming relationships and blocker state are derived; no inverse or
cached `blocked` property is written. Generic `## Related` links remain unchanged.
`task relation validate` is authoritative for graph-wide invariants such as cycles
and canonical link shape. The Base directly detects unresolved, non-task, self, and
duplicate blockers and derives satisfaction for CLI-maintained relationships.

## Research packet lifecycle

Create a project's `Research/<track>/Prompt.md` from the vault template instead of rebuilding the packet by hand:

```bash
OAW_VAULT=~/vaults/example oaw research scaffold \
  --project "Example Project" \
  --track "architecture/provider-choice" \
  --title "Provider choice" \
  --date 2026-07-12
```

The command creates `Prompt.md`, `Synthesis.md`, and the shared folder-scoped `Bases/Research packet.base`. The default template is the vault-relative `Templates/Research packet.md`; override it with `--template <vault-relative-path>`. It fills project, track, title, and date and refuses to replace an existing prompt unless `--force` is explicit. A forced scaffold never replaces an existing synthesis.

The template must have exactly one `## Deep research prompt` heading followed only by one non-empty fenced `text` block. That block is the complete provider-visible request and its contents are the exact one-click copy payload; fence markers and local metadata are excluded. Project/track tokens are rejected beyond the boundary.

After launching one provider run, register it atomically:

```bash
OAW_VAULT=~/vaults/example oaw research start \
  --project "Example Project" \
  --track "architecture/provider-choice" \
  --source "ChatGPT Pro" \
  --url "https://example.com/share/run"
```

`start` requires a safe, unique human source label and an absolute HTTP(S) URL. It creates only `Results - <Source>.md` with `status: running`, records source/URL/date provenance, appends the run under `## Running research sessions`, and creates a missing synthesis or shared Base without replacing existing content. Validation and all writes complete transactionally.

Use the `oaw-research` skill and helper to preflight and print the exact fenced-block
contents, then to ingest the finished report while preserving its raw artifact.
Native report intake remains intentionally outside the core `oaw` CLI.

## Task views

Task-note frontmatter is the only lifecycle state store. Project indexes embed the
shared `Templates/Project workspace.base#Work queue` view, while the aggregate
cross-project task Base lives at `Projects/Cross-project tasks.base`. Use the
cross-project view when choosing work across OAW and adjacent queues: it includes
`Projects/*/Tasks`, `Agents/Tasks`, and root `Tasks/`; keeps `backlog`, `todo`,
`active`, `review`, and legacy `open` tasks visible; and excludes terminal `done`
and `superseded` work.

Priority is a vault-wide 1/2/3 scale: `1` is urgent, blocking, or unusually
high-leverage; `2` is normal next-session work with clear value; `3` is useful
backlog work. Cross-project usefulness can raise priority, and the Base sorts by
priority, then effort (`S`, `M`, `L`), then title.

The comprehensive view keeps lifecycle, preparedness, and relationship columns visible.
Focused views distinguish selected and executable work (`todo + prepared + unblocked`),
prepared backlog, tasks needing triage or design (including unassessed legacy notes),
and blocked tasks. Dependency state is derived from `blocked-by` target statuses rather
than cached in task frontmatter; a Base refresh may be needed after a linked task changes.

## Sessions

### Session lookup

`oaw session lookup` looks up a session/thread ID across vault notes and session artifacts:

```bash
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc --codex-root /tmp/example-codex-sessions --claude-root /tmp/example-claude-projects
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc --verbose
```

`oaw session lookup <id>` follows a two-step resolution strategy:

- First it scans the vault and prints matching note paths and frontmatter IDs for the literal ID.
- If no vault match is found, it scans artifact roots and prints a synopsis of discovered session artifacts.
- If no match is found anywhere, it exits `0` and prints a clear *not logged* message.

Pass `--verbose` to add best-effort metrics for each harness artifact. For Codex rollout
JSONL, `Started` and `Ended` are the earliest and latest valid record timestamps and
`Duration` is their elapsed wall-clock interval (`HH:MM:SS`). `Turns` counts JSONL
message records whose role is `user` or `assistant`; injected instructions are therefore
included when they are stored as user messages. `Tokens` uses the latest cumulative
`total_token_usage` snapshot, rather than summing cumulative snapshots. Metrics that the
artifact does not record are printed as `unavailable`. Other harness formats currently
report unavailable metrics, leaving room for provider-specific parsers later. Without
`--verbose`, output is unchanged.

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

`oaw note session` and `oaw retro create` require a real session ID from a supported harness environment variable unless `--allow-missing-session-id` is explicitly accepted. `note session` maintains the same deduplicated `session-ids` frontmatter list as task lifecycle writes; retrospective creation initializes that list. `oaw note observe` does not require a session ID.
`note observe --section` defaults to `Observations`. Retrospective creation also
accepts `--date` and `--id` overrides; `--force` is required to replace an
existing generated path.

## Agent feedback

Use `oaw feedback create` to retain one concrete friction, verified behavior,
idea, or bug as a dated note under `Agents/Feedback/`:

```bash
oaw feedback create \
  --title "Body-file validation is unclear" \
  --type pain \
  --scope "oaw feedback create" \
  --body "The command should say which body source failed." \
  --command "oaw feedback create" \
  --tag cli
```

`--title`, `--type` (`pain`, `verified`, `idea`, or `bug`), `--scope`, and
exactly one of `--body` or `--body-file` are required. `--body-file -` reads
standard input. The command derives `AGT-FDBK-<slug>` from the title and writes
`Agents/Feedback/<date> <title>.md`; use `--id` or `--date` to override those
derived values. Extra `--tag` values are validated, ordered, deduplicated, and
serialized as quoted YAML strings. It refuses duplicate IDs or paths and never
overwrites an existing note. Feedback creation records a real harness session
ID unless `--allow-missing-session-id` is explicit.

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

Use installed `oaw ...` commands for operational vault writes such as task lifecycle updates and session snapshots. Reserve `uv run python bin/oaw ...` for development checks against this checkout, preferably with temp vaults. This keeps approval prompts scoped to stable commands instead of broad interpreter entrypoints; see `AGT-FDBK-allow-listed-skill-scripts`.

After refreshing the uv-managed installation, run
`uv run python scripts/check_cli_parity.py`.
It recursively compares every checkout and installed `--help` surface, including
nested commands and options, and fails with a diff when the installed artifact is stale.

## Examples from agent sessions

<details>
<summary><strong>From the 2026-07-10 integration session</strong></summary>

The following commands and outputs come from the session that reviewed, fixed, and integrated the overnight branches. Because the checkout itself was changing, the session deliberately ran `python bin/oaw ...` to dogfood the active version before it reached `main`.

> These transcripts are kept verbatim as a record of that session. They predate the
> Typer migration, when the CLI was dependency-free; today the equivalent checkout
> invocation is `uv run python bin/oaw ...`.

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

Record an integration checkpoint without changing task status:

```bash
python bin/oaw task note OAW-TSK-overnight-branch-review \
  --note "Second integration batch ready." \
  --checks "uv run pytest (tests passed)"
```

```text
Updated: Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md
Status: active
```

Complete the task after verification:

```bash
python bin/oaw task complete OAW-TSK-overnight-branch-review \
  --note "Merged and verified the reviewed overnight branches." \
  --checks "uv run pytest (tests passed)"
```

```text
Updated: Projects/Obsidian Agent Workflow/Tasks/Overnight branch review and merge.md
Status: done
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

Record implementation provenance on the task itself. Lifecycle commands capture the current agent session and update task status:

```bash
CODEX_THREAD_ID=019f3b71-14db-7480-b0c5-8836714deacc \
  oaw task start OAW-TSK-project-alias-resolution \
  --note "Captured a project alias resolution failure."

CODEX_THREAD_ID=019f3e36-ee4d-7220-8e5c-c26aa5d38893 \
  oaw task complete OAW-TSK-cli \
  --note "Added capture listing and updated skill docs." \
  --checks "pytest"
```

Example lifecycle output:

```text
Updated: Projects/Obsidian Agent Workflow/Tasks/Resolver and lifecycle CLI.md
Status: done
```

</details>

## Development worktrees

This repo keeps `git gtr` worktrees under `.worktrees/` via `.gtrconfig`.
That path is ignored by Git and stays inside the repository checkout, so
sandboxed agents can use isolated worktrees without creating a sibling checkout
outside the repo writable root.
