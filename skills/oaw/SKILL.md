---
name: oaw
description: "Trigger whenever an `obs:`-prefixed ID (e.g. `obs:OAW-TSK-cli`) or a bare frontmatter ID like `AGT-*`, `SR-*`, `CDX-*`, `FAB-*`, or `OAW-*` appears in the message — including short one-line instructions on such IDs, like marking one task blocked-by/follows another, setting a priority, or appending a note. Also use for any read or write on OAW-managed Obsidian vault notes: resolving an ID, listing a project's tasks or captures, moving a task through its lifecycle (create, backlog, promote, start, pause, review, complete), setting priority or preparedness, triaging/promoting a capture, checking or repairing wikilinks, creating a project workspace or research packet, recording a retrospective or feedback note, tracing a session/thread id, snapshotting session artifacts, or exporting/ingesting handoff notes. Provides the `oaw` CLI. (`PMX-*` IDs use the dedicated `pmx` skill.)"
---

# oaw — Obsidian ID resolution and task lifecycle

## Overview

The `oaw` CLI resolves reference IDs against note frontmatter (`id` and `aliases` only) in the user's Obsidian vault, and records agent work on project task notes. Use it instead of grepping the vault or searching local agent state: body-text mentions of an ID are not the note itself, so text search finds decoys; frontmatter matching is narrow and auditable.

Set `OAW_VAULT` to the vault root before any command that accesses the vault. There is
no built-in default; an unset or blank value fails with a clear configuration error.

This file covers the hot path: resolving, listing, and the task lifecycle. Less
frequent command domains live under `references/` — see "Other command domains"
at the end for when to read each one.

## Core rules

- Use `oaw` before any manual vault search. Do not resolve IDs by searching agent state directories (`.codex`, `.claude`, `.agents`) or session transcripts unless the user explicitly asks for forensic work.
- Keep durable written links as path links with the ID as display text, e.g. `[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]` — the link target is the vault-relative path without the `.md` extension. Reuse the path from resolve output already in hand; run `oaw resolve --path` only when no resolve has been done yet.
- When `oaw` lacks a needed capability, capture a new OAW task describing the gap, then do the minimal manual workaround and keep moving.
- If a needed capability is not documented here or in `references/`, check `oaw --help` before reaching for filesystem search.
- For `PMX-*` IDs, prefer the dedicated `pmx` skill and CLI.

### Installed vs checkout

For real vault writes, prefer stable installed commands: `oaw task ...`, `oaw session snapshot ...`, or `obsidian ...` for note/metadata writes. Use `uv run python bin/oaw ...` only for CLI development, temp-vault fixtures, or deliberately testing the checkout copy — the CLI depends on `typer`, so bare `python bin/oaw` fails. Keeping real-vault writes on installed commands ties approval scope to the operation rather than to an arbitrary script runner.

For the same reason, when a Codex approval prompt offers to persist a command prefix for OAW vault writes, persist the narrow operational command (such as `["oaw", "task"]` or `["oaw", "session", "snapshot"]`), never a broad interpreter or shell prefix (`["python"]`, `["python", "bin/oaw"]`, `["bash"]`).

For checkout development, shared errors, note splitting/reading, and the hand-rolled frontmatter parser/mutators are importable from `oaw.errors`, `oaw.notes`, and `oaw.frontmatter`. Keep direct unit coverage beside CLI contract tests when changing these helpers.

### Session provenance

The session ID is read automatically from the first supported harness environment variable. Commands that create or close agent runs — `task start`, `task pause`, `task review`, `task complete`, and `run close` — and the write form of `task rename` require a real identity and never accept `--allow-missing-session-id`. Non-run trace writes (`task create`, `project create`, `task priority`, `task preparedness`, relation mutations, `note session`, `retro create`, `feedback create`) accept that escape hatch only when the user explicitly accepts an untraceable entry.

