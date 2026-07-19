---
name: obsidian-capture
description: Capture side observations, improvement ideas, tangents, research leads, and "do not forget this" notes into the user's Obsidian vault without derailing the current task. Use when the user says `obsidian-capture`, asks for a side conversation/capture while working, wants to park something that could be improved later, or raises a non-urgent idea during focused work. Creates one note per capture under `Captures/Entries/` using the Captures V1 schema.
---

# Obsidian Capture

Use this skill to preserve a thought cheaply, then return to the active thread. The
goal is not to solve the captured idea now; it is to make the idea findable during
review.

## Destination and CLI ownership

Read `obsidian-personal` before the first vault operation. It owns the vault target,
connection preflight/retry policy, and shell-safe command guidance. Create one
vault-relative note per capture:

```bash
obsidian vault="Vault Name" create \
  path='Captures/Entries/CAP-YYYYMMDD-short-slug.md' content='...'
```

Use the registered standalone Obsidian CLI. Do not pass Electron-era flags such as
`--no-sandbox`. If the connection still fails after the bounded retry documented in
`obsidian-personal`, report the failure instead of bypassing Obsidian with a direct
vault filesystem write.

If the capture is specifically about an agent/Obsidian CLI problem that belongs on the
agent-feedback board, use `Agents/Feedback/` instead. Otherwise default to
`Captures/Entries/`; project-specific inboxes are filtered views, not folders.

If the payload is an external-LLM research prompt/result exchange, do not create a
general capture. Route it to `Projects/<Project>/Research/<track>/`: OAW owns packet
scaffolding and launched-run registration; `oaw-research` owns provider-visible
handoff and finished-report intake.

## Capture Schema

Use this frontmatter:

```yaml
---
id: CAP-YYYYMMDD-short-slug
aliases:
  - CAP-YYYYMMDD-short-slug
type: capture
created: YYYY-MM-DD
status: inbox
project:
area:
context:
outcome:
review_after:
destinations:
session-ids:
  - "<stable-harness-session-id>"
tags:
  - capture
---
```

Guidance:

- `id`: stable `CAP-YYYYMMDD-short-slug`; keep it if the capture is later routed.
- `status`: start with `inbox` unless the user explicitly says it is `parked`,
  `incubating`, `reference`, `triaged`, or `discarded`.
- `project`: short lowercase project key when obvious, such as `website`,
  `mobile-app`, or `agent-tools`; otherwise leave empty.
- `area`: broad area such as `organization`, `projects`, `tools`, or `home`; leave
  empty if unclear.
- `context`: one-line trigger for why this came up.
- `outcome`: expected next shape if known, such as "Create task later", "Evaluate
  tool", "Improve workflow", or "Reference only"; leave empty if unclear.
- `destinations`: wikilinks only after routing; leave empty for normal inbox captures.
- `session-ids`: include the current stable harness session ID as a list item when one
  exists; omit the whole property when none is exposed, and never fabricate an ID.
- `tags`: always include `capture`; also preserve explicit user-supplied topical tags
  such as `ai`, `agent-documentation`, `agentic-workflow`, `blog`, or
  `documentation` so tag-filtered Base views can surface the capture later.


### URL source captures

When the captured text includes one or more `http://` or `https://` URLs, attempt to
preserve a raw article/content snapshot in the vault. Default to Defuddle for
article extraction. Save extracted Markdown under a vault-local attachment path such
as:

```text
Captures/Attachments/<capture-id>/<slug>.defuddle.md
```

Track each extraction attempt in frontmatter with a flexible list of tool runs:

```yaml
source_captures:
  - url: https://example.com/article
    tool: defuddle
    status: success # success | failed | skipped
    attachment: Captures/Attachments/CAP-YYYYMMDD-short-slug/article.defuddle.md
    captured_at: YYYY-MM-DD
    error:
```

If Defuddle fails, still create the capture note. Set `status: failed` and record a
short `error:` string in `source_captures` so failures can be reviewed later. If
Defuddle is unavailable or intentionally skipped, use `status: skipped` and explain
why in `error:`. Future fallback tools should add their own entries with their tool
name rather than replacing the Defuddle attempt.

Raw article attachments inherit the capture note privacy level. Do not put them
under `Public/` unless the user explicitly asks for a publishable artifact.


## Body Shape

Keep the note short enough that capture stays cheap:

```markdown
# Human readable title

One or two paragraphs or bullets capturing the idea, why it came up, and what would be
useful later.

## Next review hint

- Optional: concrete question, possible destination note, or first follow-up.
```

For a side conversation during active work, include the current task context and the
improvement idea. Do not expand into a full plan unless the user explicitly asks to
switch from capture to planning.

## Workflow

1. Identify the smallest faithful capture: title, slug, context, and the idea.
2. If the capture contains URLs, attempt raw source capture with Defuddle and record
   success, failure, or skip state in `source_captures`.
3. Create the note in `Captures/Entries/` with `status: inbox`, explicit vault
   targeting, and real session provenance when available.
4. Read it back only when the content is shell-sensitive or substantial.
5. Tell the user it was captured and point them back to the active next step.

Use today's local date from the environment or `date +%F` when uncertain. Avoid asking
metadata questions unless the destination is genuinely ambiguous; empty metadata is
better than breaking flow.

## Review Surface

The capture review surface is `Captures/Captures.base`, especially the Inbox view.
It also exposes tags and has a Documentation ideas view for `ai`,
`agent-documentation`, `agentic-workflow`, `blog`, and `documentation` captures.
`Captures/Index.md` documents the schema and statuses.
