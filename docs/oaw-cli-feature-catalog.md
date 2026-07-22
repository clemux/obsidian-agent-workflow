---
type: reference
id: OAW-REF-cli-feature-catalog
aliases:
  - OAW-REF-cli-feature-catalog
generated: true
---

# OAW CLI feature catalog

> [!warning] Generated file
> Do not edit this matrix by hand. Run `uv run python scripts/generate_cli_catalog.py`.

The command hierarchy, purposes, parameters, accepted choices, and deprecation state
come from the live Typer/Click tree. Implementation ownership and mutation scope are
semantic annotations checked for complete coverage of every leaf command.

## Command groups

| Group | Purpose |
| --- | --- |
| `oaw project` | Project workspace lifecycle |
| `oaw research` | Research packet utilities |
| `oaw task` | Project task lifecycle |
| `oaw run` | Inspect and administer agent-run records |
| `oaw note` | Append session traces or observations to resolved notes |
| `oaw ingest` | Ingest approved handoff files |
| `oaw link` | Inspect and maintain durable wikilinks |
| `oaw export` | Safe outbound note export utilities |
| `oaw session` | Session artifact utilities |
| `oaw retro` | Retrospective note utilities |
| `oaw feedback` | Agent feedback note utilities |
| `oaw capture` | Capture note lifecycle |

<a id="oaw-cli-group-top-level"></a>
## Top-level commands

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-resolve"></a>`oaw-cli-resolve` | `oaw resolve` | resolve obs:&lt;ID&gt; or &lt;ID&gt; | `<note-id>` (required)<br>`--full`<br>`--path`<br>`--meta`<br>`--outline`<br>`--json` | `oaw.resolver` | Read-only vault resolution. | Active (not deprecated) |
| <a id="oaw-cli-list"></a>`oaw-cli-list` | `oaw list` | list project notes | `--project PROJECT` (required)<br>`--type NOTE_TYPE` (default: task)<br>`--status STATUS`<br>`--tag TAG` (repeatable)<br>`--tag-mode {all\|any}` (default: all)<br>`--include-archived`<br>`--sort {priority\|effort\|title}`<br>`--fields FIELDS`<br>`--goal`<br>`--json` | `oaw.resolver` | Read-only vault listing. | Active (not deprecated) |
| <a id="oaw-cli-doctor"></a>`oaw-cli-doctor` | `oaw doctor` | check vault, parser, and Obsidian-version compatibility | `--obsidian-version OBSIDIAN_VERSION`<br>`--json` | `oaw.doctor` | Read-only vault, parser, and Obsidian-version compatibility diagnostics. | Active (not deprecated) |

<a id="oaw-cli-group-project"></a>
## `oaw project`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-project-create"></a>`oaw-cli-project-create` | `oaw project create` | create a project Index.md from the vault template | `--name NAME` (required)<br>`--alias ALIAS` (required)<br>`--goal GOAL` (required)<br>`--repo REPO`<br>`--tag TAG` (repeatable)<br>`--template TEMPLATE` (default: Templates/Small project index.md)<br>`--allow-missing-session-id` | `oaw.lifecycle` | Creates one project Index.md in the vault. | Active (not deprecated) |

<a id="oaw-cli-group-research"></a>
## `oaw research`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-research-scaffold"></a>`oaw-cli-research-scaffold` | `oaw research scaffold` | create Prompt.md and Synthesis.md from the research template | `--project PROJECT` (required)<br>`--track TRACK` (required)<br>`--title TITLE` (required)<br>`--date DATE`<br>`--template TEMPLATE` (default: Templates/Research packet.md)<br>`--force` | `oaw.lifecycle` | Creates or refreshes research packet files in the vault. | Active (not deprecated) |
| <a id="oaw-cli-research-start"></a>`oaw-cli-research-start` | `oaw research start` | register one launched provider run in an existing research packet | `--project PROJECT` (required)<br>`--track TRACK` (required)<br>`--source SOURCE` (required)<br>`--url URL` (required) | `oaw.lifecycle` | Creates a provider result note, updates its prompt note, and conditionally creates Synthesis.md and Bases/Research packet.base. | Active (not deprecated) |

