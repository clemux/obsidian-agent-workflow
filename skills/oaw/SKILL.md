---
name: oaw
description: This skill should be used when an `obs:`-prefixed reference appears (e.g. `obs:OAW-TSK-cli`), when a frontmatter reference ID such as `AGT-*`, `SR-*`, `CDX-*`, `FAB-*`, or `OAW-*` needs resolving to a note in the user's Obsidian vault, when tracing a session/thread id through vault notes and session artifacts, when starting or completing a project task note with an agent-session trace, or when listing a project's tasks/captures. Provides the `oaw` CLI workflow. (`PMX-*` IDs have a dedicated `pmx` skill.)
---

# oaw — Obsidian ID resolution and task lifecycle

## Overview

The `oaw` CLI resolves reference IDs against note frontmatter (`id` and `aliases` only) in the user's Obsidian vault, and records agent work on project task notes. Use it instead of grepping the vault or searching local agent state: body-text mentions of an ID are not the note itself, so text search finds decoys; frontmatter matching is narrow and auditable.

Set `OAW_VAULT` when running against a non-default vault, tests, demos, or automation.

For checkout development, shared errors, note splitting/reading, and the
hand-rolled frontmatter parser/mutators are importable from `oaw.errors`,
`oaw.notes`, and `oaw.frontmatter`. Keep direct unit coverage beside CLI
contract tests when changing these helpers.

## Resolving IDs

Treat `obs:<ID>` as a lookup trigger — `oaw` strips the `obs:` prefix automatically; it is not part of the stored ID. Matching is exact and case-sensitive.

```bash
oaw resolve obs:OAW-TSK-cli   # default view: ID, path, title, matched-by, frontmatter, outline
oaw resolve obs:CDX           # short project alias -> matching project index, e.g. CDX-index
oaw resolve --path OAW-TSK-cli     # absolute path only
oaw resolve --meta OAW-TSK-cli     # frontmatter only (status, project, priority, ...)
oaw resolve --outline OAW-TSK-cli  # headings with line numbers
oaw resolve --json OAW-TSK-cli     # machine-readable (path, frontmatter, outline)
```

The default view answers most questions. Use `--full` (entire note body) only after deciding the body is actually needed.

Short uppercase project aliases such as `obs:CDX` or `obs:OAW` resolve only through matching notes at `Projects/<Project>/Index.md` (`CDX-index`, `OAW-index`) and only when there is no exact frontmatter `id` or `aliases` match. Ambiguous project aliases are errors with candidate paths; do not treat a failed `obs:<project alias>` as a literal vault folder.

On failure `oaw` exits non-zero with a clear message: "no note with frontmatter id or alias" for a miss, or a candidate-path list when an ID is duplicated. Surface that error to the user instead of guessing a path or falling back to text search.

## Session phase titles

When one resolved task owns the session and a rename operation is already
available in the tool set, synchronize `[MARKER] CANONICAL-ID` on initial
ownership, resume, and material phase changes: design `[DESIGN]`, implementation
`[I]`, review or verification `[R]`, wrapping up `[W]`, and completed `[DONE]`.
Incidental references do not transfer ownership. Titles never change OAW state:
`[I]` normally accompanies `task start`, `[R]` does not imply `status: review`,
and `[DONE]` is allowed only after `task complete` succeeds.

If no agent-callable rename operation is already exposed, silently skip title
synchronization. Do not investigate support, announce the limitation, ask the
user to rename, spawn or resume a client, or mutate task data as compensation.
Only when maintaining, evaluating, or explicitly discussing title sync, read
`references/session-phase-title-evaluation.md` for rationale and client evidence.

## Session lookup

Use `oaw session lookup <id>` when you need to trace a literal session/thread id quickly:

- Searches the vault first and reports matching note paths plus frontmatter ids.
- If nothing matches, searches harness artifacts and prints a session synopsis.
- If still missing, exits `0` with a clear not-logged message.

```bash
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc --codex-root /tmp/example-codex-sessions --claude-root /tmp/example-claude-projects
oaw session lookup 019f3b71-14db-7480-b0c5-8836714deacc --verbose
```

