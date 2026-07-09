---
name: oaw
description: This skill should be used when an `obs:`-prefixed reference appears (e.g. `obs:OAW-TSK-cli`), when a frontmatter reference ID such as `AGT-*`, `SR-*`, `CDX-*`, `FAB-*`, or `OAW-*` needs resolving to a note in the user's Obsidian vault, when tracing a session/thread id through vault notes and session artifacts, when starting or completing a project task note with an agent-session trace, or when listing a project's tasks/captures. Provides the `oaw` CLI workflow. (`PMX-*` IDs have a dedicated `pmx` skill.)
---

# oaw — Obsidian ID resolution and task lifecycle

## Overview

The `oaw` CLI resolves reference IDs against note frontmatter (`id` and `aliases` only) in the user's Obsidian vault, and records agent work on project task notes. Use it instead of grepping the vault or searching local agent state: body-text mentions of an ID are not the note itself, so text search finds decoys; frontmatter matching is narrow and auditable.

Set `OAW_VAULT` when running against a non-default vault, tests, demos, or automation.

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
```

Override the harness roots for demo and tests with flags or the shared `OAW_CODEX_SESSIONS_ROOT` and `OAW_CLAUDE_PROJECTS_ROOT` environment variables; fallback roots are `~/.codex/sessions` and `~/.claude/projects`.

## Listing project notes

To survey a project's tasks, list them instead of resolving one by one:

```bash
oaw list --project "Obsidian Agent Workflow"   # tab-separated: id, status, title, relative path
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
- `--write` performs the actual move:
  - accepted notes go to the vault-relative `Imports/Safe export`
  - rejected notes go to `.rejected/` in the handoff path

Safety is explicit by design: only frontmatter-marked files are ingested.

## Project task lifecycle

Lifecycle writes apply only to task notes under `Projects/*/Tasks` (the CLI enforces this):

```bash
oaw task backlog OAW-TSK-cli --note "Parked until the dependency is ready."
oaw task promote OAW-TSK-cli --note "Selected for the next session."
oaw task start OAW-TSK-cli --note "Started resolver implementation."
oaw task complete OAW-TSK-cli --note "Finished and verified." --checks "python -m unittest"
oaw task note OAW-TSK-cli --note "Recorded an independent review." --checks "python -m unittest"
```

- `backlog` sets `status: backlog`; `promote` sets `status: todo`; `start` sets `status: active`; `complete` sets `status: done`.
- `complete` requires `--checks` naming the verification actually run; do not fabricate checks.
- `note` appends a dated entry under `## Agent sessions` without changing `status` or any board. Use it for delegation reviews, design notes, partial-progress records, and other trace entries on task notes in any status.
- `backlog`, `promote`, `start`, and `complete` append a dated entry under `## Agent sessions` and move the task's card to the matching column when the project has a board (`Projects/<Project>/Board.md`) — creating the card and column heading if missing. Cards keep the `- [ ]` marker in every column; the column heading, not the checkbox, reflects status.
- The command's output (`Updated:` / `Status:` / `Board:`) confirms the write. To report resulting state, rely on that output plus `oaw resolve --meta` if needed — do not re-read the whole note with `--full`.
- The session ID is read automatically from the harness environment; the first of `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, `OPENCODE_SESSION_ID`, `GEMINI_SESSION_ID` that is set wins. `oaw` never invents one: with no session variable set, the command fails with a clear error (so there is no need to check the variables beforehand). Pass `--allow-missing-session-id` only when the user explicitly accepts an untraceable entry.

Project boards should use the column order `Backlog` → `Todo` → `Active` → `Done`. Keep `Todo` for near-term chosen work. Put unscheduled known work in `Backlog`, and when a session decides what should happen next, run `oaw task promote ...` so the board reflects the decision.

Use `oaw board ensure-backlog --project "Project Name"` to add a missing `Backlog` column before `Todo` on an existing project board without rewriting cards.

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

`oaw note session` and `oaw retro create` require a real session ID from a supported harness environment variable unless the user explicitly accepts `--allow-missing-session-id`. `oaw note observe` does not require a session ID.

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

When deciding what OAW or adjacent agent-tooling work to pick up next, consult the aggregate task Base at `Projects/Cross-project tasks.base` before relying on one project board. Its `Open cross-project tasks` view includes task notes from `Projects/*/Tasks` and `Agents/Tasks`, keeps `backlog`, `todo`, `active`, and legacy `open` tasks visible, and excludes terminal `done` and `superseded` work.

Priority uses a vault-wide 1/2/3 scale:

- `1`: urgent, blocking, or unusually high-leverage work.
- `2`: normal next-session work with clear value.
- `3`: useful backlog work that should not outrank sharper tasks.

Cross-project usefulness can raise priority: a task that improves multiple projects, agent handoffs, or repeatable workflow safety may deserve a lower numeric priority than a similar one-project task. The Base sorts by priority rank, then effort rank (`S`, `M`, `L`), then title; missing priority or effort sorts after explicit values.

## Session artifact snapshots

When a session should be preserved for retrospectives, snapshot its transient harness artifacts into the vault attachments folder:

```bash
oaw session snapshot 73550790-5af5-4efc-828c-72e6e1053d8f \
  --slug sr-dogfood-zombie-codex \
  --partial \
  --codex-thread 019f3e73-029f-7ea2-9772-fdfa1e25fb8f \
  --codex-thread 019f3e8d-8307-7052-b367-57e78f3316ae
```

- The command writes to `Agents/Retrospectives/attachments/<date>-<slug>/` by default.
- It copies the Claude parent transcript, Claude `subagents/*.jsonl`, discoverable Codex rollouts, and referenced plugin job logs.
- Codex rollouts are discovered by referenced thread IDs or explicit `--codex-thread <id>` flags. Use `--codex-rollout <filename-or-path>` for an exact rollout. Use `--grep <literal>` only when the literal identifies one rollout; ambiguous grep matches fail and should be replaced with explicit `--codex-thread` or `--codex-rollout` flags.
- It writes `manifest.json` with each source path, destination path, copy time, size, hash, category, and completeness. Use the manifest instead of hand-writing provenance.
- Use `--partial` while the parent session is still live. Re-run the same command later to refresh the parent transcript, pick up new subagents, and remove stale files listed in the previous manifest.
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