<a id="oaw-cli-group-task"></a>
## `oaw task`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-task-backlog"></a>`oaw-cli-task-backlog` | `oaw task backlog` | move a project task to Backlog | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--checks CHECKS`<br>`--allow-missing-session-id` | `oaw.lifecycle` | Updates a task note's status and session provenance; refuses while a run is running. | Active (not deprecated) |
| <a id="oaw-cli-task-promote"></a>`oaw-cli-task-promote` | `oaw task promote` | move a project task to Todo | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--checks CHECKS`<br>`--allow-missing-session-id` | `oaw.lifecycle` | Updates a task note's status and session provenance; refuses while a run is running. | Active (not deprecated) |
| <a id="oaw-cli-task-start"></a>`oaw-cli-task-start` | `oaw task start` | move a project task to Active | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--checks CHECKS` | `oaw.lifecycle` | Updates a task note and creates or resumes the caller's run record (state: running). | Active (not deprecated) |
| <a id="oaw-cli-task-pause"></a>`oaw-cli-task-pause` | `oaw task pause` | pause an active project task's running run record | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE` | `oaw.lifecycle` | Updates a task note and transitions the caller's running run record to paused. | Active (not deprecated) |
| <a id="oaw-cli-task-review"></a>`oaw-cli-task-review` | `oaw task review` | move a verified project task to Review | `<note-id>` (required)<br>`--checks CHECKS` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE` | `oaw.lifecycle` | Updates a task note and transitions the caller's run record to closed (review handoff). | Active (not deprecated) |
| <a id="oaw-cli-task-complete"></a>`oaw-cli-task-complete` | `oaw task complete` | move a verified project task to Done | `<note-id>` (required)<br>`--checks CHECKS` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE` | `oaw.lifecycle` | Updates a task note and creates or transitions the caller's run record to completed. | Active (not deprecated) |
| <a id="oaw-cli-task-note"></a>`oaw-cli-task-note` | `oaw task note` | append an agent session note without changing status | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--checks CHECKS`<br>`--allow-missing-session-id` | `oaw.lifecycle` | Appends session provenance to one project task note; also updates the caller's running run record when one exists. | Active (not deprecated) |
| <a id="oaw-cli-task-rename"></a>`oaw-cli-task-rename` | `oaw task rename` | preview or apply a safe task title and path rename | `<note-id>` (required)<br>`--title TITLE` (required)<br>`--note NOTE` (required)<br>`--write`<br>`--expect-plan EXPECT_PLAN` | `oaw.task_rename` | Dry-run by default; --write renames one task note and migrates active Markdown wikilinks across the vault under a reviewed plan token. | Active (not deprecated) |
| <a id="oaw-cli-task-priority"></a>`oaw-cli-task-priority` | `oaw task priority` | update task priority without changing lifecycle status | `<note-id>` (required)<br>`--priority {1\|2\|3}` (required)<br>`--note NOTE` (required)<br>`--allow-missing-session-id` | `oaw.lifecycle` | Updates task priority frontmatter and appends session provenance. | Active (not deprecated) |
| <a id="oaw-cli-task-preparedness"></a>`oaw-cli-task-preparedness` | `oaw task preparedness` | update task preparedness without changing lifecycle status | `<note-id>` (required)<br>`--state {needs-triage\|needs-design\|prepared}` (required)<br>`--note NOTE` (required)<br>`--allow-missing-session-id` | `oaw.lifecycle` | Updates task preparedness frontmatter and appends session provenance. | Active (not deprecated) |
| <a id="oaw-cli-task-create"></a>`oaw-cli-task-create` | `oaw task create` | create a new project task note | `--project PROJECT`<br>`--title TITLE`<br>`--from-capture FROM_CAPTURE`<br>`--start`<br>`--id REQUESTED_ID`<br>`--status {backlog\|todo}` (repeatable)<br>`--priority {1\|2\|3}` (repeatable)<br>`--effort {S\|M\|L}` (repeatable)<br>`--preparedness {needs-triage\|needs-design\|prepared}` (repeatable)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--tag TAG` (repeatable)<br>`--execution {human\|agent\|hybrid}`<br>`--allow-missing-session-id` | `oaw.lifecycle` | Creates a task note; promoting a capture also updates the source capture's status and links, and --start also creates the caller's run record. | Active (not deprecated) |
| <a id="oaw-cli-task-relation-add"></a>`oaw-cli-task-relation-add` | `oaw task relation add` | add a canonical semantic relationship | `<source>` (required)<br>`<relation-type>` (required)<br>`<target>` (required)<br>`--note NOTE` (required)<br>`--allow-missing-session-id` | `oaw.lifecycle` | Adds a canonical relationship frontmatter entry to one task note. | Active (not deprecated) |
| <a id="oaw-cli-task-relation-remove"></a>`oaw-cli-task-relation-remove` | `oaw task relation remove` | remove a semantic relationship | `<source>` (required)<br>`<relation-type>` (required)<br>`<target>` (required)<br>`--note NOTE` (required)<br>`--allow-missing-session-id` | `oaw.lifecycle` | Removes a relationship frontmatter entry from one task note. | Active (not deprecated) |
| <a id="oaw-cli-task-relation-list"></a>`oaw-cli-task-relation-list` | `oaw task relation list` | list outgoing or derived incoming relationships | `<task>` (required)<br>`--incoming`<br>`--json` | `oaw.relations` | Read-only listing of one task's outgoing or derived incoming relations. | Active (not deprecated) |
| <a id="oaw-cli-task-relation-validate"></a>`oaw-cli-task-relation-validate` | `oaw task relation validate` | validate one reachable graph or the whole vault | `<task>`<br>`--json` | `oaw.relations` | Read-only validation of one reachable graph or the whole vault. | Active (not deprecated) |