`--verbose` adds best-effort per-artifact metadata. For Codex rollout JSONL, timestamps
are the earliest and latest valid record timestamps, duration is their elapsed wall-clock
interval, turns count message records by `user` and `assistant` role (including injected
instructions stored as user messages), and tokens come from the latest cumulative
`total_token_usage` snapshot. Missing metrics and unsupported harness formats are shown
as `unavailable`; default output remains unchanged.

Override the harness roots for demo and tests with flags or the shared `OAW_CODEX_SESSIONS_ROOT` and `OAW_CLAUDE_PROJECTS_ROOT` environment variables; fallback roots are `~/.codex/sessions` and `~/.claude/projects`.

## Listing project notes

To survey a project's tasks, list them instead of resolving one by one:

```bash
oaw list --project "Obsidian Agent Workflow"   # tab-separated: id, status, title, relative path
oaw list --project "Obsidian Agent Workflow" --status active
```

The project name is the folder name under `Projects/` in the vault. `task` is the default note type.

Some projects also use atomic capture notes for evidence/inbox items. List captures by frontmatter instead of opening a long inbox note:

```bash
oaw list --project Fable --type capture
```

Capture listing hides `status: archived` notes by default. Use `--include-archived` only for historical/provenance work, or `--status archived` when the archived set is the explicit target. For archived captures, prefer `oaw resolve --meta` or default `oaw resolve` first; use `--full` only after confirming the archived body is needed.

## Safe export ingestion

Use `oaw ingest safe-export` to move approved handoff notes into the vault:

```bash
OAW_INGESTION_ROOT=/path/to/ingestion-root \
  oaw ingest safe-export
OAW_INGESTION_ROOT=/path/to/ingestion-root \
  oaw ingest safe-export --write
oaw ingest safe-export --ingestion-root /path/to/handoff --destination "Imports/Reviewed"
```

- Scans markdown files in the handoff folder:
  - default: `OAW_INGESTION_ROOT`
  - fallback: `~/obsidian-ingestion`
- Reads frontmatter only; no body text is used for acceptance.
- Accepted markers (frontmatter):
  - `export-scope: personal` (preferred)
  - `export-approved: personal`
  - `safe-export-personal: true`
  - `safe-export-personal` tag
- Dry-run by default: it reports what would be ingested or rejected without writing.
- `--ingestion-root` overrides the environment/default source and `--destination` sets a vault-relative destination.
- `--write` performs the actual move:
  - accepted notes go to the vault-relative `Imports/Safe export`
  - rejected notes go to `.rejected/` in the handoff path

Safety is explicit by design: only frontmatter-marked files are ingested.

## Project workspace creation

Use the vault template to create the minimal project index before creating tasks for a
new repository or workstream:

```bash
OAW_VAULT=~/vaults/example oaw project create \
  --name "Example Project" \
  --alias EXP \
  --goal "Maintain the example project's durable workspace." \
  --repo ~/dev/example-project \
  --tag example-project
```

- `create` writes only `Projects/<name>/Index.md`. It does not create `Tasks/`, a
  project-local Base, bookmarks, or an entry in `Projects/Index.md`; the index
  embeds the vault's shared project workspace Base.
- The default template is `Templates/Small project index.md`; `--template` accepts one
  alternative vault-relative path. The template must have exactly one H1 containing
  `{{title}}`, one `## Goal`, and one `## Current state`. OAW also resolves optional
  native `{{date}}` tokens and rejects unresolved template expressions.
- The command sets `type: project`, a slugged `project`, `status: active`, optional
  quoted `repo`, `<ALIAS>-index` as the ID and alias, `projects` plus project tags, and
  real session provenance. `--tag` is repeatable; extra tags must be lowercase safe
  identifiers and are deduplicated in first-seen order.
- Project names must be safe one-segment folder names and aliases must match
  `[A-Z][A-Z0-9]{1,7}`. User values, template structure, destination absence, and ID
  uniqueness are all checked before the transactional write. There is no overwrite or
  `--force` path.
- A real harness session ID is required by default. Pass
  `--allow-missing-session-id` only when the user explicitly accepts an untraceable
  project creation.