With a real harness ID, lifecycle and `task note` writes append it to the note's deduplicated `session-ids` frontmatter list. The explicit missing-ID path writes only the body trace; it does not add a synthetic list value.

### Durable obs references

Explicit `obs:<ID>` prose mentions (e.g. `obs:OAW-TSK-cli`) are automatically rewritten into durable `[[vault/path|ID]]` wikilinks whenever OAW writes a note body it owns — task creation and lifecycle notes, project goals, `note session` entries, observation bodies, feedback bodies, and retrospective summaries. Resolution is strict: one missing, ambiguous, or malformed eligible reference aborts the entire write before anything is touched, with frontmatter and existing links/code left untouched. To materialize references already sitting in authored prose, run `oaw link materialize <note> [--dry-run|--write]`. Full command and parser semantics are in `references/links-and-relations.md`.

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

When one resolved task owns the session and a rename operation is already available in the tool set, synchronize `[MARKER] CANONICAL-ID` on initial ownership, resume, and material phase changes: design `[DESIGN]`, implementation `[I]`, review or verification `[R]`, wrapping up `[W]`, and completed `[DONE]`. Incidental references do not transfer ownership. Titles never change OAW state: `[I]` normally accompanies `task start`, `[R]` does not imply `status: review`, and `[DONE]` is allowed only after `task complete` succeeds.

If no agent-callable rename operation is already exposed, silently skip title synchronization. Do not investigate support, announce the limitation, ask the user to rename, spawn or resume a client, or mutate task data as compensation. Only when maintaining, evaluating, or explicitly discussing title sync, read `references/session-phase-title-evaluation.md` for rationale and client evidence.

## Session lookup

Use `oaw session lookup <id>` when you need to trace a literal session/thread id quickly:

- Searches the vault first and reports matching note paths plus frontmatter ids.
- Without `--verbose`, a vault match uses the fast path and stops after reporting those notes.
- With `--verbose`, continues into harness artifacts even when vault matches exist and reports both result classes.
- If nothing matches, searches harness artifacts and prints a session synopsis.
- If still missing, exits `0` with a clear not-logged message.

```bash
oaw session lookup 00000000-0000-4000-8000-000000000001
oaw session lookup 00000000-0000-4000-8000-000000000001 --codex-root /tmp/example-codex-sessions --claude-root /tmp/example-claude-projects
oaw session lookup 00000000-0000-4000-8000-000000000001 --verbose
```

`--verbose` adds best-effort per-artifact metadata; `references/session-artifacts.md` documents the exact metric semantics. By default, Codex lookup searches both `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions` (`~/.codex` when `CODEX_HOME` is unset), preferring the active copy when the same rollout filename exists in both. `--codex-root` or `OAW_CODEX_SESSIONS_ROOT` selects exactly one Codex root for controlled fixtures or alternate installations. `--claude-root` and `OAW_CLAUDE_PROJECTS_ROOT` similarly override the `~/.claude/projects` fallback.

## Listing project notes

To survey a project's tasks, list them instead of resolving one by one:

```bash
oaw list --project "Obsidian Agent Workflow"   # tab-separated: id, status, title, relative path
oaw list --project "Obsidian Agent Workflow" --status active
```

`--project` accepts a project alias (with or without the `obs:` prefix) or the folder name under `Projects/` in the vault. `task` is the default note type.

To build a priority-ranked, goal-annotated view of a project's tasks, use the
list command's own sort and projection instead of a shell loop over each note's
frontmatter and body:

```bash
oaw list --project "Obsidian Agent Workflow" --status todo \
  --sort priority --fields id,priority,effort,goal --json
```

- `--sort {priority,effort,title}` orders rows by the vault-wide 1/2/3 priority
  rank, then effort rank (`S`, `M`, `L`), then title; missing values sort last.
- `--fields` projects a comma-separated column set (default `id,status,title,path`);
  unknown fields error clearly. Projectable fields: `id`, `status`, `title`,
  `path`, `goal`, `priority`, `effort`, `preparedness`, `type`, `project`,
  `created`, `execution`.
