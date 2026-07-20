---
name: obsidian-capture
description: Capture side observations, improvement ideas, tangents, research leads, and "do not forget this" notes into the user's Obsidian vault without derailing the current task. Use when the user says `obsidian-capture`, asks for a side conversation/capture while working, wants to park something that could be improved later, or raises a non-urgent idea during focused work. Creates one note per capture under `Captures/Entries/` using the Captures V1 schema.
---

# Obsidian Capture

Use this skill to preserve a thought cheaply, then return to the active thread. The
goal is not to solve the captured idea now; it is to make the idea findable during
review.

## Destination and CLI ownership

Before any real-vault operation, read and follow `obsidian-personal`. It owns vault
targeting, preflight/retry policy, and shell-safe command guidance. Use installed
`oaw` for real-vault capture writes; use `uv run python bin/oaw ...` only while
developing OAW against a temporary `OAW_VAULT`.

Create one capture note per idea with the `oaw` CLI. It owns the schema: it generates
the `CAP-YYYYMMDD-<slug>` ID and aliases, stamps a full timezone-aware `created`
timestamp, sets `status: inbox`, records real session provenance automatically, and
writes the note atomically under `Captures/Entries/`. Do not hand-write capture
frontmatter or use the raw `obsidian create` command for captures.

```bash
oaw capture create --title "Human readable title" \
  --context "one-line trigger for why this came up" \
  --project obs:PROJECT --tag ai --url https://example.com/source
```

Set `OAW_VAULT` to the vault root first. If session provenance is unavailable and the
user accepts an untraceable capture, pass `--allow-missing-session-id`; never fabricate
a session ID. If `oaw` itself fails, report the failure instead of bypassing it with a
direct vault filesystem write.

If the capture is specifically about an agent/Obsidian CLI problem that belongs on the
agent-feedback board, use `oaw feedback create` instead. Otherwise default to a capture;
project-specific inboxes are filtered views, not folders.

If the payload is an external-LLM research prompt/result exchange, do not create a
general capture. Route it through OAW research packet scaffolding and `oaw-research`
provider handoff instead.

## Capture Schema

`oaw capture create` produces the Captures V1 schema; supply its fields through options
rather than typing YAML:

- `--title` (required): the human-readable title; also the source of the generated
  `CAP-YYYYMMDD-<slug>` ID and the `# Title` body heading.
- `--body` / `--body-file` (optional): put the context you actually have into the body —
  what came up, why it matters, and what would be useful later. `--body-file -` reads
  stdin, which is safer for multi-line or shell-sensitive content. Do not invent filler;
  a short faithful capture is better than a padded one, and omitting the body is fine.
- `--project obs:<ALIAS>`: link the capture to a project when it clearly belongs to one;
  this records the project and cross-links the project Index. Leave it off otherwise.
- `--area`, `--context`, `--outcome`: optional single-line metadata (broad area; the
  trigger; the expected next shape such as "create task later" or "reference only").
- `--url` (repeatable): cite each `http://`/`https://` source as an ordinary property.
  URLs are recorded as citations only — do not fetch, extract, or snapshot their content.
- `--tag` (repeatable): `capture` is always applied; add explicit user-supplied topical
  tags such as `ai`, `agent-documentation`, `blog`, or `documentation` so tag-filtered
  Base views surface the capture later.

`created` is a full UTC datetime with seconds, not a bare date, so captures sort by real
creation time. New captures always start at `status: inbox`; use `oaw capture triage` to
move them to `incubating`, `parked`, `reference`, `triaged`, or `discarded` later.

## Workflow

1. Identify the smallest faithful capture: title, the idea, and the trigger context.
2. Run `oaw capture create` with `--title`, the context you have in `--body`/`--body-file`,
   and any obvious `--project`/`--area`/`--tag`/`--url` values. Cite URLs; never fetch them.
3. Read it back only when the content is shell-sensitive or substantial.
4. Tell the user it was captured and point them back to the active next step.

Avoid asking metadata questions unless the destination is genuinely ambiguous; empty
metadata is better than breaking flow.

## Review Surface

The capture review surface is `Captures/Captures.base`, especially the Inbox view.
It also exposes tags and has a Documentation ideas view for `ai`,
`agent-documentation`, `agentic-workflow`, `blog`, and `documentation` captures.
`Captures/Index.md` documents the schema and statuses.