## Project task lifecycle

Lifecycle writes apply to task notes under `Projects/*/Tasks`, `Agents/Tasks`, and
root `Tasks/`:

```bash
oaw task create --project obs:OAW --title "Example task" --note "Initial problem statement."
oaw task create --from-capture obs:OAW-CAP-routing-regression \
  --title "Investigate routing regression" --status todo
oaw task create --from-capture obs:OAW-CAP-urgent \
  --title "Handle urgent request" --start
oaw task backlog OAW-TSK-cli --note "Parked until the dependency is ready."
oaw task promote OAW-TSK-cli --note "Selected for the next session."
oaw task start OAW-TSK-cli --note "Started resolver implementation."
oaw task pause OAW-TSK-cli --note "Paused this session's run."
oaw task review OAW-TSK-cli --note "Ready for review." --checks "pytest"
oaw task complete OAW-TSK-cli --note "Finished and verified." --checks "pytest"
oaw task note OAW-TSK-cli --note "Recorded an independent review." --checks "pytest"
oaw task priority OAW-TSK-cli --priority 1 --note "Raised after cross-project triage."
```

- `create` makes a new project task with standard frontmatter, a `Problem` section, a durable project-index link, and an `## Agent sessions` trace. `--project` takes a project alias (`obs:OAW`) or folder name; the ID defaults to `<ALIAS>-TSK-<slug>`; status defaults to `backlog` (`--status todo` for selected work); optional values include `--priority 1|2|3`, `--effort S|M|L`, repeatable `--tag`, and `--execution human|agent|hybrid`. `--start` works with or without a capture and atomically creates the active task and run. It defaults execution to `agent`, requires a real session ID, and rejects human execution.
- An actionable request on an `obs:CAP-*` or project capture ID is a promotion trigger: before investigation, implementation, or other material work, run `task create --from-capture <CAP-ID>`. The project and title default from a capture under `Projects/<Project>/`, or may be explicit. Promotion preserves the capture note, body, stable ID, and expected-next-shape `Outcome`; records `source-capture` on the task; adds durable links in both directions; appends the task wikilink to the capture's `destinations` frontmatter; and changes the capture to `triaged` only when every write succeeds. Failure rolls all writes back. Choose backlog (default), `--status todo`, or `--start` for immediate `active` intent. `--start` does not relax session-ID requirements or invent provenance.
- `backlog` sets `status: backlog`; `promote` sets `status: todo`; `start` sets `status: active`; `pause` pauses only the caller's run and leaves task status unchanged; `review` closes the caller's run with reason `review` and sets task status to `review`; `complete` completes the caller's run and sets task status to `done`.
- `complete` requires `--checks` naming the verification actually run; do not fabricate checks.
- `note` appends a dated entry without changing status. If the caller already has a matching running record it refreshes that record; it never creates a run.
- `priority` sets an existing task's priority to `1`, `2`, or `3`, appends a dated agent-session trace, and leaves status and run records unchanged. It preserves unrelated frontmatter formatting and inline priority comments, and rejects unsupported task locations or malformed/duplicate priority fields before writing.
- `backlog`, `promote`, `start`, `review`, and `complete` append a dated entry under `## Agent sessions`; task-note frontmatter is the single lifecycle source of truth and is surfaced through project and cross-project Bases.
- The command's output (`Updated:` / `Status:` / optional `Run:`) confirms the write. To report resulting state, rely on that output plus `oaw resolve --meta` if needed — do not re-read the whole note with `--full`.
- The session ID is read automatically from the first supported harness variable. `start`, `pause`, `review`, `complete`, and `run close` require a real identity and never accept `--allow-missing-session-id`. `task priority` follows the non-run trace policy and accepts that escape hatch only when the user explicitly accepts an untraceable entry.
- With a real harness ID, lifecycle and `task note` writes append it as a quoted string to a deduplicated `session-ids` frontmatter block list, preserving existing entries, comments, and any legacy scalar `session-id`. Unsupported inline, mapping, or ambiguous non-string `session-ids` shapes fail before the note is written. The explicit missing-ID path writes only the body trace; it does not add a synthetic list value.

