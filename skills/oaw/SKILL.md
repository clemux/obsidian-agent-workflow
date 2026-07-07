---
name: oaw
description: This skill should be used when an `obs:`-prefixed reference appears (e.g. `obs:OAW-TSK-cli`), when a frontmatter reference ID such as `AGT-*`, `SR-*`, `CDX-*`, `FAB-*`, or `OAW-*` needs resolving to a note in clemux's Obsidian vault, when starting or completing a project task note with an agent-session trace, or when listing a project's tasks/captures. Provides the `oaw` CLI workflow. (`PMX-*` IDs have a dedicated `pmx` skill.)
---

# oaw — Obsidian ID resolution and task lifecycle

## Overview

The `oaw` CLI resolves reference IDs against note frontmatter (`id` and `aliases` only) in clemux's Obsidian vault, and records agent work on project task notes. Use it instead of grepping the vault or searching local agent state: body-text mentions of an ID are not the note itself, so text search finds decoys; frontmatter matching is narrow and auditable.

The vault defaults to `/path/to/vault`; override with the `OAW_VAULT` environment variable.

## Resolving IDs

Treat `obs:<ID>` as a lookup trigger — `oaw` strips the `obs:` prefix automatically; it is not part of the stored ID. Matching is exact and case-sensitive.

```bash
oaw resolve obs:OAW-TSK-cli   # default view: ID, path, title, matched-by, frontmatter, outline
oaw resolve --path OAW-TSK-cli     # absolute path only
oaw resolve --meta OAW-TSK-cli     # frontmatter only (status, project, priority, ...)
oaw resolve --outline OAW-TSK-cli  # headings with line numbers
oaw resolve --json OAW-TSK-cli     # machine-readable (path, frontmatter, outline)
```

The default view answers most questions. Use `--full` (entire note body) only after deciding the body is actually needed.

On failure `oaw` exits non-zero with a clear message: "no note with frontmatter id or alias" for a miss, or a candidate-path list when an ID is duplicated. Surface that error to the user instead of guessing a path or falling back to text search.

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

## Project task lifecycle

Lifecycle writes apply only to task notes under `Projects/*/Tasks` (the CLI enforces this):

```bash
oaw task start OAW-TSK-cli --note "Started resolver implementation."
oaw task complete OAW-TSK-cli --note "Finished and verified." --checks "python -m unittest"
```

- `start` sets `status: active`; `complete` sets `status: done`.
- `complete` requires `--checks` naming the verification actually run; do not fabricate checks.
- Both append a dated entry under `## Agent sessions` in the note. When the project has a board (`Projects/<Project>/Board.md`), they also move the task's card to the matching column — creating the card, and the column heading, if missing. Cards keep the `- [ ]` marker in every column; the column heading, not the checkbox, reflects status.
- The command's output (`Updated:` / `Status:` / `Board:`) confirms the write. To report resulting state, rely on that output plus `oaw resolve --meta` if needed — do not re-read the whole note with `--full`.
- The session ID is read automatically from the harness environment; the first of `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, `OPENCODE_SESSION_ID`, `GEMINI_SESSION_ID` that is set wins. `oaw` never invents one: with no session variable set, the command fails with a clear error (so there is no need to check the variables beforehand). Pass `--allow-missing-session-id` only when the user explicitly accepts an untraceable entry.

## Rules

- Use `oaw` before any manual vault search. Do not resolve IDs by searching agent state directories (`.codex`, `.claude`, `.agents`) or session transcripts unless the user explicitly asks for forensic work.
- Keep durable written links as path links with the ID as display text, e.g. `[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]` — the link target is the vault-relative path without the `.md` extension. Reuse the path from resolve output already in hand; run `oaw resolve --path` only when no resolve has been done yet.
- If a needed capability is not documented here, check `oaw --help` before reaching for filesystem search.
- For `PMX-*` IDs, prefer the dedicated `pmx` skill and CLI.