<a id="oaw-cli-group-run"></a>
## `oaw run`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-run-list"></a>`oaw-cli-run-list` | `oaw run list` | list run records | `--task TASK`<br>`--state {running\|paused\|completed\|closed}`<br>`--session SESSION`<br>`--current-session`<br>`--json` | `oaw.lifecycle` | Read-only listing of run records. | Active (not deprecated) |
| <a id="oaw-cli-run-close"></a>`oaw-cli-run-close` | `oaw run close` | administratively close a run | `<identifier>` (required)<br>`--reason REASON` (required) | `oaw.lifecycle` | Administratively transitions one run record to closed. | Active (not deprecated) |
| <a id="oaw-cli-run-audit"></a>`oaw-cli-run-audit` | `oaw run audit` | audit registry consistency | — | `oaw.lifecycle` | Read-only consistency audit of the run registry. | Active (not deprecated) |

<a id="oaw-cli-group-note"></a>
## `oaw note`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-note-session"></a>`oaw-cli-note-session` | `oaw note session` | append an Agent sessions entry | `<note-id>` (required)<br>`--note NOTE`<br>`--note-file NOTE_FILE`<br>`--checks CHECKS`<br>`--allow-missing-session-id` | `oaw.lifecycle` | Updates session frontmatter and body on one resolved note. | Active (not deprecated) |
| <a id="oaw-cli-note-observe"></a>`oaw-cli-note-observe` | `oaw note observe` | append a dated observation block | `<note-id>` (required)<br>`--title TITLE` (required)<br>`--body BODY`<br>`--body-file BODY_FILE`<br>`--section SECTION` (default: Observations) | `oaw.retro` | Appends an observation block to one resolved note. | Active (not deprecated) |