Agent runs are durable records under `Agents/Runs/`. The same task/provider/session
`start` is idempotent; distinct sessions get distinct records. Multiple sessions may
run the same task, but `review` and `complete` refuse while another record remains
`running`. A derived `stale` age (more than 24 hours) is visible but never changes
state or releases concurrency. Use `oaw run list [--task ID] [--state STATE] [--json]`,
`oaw run close <RUN-ID> --reason <reason>`, and `oaw run audit`. Administrative close
records the real closer while preserving the original agent identity and never
changes task lifecycle state.

Keep `Todo` for near-term chosen work and put unscheduled known work in `Backlog`. When implementation is ready for verification, run `oaw task review ... --checks ...` so the task note records the handoff.

At wrap-up, check whether substantive work occurred. If it did and no task owns it, create or promote the source capture into a task before retrospective closeout. A retrospective may close only after that task is `done` via `oaw task complete ... --checks "<verification actually run>"`; link the retrospective primarily to the completed task and retain the source-capture link as provenance. Do not treat a capture `Outcome` as a completion report.

For any project tracked through OAW, keep pre-implementation architecture,
designs, and proposals on the owning task note by default. Do not create a
tracked repository design document until implementation makes it part of the
project's durable documentation, unless the user explicitly asks for a tracked
artifact. This keeps proposals from looking like shipped behavior and leaves
the working tree clean for a fresh implementation session.

## Research packet lifecycle

Use the vault's Obsidian-compatible template to create research prompts; do not reconstruct packet structure from instructions:

```bash
OAW_VAULT=~/vaults/example oaw research scaffold \
  --project "Example Project" \
  --track "architecture/provider-choice" \
  --title "Provider choice" \
  --date 2026-07-12
```

The command writes `Prompt.md`, `Synthesis.md`, and the shared folder-scoped `Bases/Research packet.base`. It refuses an existing prompt unless `--force` is explicit; forcing never replaces an existing synthesis. The single exact `## Deep research prompt` heading must be followed only by one non-empty fenced `text` block. Its contents are the complete copy-ready provider request; local metadata, fence markers, and extra commentary are excluded from handoff.

Immediately after launching exactly one provider run, register it:

```bash
OAW_VAULT=~/vaults/example oaw research start \
  --project "Example Project" \
  --track "architecture/provider-choice" \
  --source "ChatGPT Pro" \
  --url "https://example.com/share/run"
```

`start` creates one `Results - <Source>.md` with running status and provenance, appends it to the prompt's initially empty `## Running research sessions`, refuses unsafe or duplicate source labels and non-HTTP(S) URLs, and rolls back partial writes. It creates a missing synthesis/Base but never overwrites synthesis content.

Use the `obsidian-research` skill for provider-visible handoff preflight, exact copy output, finished-report intake, raw-artifact preservation, and provider-specific normalization. Do not pre-create pending provider placeholders or use its legacy `set-url` command for OAW-owned project packets.

## Note intake

Use `oaw note` for append-safe updates on resolved notes that are not project task lifecycle changes.

Append the same `## Agent sessions` entry shape to any resolved note:

```bash
oaw note session AGT-TSK-session-retrospectives --note "Reviewed retrospective habit."
```

Append a dated observation block under `## Observations` or another explicit heading:

```bash
oaw note observe CDX-RES-routing-evidence \
  --title "Wrap-up format gap" \
  --body "The evidence note needs a mechanical append path."
```

Create a draft retrospective note under `Agents/Retrospectives/`:

```bash
oaw retro create \
  --title "Resolver dogfood" \
  --summary "Captured the resolver workflow and follow-ups."
```

`oaw note session` and `oaw retro create` require a real session ID from a supported harness environment variable unless the user explicitly accepts `--allow-missing-session-id`. `note session` maintains the same deduplicated `session-ids` frontmatter list as task writes; retrospective creation initializes that list. `oaw note observe` does not require a session ID.
Use `note observe --section` for a heading other than `Observations`. `retro create`
also accepts `--date` and `--id`; replacing an existing generated note requires
the explicit `--force` flag.