- `--goal` adds a snippet from each note's `## Problem` first content line.
- `--json` emits the projected, sorted records as an object array so no shell
  frontmatter parsing is needed.

Some projects also use atomic capture notes for evidence/inbox items. List captures by frontmatter instead of opening a long inbox note:

```bash
oaw list --project Fable --type capture
```

Capture listing hides `status: archived` notes by default. Use `--include-archived` only for historical/provenance work, or `--status archived` when the archived set is the explicit target. For archived captures, prefer `oaw resolve --meta` or default `oaw resolve` first; use `--full` only after confirming the archived body is needed.

## Captures

The `obsidian-capture` companion skill owns the conversational capture workflow —
when to capture and what goes in the note. This skill owns the `oaw capture`
command surface that workflow (and any direct capture request) uses.

Use `oaw capture` for capture notes instead of hand-writing frontmatter or the raw
`obsidian create` command. `create` generates the `CAP-YYYYMMDD-<slug>` ID, stamps a
full timezone-aware `created` timestamp, and starts at `status: inbox`:

```bash
oaw capture create --title "Investigate flaky resolver"
oaw capture create --title "Route this idea" --project obs:OAW --url https://example.com/src
```

- `list` is the vault-wide capture catalog: it finds every `type: capture` note in any
  folder and shows all statuses. Filter with `--status`/`--project`, order with
  `--sort newer|older`, emit `--json`. This differs from `oaw list --type capture`,
  which keeps its project-scoped, archived-hiding contract. `show <ID>` prints one
  capture's metadata and full body from any location.
- `triage <ID> --status <state> (--reason "..." | --no-reason)` moves a capture between
  `inbox`, `incubating`, `parked`, `reference`, `triaged`, and `discarded`, appending a
  dated `## Triage` audit entry. Exactly one of `--reason`/`--no-reason` is required.
  `--status incubating` requires `--review-after YYYY-MM-DD` (and leaving `incubating`
  clears it); `--status triaged` requires at least one `--destination` (stored or
  supplied), which also writes reciprocal `## Related` links. Triage only writes captures
  under `Captures/Entries/`; captures elsewhere are refused.
- Promotion via `oaw task create --from-capture <ID>` is metadata-first: it uses the
  capture's `project` frontmatter, then an explicit `--project`, then legacy `Projects/`
  path inference; a mismatch between an explicit `--project` and capture metadata errors.

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
oaw task rename OAW-TSK-cli --title "Resolver command" --note "Use the shipped name."
oaw task priority OAW-TSK-cli --priority 1 --note "Raised after cross-project triage."
oaw task preparedness OAW-TSK-cli --state prepared \
  --note "Execution is designed and known blockers are recorded."