<a id="oaw-cli-group-ingest"></a>
## `oaw ingest`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-ingest-safe-export"></a>`oaw-cli-ingest-safe-export` | `oaw ingest safe-export` | ingest frontmatter-approved Markdown files | `--ingestion-root INGESTION_ROOT`<br>`--destination DESTINATION` (default: Imports/Safe export)<br>`--dry-run`<br>`--write` | `oaw.ingest` | Dry-run by default; --write moves approved and rejected handoff files. | Active (not deprecated) |

<a id="oaw-cli-group-link"></a>
## `oaw link`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-link-check"></a>`oaw-cli-link-check` | `oaw link check` | check whether two notes link to each other | `<left>` (required)<br>`<right>` (required) | `oaw.links` | Read-only link inspection. | Active (not deprecated) |
| <a id="oaw-cli-link-list"></a>`oaw-cli-link-list` | `oaw link list` | list explicit wikilinks from a note | `<note>` (required) | `oaw.links` | Read-only link listing. | Active (not deprecated) |
| <a id="oaw-cli-link-ensure"></a>`oaw-cli-link-ensure` | `oaw link ensure` | ensure one durable wikilink exists | `<source>` (required)<br>`<target>` (required)<br>`--section SECTION` (default: Related)<br>`--label LABEL`<br>`--dry-run`<br>`--write` | `oaw.links` | Dry-run by default; --write updates one source note. | Active (not deprecated) |
| <a id="oaw-cli-link-ensure-bidirectional"></a>`oaw-cli-link-ensure-bidirectional` | `oaw link ensure-bidirectional` | ensure durable links in both directions | `<left>` (required)<br>`<right>` (required)<br>`--section SECTION` (default: Related)<br>`--dry-run`<br>`--write` | `oaw.links` | Dry-run by default; --write updates both resolved notes. | Active (not deprecated) |
| <a id="oaw-cli-link-lint"></a>`oaw-cli-link-lint` | `oaw link lint` | suggest durable replacements for opaque ID links | — | `oaw.links` | Read-only link diagnostics. | Active (not deprecated) |
| <a id="oaw-cli-link-materialize"></a>`oaw-cli-link-materialize` | `oaw link materialize` | replace explicit obs:ID prose with durable wikilinks | `<note>` (required)<br>`--dry-run`<br>`--write` | `oaw.links` | Dry-run by default; --write updates one source note. | Active (not deprecated) |

<a id="oaw-cli-group-export"></a>
## `oaw export`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-export-note"></a>`oaw-cli-export-note` | `oaw export note` | export a marked-safe note bundle | `<note-id>` (required)<br>`--target TARGET` (default: work)<br>`--output-root OUTPUT_ROOT`<br>`--force` | `oaw.exports` | Writes an approved export bundle outside the vault. | Active (not deprecated) |
| <a id="oaw-cli-export-validate"></a>`oaw-cli-export-validate` | `oaw export validate` | validate an exported bundle | `<bundle>` (required)<br>`--target TARGET` | `oaw.exports` | Read-only validation of an existing export bundle. | Active (not deprecated) |

<a id="oaw-cli-group-session"></a>
## `oaw session`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-session-lookup"></a>`oaw-cli-session-lookup` | `oaw session lookup` | find notes or artifacts for a session ID | `<session-id>` (required)<br>`--verbose`<br>`--codex-root CODEX_ROOT`<br>`--claude-root CLAUDE_ROOT` | `oaw.sessions` | Read-only vault and harness-artifact lookup. | Active (not deprecated) |
| <a id="oaw-cli-session-snapshot"></a>`oaw-cli-session-snapshot` | `oaw session snapshot` | copy session artifacts for retrospectives | `<session-id>` (required)<br>`--slug SLUG`<br>`--date DATE`<br>`--partial`<br>`--complete`<br>`--codex-only`<br>`--codex-thread CODEX_THREAD` (repeatable)<br>`--codex-rollout CODEX_ROLLOUT` (repeatable)<br>`--claude-session CLAUDE_SESSION` (repeatable)<br>`--grep GREP` (repeatable)<br>`--output-root OUTPUT_ROOT`<br>`--claude-root CLAUDE_ROOT`<br>`--codex-root CODEX_ROOT`<br>`--plugin-data-root PLUGIN_DATA_ROOT` | `oaw.snapshot` | Writes a session snapshot tree to the configured output root. | Active (not deprecated) |