Review historical agent retrospectives through `Agents/Retrospectives.base#Recent`. The Base also
provides Drafts, By provider, Sensitive, and Metadata audit views. `Projects/Session Retrospectives/`
is a separate software project whose Base tracks project tasks and research; it is not the historical
agent-retrospective review surface.

## Agent feedback

Create one durable feedback note for a concrete friction, verified behavior,
idea, or bug instead of leaving it only in a session transcript:

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
exactly one body source are mandatory: use `--body` or `--body-file`; pass
`--body-file -` to read standard input. The note path is
`Agents/Feedback/<date> <title>.md` and its default ID/alias is
`AGT-FDBK-<title-slug>`. `--date` and `--id` override those derived values.
Repeat `--tag` for safe, deduplicated extra tags. The command refuses duplicate
IDs and paths, never overwrites feedback, and requires real session provenance
unless `--allow-missing-session-id` is explicitly accepted.

## Safe outbound exports

Use `oaw export note` only for notes that have explicit frontmatter approval:

```yaml
export-scope: work
return_ingest: true
export_artifacts:
  - scripts/run.sh
```

Export a bundle:

```bash
oaw export note OAW-TSK-export-example --target work --output-root ~/obsidian-export
```

Validate a returned or transferred bundle before trusting it on the receiving side:

```bash
oaw export validate ~/obsidian-export/OAW-TSK-export-example --target work
```

- `note` refuses unmarked notes and notes whose `export-scope` does not match `--target`. Legacy `safe_for_export: true` plus a matching `export_target` remains accepted for existing notes.
- The bundle contains `note.md`, optional copied artifacts from `export_artifacts`, and `manifest.json`.
- Existing bundles are refused unless `export note --force` is explicit; `validate --target` defaults to the manifest target when omitted.
- Manifest paths are vault-relative, not absolute local paths.
- `validate` confines manifest paths to the bundle and checks the safe marker, target, note checksum, artifact checksums, and artifact presence.

## Cross-project task Base

When deciding what work to pick up next, consult the aggregate task Base at `Projects/Cross-project tasks.base`. Its `Open cross-project tasks` view includes task notes from `Projects/*/Tasks`, `Agents/Tasks`, and root `Tasks/`; keeps `active`, `review`, `todo`, `backlog`, and legacy `open` tasks visible; and excludes terminal `done` and `superseded` work. The display order is Active, Review, Todo, Open / untriaged, then Backlog. New tasks should use `todo` when deliberately selected or `backlog` when unscheduled; do not create new `open` tasks.

Priority uses a vault-wide 1/2/3 scale:

- `1`: urgent, blocking, or unusually high-leverage work.
- `2`: normal next-session work with clear value.
- `3`: useful backlog work that should not outrank sharper tasks.

Cross-project usefulness can raise priority: a task that improves multiple projects, agent handoffs, or repeatable workflow safety may deserve a lower numeric priority than a similar one-project task. The Base sorts by priority rank, then effort rank (`S`, `M`, `L`), then title; missing priority or effort sorts after explicit values.

## Link management

Use `oaw link` for durable wikilink checks and append-only repairs:

```bash
oaw link check OAW-TSK-cli OAW-TSK-session-lookup
oaw link list OAW-TSK-cli
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --label "Session lookup"
oaw link ensure-bidirectional OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link lint
```

- `check` reports whether each note links to the other.
- `list` prints explicit wikilinks from a note and resolves each target when possible.
- `ensure` and `ensure-bidirectional` default to a dry-run preview. Pass `--write` only when the user asked to apply the append-only section edit.
- One-way `ensure --label` overrides the target ID used as display text.
- Edits use durable `[[vault/path|ID]]` links and skip duplicates when a path-form link is already present with any alias.
- `lint` reports opaque ID links such as `[[OAW-TSK-cli]]` and suggests durable replacements when the ID resolves.

## Session artifact snapshots

When a session should be preserved for retrospectives, snapshot its transient harness artifacts into the vault attachments folder:

