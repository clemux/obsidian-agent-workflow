# Note intake, retrospectives, and agent feedback

Read this before running `oaw note`, `oaw retro`, or `oaw feedback` commands.

## Note intake

Use `oaw note` for append-safe updates on resolved notes that are not project task lifecycle changes.

Append the same `## Agent sessions` entry shape to any resolved note:

```bash
oaw note session AGT-TSK-example --note "Reviewed the retrospective habit."
```

Append a dated observation block under `## Observations` or another explicit heading:

```bash
oaw note observe EXP-RES-evidence-example \
  --title "Wrap-up format gap" \
  --body "The evidence note needs a mechanical append path."
oaw note observe EXP-RES-evidence-example \
  --title "Shell-safe evidence" \
  --body-file /path/to/observation.md
oaw task note EXP-TSK-example --note-file - < /path/to/session-note.md
```

`note session` follows the same exactly-one `--note` / `--note-file` rule as task
session entries. `note observe` requires exactly one of `--body` or `--body-file`;
use `--body-file -` for standard input. Sources are UTF-8 and preserve their raw
Markdown before the command's existing note-formatting rules; empty or unreadable
sources fail without changing the target note.

Create a draft retrospective note under `Agents/Retrospectives/`:

```bash
oaw retro create \
  --title "Resolver dogfood" \
  --summary "Captured the resolver workflow and follow-ups."
```

`oaw note session` and `oaw retro create` follow the run-write session provenance
policy from the core skill: a real session ID is required unless the user
explicitly accepts `--allow-missing-session-id`. `note session` maintains the
same deduplicated `session-ids` frontmatter list as task writes; retrospective
creation initializes that list. `oaw note observe` does not require a session ID.
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