<a id="oaw-cli-group-retro"></a>
## `oaw retro`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-retro-create"></a>`oaw-cli-retro-create` | `oaw retro create` | create a dated retrospective draft | `--title TITLE` (required)<br>`--summary SUMMARY`<br>`--date DATE`<br>`--id REQUESTED_ID`<br>`--force`<br>`--allow-missing-session-id` | `oaw.retro` | Creates or explicitly replaces one retrospective note. | Active (not deprecated) |

<a id="oaw-cli-group-feedback"></a>
## `oaw feedback`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-feedback-create"></a>`oaw-cli-feedback-create` | `oaw feedback create` | create a durable agent-feedback note | `--title TITLE` (required)<br>`--type {pain\|verified\|idea\|bug}` (required)<br>`--scope SCOPE` (required)<br>`--body BODY`<br>`--body-file BODY_FILE`<br>`--command COMMAND`<br>`--tag TAG` (repeatable)<br>`--id REQUESTED_ID`<br>`--date DATE`<br>`--allow-missing-session-id` | `oaw.feedback` | Creates one durable feedback note in the vault. | Active (not deprecated) |

<a id="oaw-cli-group-capture"></a>
## `oaw capture`

| ID | Command | Purpose | Arguments and options | Owner | Mutation scope | State |
| --- | --- | --- | --- | --- | --- | --- |
| <a id="oaw-cli-capture-create"></a>`oaw-cli-capture-create` | `oaw capture create` | create a capture note under Captures/Entries/ | `--title TITLE` (required)<br>`--body BODY`<br>`--body-file BODY_FILE`<br>`--project PROJECT`<br>`--area AREA`<br>`--context CONTEXT`<br>`--outcome OUTCOME`<br>`--url URL` (repeatable)<br>`--tag TAG` (repeatable)<br>`--json`<br>`--allow-missing-session-id` | `oaw.captures` | Creates one capture note under Captures/Entries/; with --project also sets project frontmatter and links the capture and project Index. | Active (not deprecated) |
| <a id="oaw-cli-capture-list"></a>`oaw-cli-capture-list` | `oaw capture list` | list captures vault-wide by frontmatter type | `--status STATUS`<br>`--project PROJECT`<br>`--sort {newer\|older}` (default: newer)<br>`--json` | `oaw.captures` | Read-only vault-wide capture listing across all statuses. | Active (not deprecated) |
| <a id="oaw-cli-capture-show"></a>`oaw-cli-capture-show` | `oaw capture show` | show one capture note from any vault location | `<note-id>` (required)<br>`--json` | `oaw.captures` | Read-only display of one capture note from any location. | Active (not deprecated) |
| <a id="oaw-cli-capture-triage"></a>`oaw-cli-capture-triage` | `oaw capture triage` | transition a canonical capture's status | `<note-id>` (required)<br>`--status {inbox\|incubating\|parked\|reference\|triaged\|discarded}` (required)<br>`--reason REASON`<br>`--no-reason`<br>`--review-after REVIEW_AFTER`<br>`--destination DESTINATION` (repeatable)<br>`--json`<br>`--allow-missing-session-id` | `oaw.captures` | Updates a canonical capture's status, review-after, destinations, reciprocal links, session provenance, and triage audit in one transaction. | Active (not deprecated) |
