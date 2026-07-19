# Project workspace creation and research packets

Read this before running `oaw project create` or any `oaw research` command.

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

Use the `oaw-research` skill for provider-visible handoff preflight, exact copy
output, finished-report intake, raw-artifact preservation, and provider-specific
normalization. Do not pre-create pending provider placeholders.
