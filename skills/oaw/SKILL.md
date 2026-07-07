---
name: oaw
description: Resolve Obsidian vault reference IDs and manage Obsidian Agent Workflow task lifecycle. Use when Codex sees an obs-prefixed ID, needs to resolve `AGT-*`, `PMX-*`, `SR-*`, `CDX-*`, `OAW-*`, or other frontmatter IDs in clemux's Obsidian vault, or needs to start/complete a project task note with an agent-session trace.
---

# OAW

## Overview

Use the local `oaw` CLI instead of broad filesystem/session-history searches when resolving Obsidian reference IDs. The resolver matches only note frontmatter `id` and `aliases`, making lookups auditable and narrow.

## Resolve IDs

Prefer cheap views first:

```bash
oaw resolve obs:AGT-TSK-obsidian-task-ids
oaw resolve --path OAW-TSK-cli
oaw resolve --meta SR-index
oaw resolve --outline CDX-TSK-dogfood-metrics
oaw resolve --json PMX-TSK-xdist
```

Use `--full` only after deciding the note body is needed.

## Project Task Lifecycle

Use lifecycle writes only for project task notes under `Projects/*/Tasks`:

```bash
oaw task start OAW-TSK-cli --note "Started resolver implementation."
oaw task complete OAW-TSK-cli --note "Finished and verified." --checks "python -m unittest"
```

Lifecycle commands update frontmatter status, append an `## Agent sessions` line, and move the task card on the project board when present. They never invent session IDs. If no stable harness session ID exists, do not use lifecycle writes unless the user explicitly accepts `--allow-missing-session-id`.

## Rules

- Treat `obs:<ID>` as a lookup trigger; `obs:` is not part of the stored note ID.
- Do not resolve IDs by searching `.codex`, `.claude`, `.agents`, or session transcripts unless the user asks for forensic work.
- Keep durable written links as path links with ID display text, for example `[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]`.
- Use `oaw` before falling back to manual vault search. If `oaw` reports duplicates or no match, surface that error instead of guessing a path.