```

- `create` makes a new project task with standard frontmatter, a `Problem` section, a durable project-index link, and an `## Agent sessions` trace. `--project` takes a project alias (`obs:OAW`) or folder name; the ID defaults to `<ALIAS>-TSK-<slug>`; status defaults to `backlog` (`--status todo` for selected work); preparedness defaults to `needs-triage` (`--preparedness needs-triage|needs-design|prepared` for an explicit assessment); optional values include `--priority 1|2|3`, `--effort S|M|L`, repeatable `--tag`, and `--execution human|agent|hybrid`. A title—explicit or capture-derived—must be a portable filename component: non-empty with no surrounding whitespace, `.`/`..`, leading dot, trailing dot or space, control character, Windows device name, or `\ / : * ? " < > |`. Rephrase reserved display punctuation with a hyphen or em dash, such as `Review — API` instead of `Review: API`. Validation precedes every task, capture, relation, and run write. `--start` works with or without a capture and atomically creates the active task and run. It defaults execution to `agent`, requires a real session ID, and rejects human execution.
- An actionable request on an `obs:CAP-*` or project capture ID is a promotion trigger: before investigation, implementation, or other material work, run `task create --from-capture <CAP-ID>`. The project and title default from a capture under `Projects/<Project>/`, or may be explicit. Promotion preserves the capture note, body, stable ID, and expected-next-shape `Outcome`; records `source-capture` on the task; adds durable links in both directions; appends the task wikilink to the capture's `destinations` frontmatter; and changes the capture to `triaged` only when every write succeeds. Failure rolls all writes back. Choose backlog (default), `--status todo`, or `--start` for immediate `active` intent. `--start` does not relax session-ID requirements or invent provenance.
- `backlog` sets `status: backlog`; `promote` sets `status: todo`; `start` sets `status: active`; `pause` pauses only the caller's run and leaves task status unchanged; `review` closes the caller's run with reason `review` and sets task status to `review`; `complete` completes the caller's run and sets task status to `done`. In the same session, `complete` may promote that reviewed run directly from `closed` to `completed` while the task is still in `review`, without an intermediate `start` or `active` trace. Other closed or terminal runs are rejected.
- `complete` requires `--checks` naming the verification actually run; do not fabricate checks.
- `note` appends a dated entry without changing status. If the caller already has a matching running record it refreshes that record; it never creates a run.
- `rename <TASK-ID> --title <TITLE> --note <REASON>` accepts only the canonical frontmatter ID and previews a deterministic filename, H1, and whole-vault Markdown backlink migration without writing. Apply the reviewed plan by repeating the exact command with `--write --expect-plan sha256:<digest>`. Write mode requires a real session, refuses running task runs, unsafe or colliding titles, symlink or unreadable Markdown notes, malformed task/run/relation state, and concurrent byte or file-identity changes. It preserves the stable ID, aliases, lifecycle and preparedness fields, run identities, wikilink aliases/embeds/suffixes, and protected code/comments; caught commit or postcondition failures roll back. An already-matching path and H1 is a no-op with no trace.
- `priority` sets an existing task's priority to `1`, `2`, or `3`, appends a dated agent-session trace, and leaves status and run records unchanged.
- `preparedness` sets the independent design-sufficiency property to `needs-triage`, `needs-design`, or `prepared`; appends a dated trace; and leaves lifecycle status and run records unchanged. Missing preparedness on legacy tasks means unassessed, not prepared. A prepared task may still be blocked or unscheduled.
- `backlog`, `promote`, `start`, `review`, and `complete` append a dated entry under `## Agent sessions`; task-note frontmatter is the single lifecycle source of truth and is surfaced through project and cross-project Bases.
- For `backlog`, `promote`, `start`, `pause`, `review`, `complete`, and `task note`, provide exactly one Markdown source: `--note` or `--note-file`. `--note-file -` reads standard input. Prefer the file/stdin route when content has backticks, dollar signs, quotes, or multiple lines so the shell never evaluates it. `task create` keeps its initial problem statement optional, but when supplied accepts exactly one of those same sources.
- The command's output (`Updated:` / `Status:` / optional `Run:`) confirms the write. To report resulting state, rely on that output plus `oaw resolve --meta` if needed — do not re-read the whole note with `--full`.

Agent runs are durable records under `Agents/Runs/`. The same task/provider/session
`start` is idempotent; distinct sessions get distinct records. Multiple sessions may
run the same task, but `review` and `complete` refuse while another record remains
`running`. A derived `stale` age (more than 24 hours) is visible but never changes
state or releases concurrency. When a competing run blocks completion, inspect it
read-only, warn the user, and ask whether to wait, investigate, or treat it as
abandoned; never close it automatically. Work after the review handoff requires
renewed verification and review before completion, even after an abandoned competitor
is administratively closed. Use `oaw run list [--task ID] [--state STATE]
[--session ID | --current-session] [--json]` (`--current-session` reads the harness
session ID and errors without one),
`oaw run close <RUN-ID> --reason <reason>`, and `oaw run audit`. Administrative close
records the real closer while preserving the original agent identity and never
changes task lifecycle state.