```bash
oaw session snapshot 73550790-5af5-4efc-828c-72e6e1053d8f \
  --slug sr-dogfood-zombie-codex \
  --partial \
  --codex-thread 019f3e73-029f-7ea2-9772-fdfa1e25fb8f \
  --codex-thread 019f3e8d-8307-7052-b367-57e78f3316ae \
  --claude-session 019f3ef0-1111-7222-8333-c26aa5d38893

# Codex-only session (no Claude parent transcript)
oaw session snapshot "$CODEX_THREAD_ID" --codex-only --partial --slug codex-dogfood
```

- The command writes to `Agents/Retrospectives/attachments/<date>-<slug>/` by default.
- By default it copies the Claude parent transcript, nested Claude subagent transcripts, task outputs under `tasks/`, workflow run artifacts under `subagents/workflows/`, persisted workflow scripts under `workflows/scripts/`, discoverable Codex rollouts, referenced plugin job logs, and fork parents referenced by explicit Claude/fork markers or `--claude-session`. Use `--codex-only` when no Claude parent exists; the positional Codex thread's own rollout is always required.
- Fork parents are auto-discovered from `CLAUDE_SESSION_ID`, `claude-session`, `fork ... session`, and `btw-session` references in copied artifacts. Use `--claude-session <id>` for parents those artifacts do not reference clearly; repeat it for multiple fork parents.
- Codex rollouts are discovered by referenced thread IDs or explicit `--codex-thread <id>` flags. Use `--codex-rollout <filename-or-path>` for an exact rollout. Use `--grep <literal>` only when the literal identifies one rollout; ambiguous grep matches fail and should be replaced with explicit `--codex-thread` or `--codex-rollout` flags.
- It writes `manifest.json` with each source path, destination path, copy time, size, hash, category, mode, and completeness. Use the manifest instead of hand-writing provenance.
- Use `--partial` while the session is still live. Re-run the same command later to refresh the transcript, preserve nested artifacts, pick up new artifacts, and remove stale files listed in the previous manifest.
- Use `--complete` to override current-session detection, `--date` to override the folder date, and `--output-root`, `--claude-root`, `--codex-root`, or `--plugin-data-root` for controlled test/demo locations.
- For real vault snapshots, use the installed `oaw session snapshot ...` command. Reserve `uv run python bin/oaw session snapshot ...` for repo-development checks, temp-vault fixtures, or deliberately testing the checkout copy.

## Rules

- Use `oaw` before any manual vault search. Do not resolve IDs by searching agent state directories (`.codex`, `.claude`, `.agents`) or session transcripts unless the user explicitly asks for forensic work.
- Keep durable written links as path links with the ID as display text, e.g. `[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]` — the link target is the vault-relative path without the `.md` extension. Reuse the path from resolve output already in hand; run `oaw resolve --path` only when no resolve has been done yet.
- When `oaw` lacks a needed capability, capture a new OAW task describing the gap, then do the minimal manual workaround and keep moving.
- For real vault writes, prefer stable installed commands: `oaw task ...`, `oaw session snapshot ...`, or `obsidian ...` for note/metadata writes. Use `uv run python bin/oaw ...` only for CLI development, temp-vault fixtures, or deliberately testing the checkout copy (the CLI depends on `typer`, so bare `python bin/oaw` fails). This follows the approval-scope lesson from [[Agents/Feedback/2026-07-08 allow-listed skill scripts for vault writes|AGT-FDBK-allow-listed-skill-scripts]].
- If a needed capability is not documented here, check `oaw --help` before reaching for filesystem search.
- For `PMX-*` IDs, prefer the dedicated `pmx` skill and CLI.

## Approval prefixes

When a Codex approval prompt offers to persist a command prefix for OAW vault writes, prefer the narrow operational command that actually needs the permission. Good persisted prefixes are stable entrypoints such as:

```text
["oaw", "session", "snapshot"]
["oaw", "task"]
```

Do not persist broad interpreter or shell prefixes for OAW work, such as:

```text
["python"]
["python", "bin/oaw"]
["bash"]
```

Use repo-local `uv run python bin/oaw ...` for development and temp-vault tests (the CLI depends on `typer`, so bare `python bin/oaw` fails). For real vault writes, prefer installed `oaw ...` commands so the approval scope stays tied to the operation rather than to an arbitrary script runner.
