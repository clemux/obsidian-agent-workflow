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

## Project task lifecycle

Lifecycle writes apply only to task notes under `Projects/*/Tasks` (the CLI enforces this):

```bash
oaw task create --project obs:OAW --title "Example task" --note "Initial problem statement."
oaw task create --from-capture obs:OAW-CAP-routing-regression \
  --title "Investigate routing regression" --status todo
oaw task create --from-capture obs:OAW-CAP-urgent \
  --title "Handle urgent request" --start
oaw task backlog OAW-TSK-cli --note "Parked until the dependency is ready."
oaw task promote OAW-TSK-cli --note "Selected for the next session."
oaw task start OAW-TSK-cli --note "Started resolver implementation."
oaw task complete OAW-TSK-cli --note "Finished and verified." --checks "pytest"
oaw task note OAW-TSK-cli --note "Recorded an independent review." --checks "pytest"
```

- `create` makes a new task note under the project's `Tasks/` folder with standard frontmatter, a `Problem` section, a durable project-index link, an `## Agent sessions` trace, and a board card. `--project` takes a project alias (`obs:OAW`) or `Projects/` folder name; the ID defaults to `<ALIAS>-TSK-<slug>` (override with `--id`); status defaults to `backlog` (`--status todo` for selected work); optional `--priority 1|2|3`, `--effort S|M|L`, and repeatable `--tag`. Duplicate IDs or existing paths fail without writing. Use it instead of hand-writing task frontmatter.
- An actionable request on an `obs:CAP-*` or project capture ID is a promotion trigger: before investigation, implementation, or other material work, run `task create --from-capture <CAP-ID>`. The project and title default from a capture under `Projects/<Project>/`, or may be explicit. Promotion preserves the capture note, body, stable ID, and expected-next-shape `Outcome`; records `source-capture` on the task; adds durable links in both directions; appends the task wikilink to the capture's `destinations` frontmatter; registers the project-board card; and changes the capture to `triaged` only when every write succeeds. Failure rolls all writes back. Choose backlog (default), `--status todo`, or `--start` for immediate `active` intent. `--start` does not relax session-ID requirements or invent provenance.
- `backlog` sets `status: backlog`; `promote` sets `status: todo`; `start` sets `status: active`; `complete` sets `status: done`.
- `complete` requires `--checks` naming the verification actually run; do not fabricate checks.
- `note` appends a dated entry under `## Agent sessions` without changing `status` or any board. Use it for delegation reviews, design notes, partial-progress records, and other trace entries on task notes in any status.
- `backlog`, `promote`, `start`, and `complete` append a dated entry under `## Agent sessions` and move the task's card to the matching column when the project has a board (`Projects/<Project>/Board.md`) — creating the card and column heading if missing. Cards keep the `- [ ]` marker in every column; the column heading, not the checkbox, reflects status.
- The command's output (`Updated:` / `Status:` / `Board:`) confirms the write. To report resulting state, rely on that output plus `oaw resolve --meta` if needed — do not re-read the whole note with `--full`.
- The session ID is read automatically from the harness environment; the first of `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, `OPENCODE_SESSION_ID`, `GEMINI_SESSION_ID` that is set wins. `oaw` never invents one: with no session variable set, the command fails with a clear error (so there is no need to check the variables beforehand). Pass `--allow-missing-session-id` only when the user explicitly accepts an untraceable entry.
- With a real harness ID, lifecycle and `task note` writes append it as a quoted string to a deduplicated `session-ids` frontmatter block list, preserving existing entries, comments, and any legacy scalar `session-id`. Unsupported inline, mapping, or ambiguous non-string `session-ids` shapes fail before the note is written. The explicit missing-ID path writes only the body trace; it does not add a synthetic list value.

Project boards should use the column order `Backlog` → `Todo` → `Active` → `Done`. Keep `Todo` for near-term chosen work. Put unscheduled known work in `Backlog`, and when a session decides what should happen next, run `oaw task promote ...` so the board reflects the decision.

At wrap-up, check whether substantive work occurred. If it did and no task owns it, create or promote the source capture into a task before retrospective closeout. A retrospective may close only after that task is `done` via `oaw task complete ... --checks "<verification actually run>"`; link the retrospective primarily to the completed task and retain the source-capture link as provenance. Do not treat a capture `Outcome` as a completion report.

Use `oaw board ensure-backlog --project "Project Name"` to add a missing `Backlog` column before `Todo` on an existing project board without rewriting cards.

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

## Cross-project Next steps board

The vault-wide priority board lives at `Projects/Next steps.md` (`id: NEXT-board`). It is a hand-curated layer over project task notes, so routine card edits should use `oaw board` instead of manual kanban line surgery.

Add a linked card with the board's standard card shape:

```bash
oaw board add \
  --column "Next session(s)" \
  --link "Projects/Obsidian Agent Workflow/Tasks/Next steps board integration" \
  --title "Next steps board integration" \
  --why "document conventions and wire wrap-up handling" \
  --id OAW-TSK-next-board