Tasks may declare hard dependencies: `blocked-by` relations gate `review` and
`complete`, which refuse unresolved or invalid hard blockers. Starting blocked work
is allowed for triage, design, or preparation; surface the CLI's blocker output, and
never remove a relationship merely to make a lifecycle command pass. Full relation
commands and semantics are in `references/links-and-relations.md`.

Keep `Todo` for near-term chosen work and put unscheduled known work in `Backlog`. When implementation is ready for verification, run `oaw task review ... --checks ...` so the task note records the handoff.

When an OAW task moves into repository implementation or review, load the
`oaw-task-execution` companion before repository edits. It owns main-checkout
preflight, GTR feature isolation, and proportional implementation and review
delegation. Keep task resolution, provenance, relationships, and lifecycle writes in
this skill. Do not load the companion for status-only or vault-only work.

At wrap-up — the user stopping, asking whether the session is done, or ending urgently — load the `oaw-wrap-up` companion skill; it owns end-of-session closure: run and worktree inventory, classification, resume blocks, and the closure receipt. Two rules it enforces also apply whenever closing out here: substantive work with no owning task must be created or promoted (from its source capture) into a task before retrospective closeout, and a retrospective may close only after that task is `done` via `oaw task complete ... --checks "<verification actually run>"`. Link the retrospective primarily to the completed task and retain the source-capture link as provenance. Do not treat a capture `Outcome` as a completion report.

For any project tracked through OAW, keep pre-implementation architecture,
designs, and proposals on the owning task note by default. Do not create a
tracked repository design document until implementation makes it part of the
project's durable documentation, unless the user explicitly asks for a tracked
artifact. This keeps proposals from looking like shipped behavior and leaves
the working tree clean for a fresh implementation session.

## Cross-project task Base

When deciding what work to pick up next, consult the aggregate task Base at `Projects/Cross-project tasks.base`. Its `Open cross-project tasks` view includes task notes from `Projects/*/Tasks`, `Agents/Tasks`, and root `Tasks/`; keeps `active`, `review`, `todo`, `backlog`, and legacy `open` tasks visible; and excludes terminal `done` and `superseded` work. The display order is Active, Review, Todo, Open / untriaged, then Backlog. New tasks should use `todo` when deliberately selected or `backlog` when unscheduled; do not create new `open` tasks. The comprehensive view exposes preparedness and relationship fields. Focused views show selected and executable work (`todo + prepared + unblocked`), prepared backlog, tasks needing preparation, and blocked tasks. Dependency state is derived rather than cached; refresh the Base when a linked target status changed but the view has not updated.

Priority uses a vault-wide 1/2/3 scale:

- `1`: urgent, blocking, or unusually high-leverage work.
- `2`: normal next-session work with clear value.
- `3`: useful backlog work that should not outrank sharper tasks.

Cross-project usefulness can raise priority: a task that improves multiple projects, agent handoffs, or repeatable workflow safety may deserve a lower numeric priority than a similar one-project task. The Base sorts by priority rank, then effort rank (`S`, `M`, `L`), then title; missing priority or effort sorts after explicit values.

## Other command domains

Read the matching reference before using these command groups; each file carries the full contract:

- `references/exports-and-ingestion.md` — before any `oaw ingest safe-export` or `oaw export note|validate` command.
- `references/project-and-research.md` — before `oaw project create` or any `oaw research` command (the `oaw-research` skill owns provider handoff and report intake).
- `references/notes-and-feedback.md` — before `oaw note session|observe`, `oaw retro create`, or `oaw feedback create`.
- `references/links-and-relations.md` — before `oaw link` commands or `oaw task relation` mutations and validation.
- `references/session-artifacts.md` — before `oaw session snapshot`, or for `session lookup --verbose` metric semantics.
- `references/session-phase-title-evaluation.md` — only when maintaining, evaluating, or explicitly discussing session-title sync.
