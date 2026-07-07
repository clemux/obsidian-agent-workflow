# obsidian-agent-workflow

Local-first tooling for resolving Obsidian reference IDs and recording agent work on project task notes.

## `oaw`

`oaw` resolves vault IDs from note frontmatter instead of asking agents to search broad local state:

```bash
oaw resolve obs:AGT-TSK-obsidian-task-ids
oaw resolve --path OAW-TSK-cli
oaw resolve --json SR-index
```

It can list project notes by frontmatter type. Task listing is the default; capture listing hides `status: archived` notes unless explicitly requested:

```bash
oaw list --project Fable
oaw list --project Fable --type capture
oaw list --project Fable --type capture --include-archived
```

It also supports a conservative task lifecycle for project tasks under `Projects/*/Tasks`:

```bash
oaw task start OAW-TSK-cli --note "Implemented resolver and lifecycle CLI."
oaw task complete OAW-TSK-cli --note "Verified end-to-end." --checks "python -m unittest"
```

Lifecycle commands update task frontmatter, append an `## Agent sessions` trace, and move the matching card on the project `Board.md` when one exists. They never invent a session ID; pass a real ID through a known harness env var such as `CODEX_THREAD_ID`, or use `--allow-missing-session-id` explicitly.

## Install

Symlink the command onto `PATH`:

```bash
ln -s /home/clemux/dev/obsidian-agent-workflow/bin/oaw ~/.local/bin/oaw
```

The vault defaults to `/path/to/vault`. Override with `OAW_VAULT`.