```

Move or complete an existing card by a stable ID or unique text token already present in the card:

```bash
oaw board move OAW-TSK-next-board --column "Now (current session)"
oaw board done OAW-TSK-next-board
```

- `move` and `done` require exactly one matching card; a zero-match or duplicate match is an error.
- `move` preserves card text and keeps the card unchecked.
- `done` moves the card to `Done` and changes the checkbox to `- [x]`.
- The command targets `Projects/Next steps.md`; project-local boards still use `oaw task start/complete`.

## Cross-project task Base

When deciding what work to pick up next, consult the aggregate task Base at `Projects/Cross-project tasks.base` before relying on one project board. Its `Open cross-project tasks` view includes task notes from `Projects/*/Tasks`, `Agents/Tasks`, and root `Tasks/`; keeps `active`, `review`, `todo`, `backlog`, and legacy `open` tasks visible; and excludes terminal `done` and `superseded` work. The display order is Active, Review, Todo, Open / untriaged, then Backlog. New tasks should use `todo` when deliberately selected or `backlog` when unscheduled; do not create new `open` tasks.

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
- For real vault snapshots, use the installed `oaw session snapshot ...` command. Reserve `python bin/oaw session snapshot ...` for repo-development checks, temp-vault fixtures, or deliberately testing the checkout copy.

## Rules

- Use `oaw` before any manual vault search. Do not resolve IDs by searching agent state directories (`.codex`, `.claude`, `.agents`) or session transcripts unless the user explicitly asks for forensic work.
- When changing `NEXT-board`, prefer `oaw board add/move/done`; edit the markdown directly only for convention text, bulk cleanup, or cases the command cannot express.
- Keep durable written links as path links with the ID as display text, e.g. `[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]` — the link target is the vault-relative path without the `.md` extension. Reuse the path from resolve output already in hand; run `oaw resolve --path` only when no resolve has been done yet.
- When `oaw` lacks a needed capability, capture a new OAW task describing the gap, then do the minimal manual workaround and keep moving.
- For real vault writes, prefer stable installed commands: `oaw task ...`, `oaw board ...`, `oaw session snapshot ...`, or `obsidian ...` for note/metadata writes. Use `python bin/oaw ...` only for CLI development, temp-vault fixtures, or deliberately testing the checkout copy. This follows the approval-scope lesson from [[Agents/Feedback/2026-07-08 allow-listed skill scripts for vault writes|AGT-FDBK-allow-listed-skill-scripts]].
- If a needed capability is not documented here, check `oaw --help` before reaching for filesystem search.
- For `PMX-*` IDs, prefer the dedicated `pmx` skill and CLI.

## Approval prefixes

When a Codex approval prompt offers to persist a command prefix for OAW vault writes, prefer the narrow operational command that actually needs the permission. Good persisted prefixes are stable entrypoints such as:

```text
["oaw", "session", "snapshot"]
["oaw", "task"]
["oaw", "board"]
```

Do not persist broad interpreter or shell prefixes for OAW work, such as:

```text
["python"]
["python", "bin/oaw"]
["bash"]
```

Use repo-local `python bin/oaw ...` for development and temp-vault tests. For real vault writes, prefer installed `oaw ...` commands so the approval scope stays tied to the operation rather than to an arbitrary script runner.
