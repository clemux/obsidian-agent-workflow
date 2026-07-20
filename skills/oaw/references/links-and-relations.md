# Link management and semantic task relationships

Read this before running `oaw link` commands or mutating task relations with
`oaw task relation`. The core skill keeps the short policy (durable link format,
hard `blocked-by` semantics); this file has the full command surface.

## Link management

Use `oaw link` for durable wikilink checks and append-only repairs:

```bash
oaw link check OAW-TSK-cli OAW-TSK-session-lookup
oaw link list OAW-TSK-cli
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link ensure OAW-TSK-cli OAW-TSK-session-lookup --label "Session lookup"
oaw link ensure-bidirectional OAW-TSK-cli OAW-TSK-session-lookup --section Related
oaw link lint
```

- `check` reports whether each note links to the other.
- `list` prints explicit wikilinks from a note and resolves each target when possible.
- `ensure` and `ensure-bidirectional` default to a dry-run preview. Pass `--write` only when the user asked to apply the append-only section edit.
- One-way `ensure --label` overrides the target ID used as display text.
- Edits use durable `[[vault/path|ID]]` links and skip duplicates when a path-form link is already present with any alias.
- `lint` reports opaque ID links such as `[[OAW-TSK-cli]]` and suggests durable replacements when the ID resolves.

## Materializing obs references

`oaw link materialize <note> [--dry-run|--write]` rewrites explicit `obs:<ID>` prose
mentions (for example `See obs:OAW-TSK-session-lookup.`) into durable
`[[vault/path|ID]]` wikilinks:

```bash
oaw link materialize OAW-TSK-cli
oaw link materialize OAW-TSK-cli --write
```

- `--dry-run` is the default and only previews the replacements that would be made;
  pass `--write` to apply them. Passing both is a usage error.
- Resolution reuses the normal exact-ID, alias, and short-project-alias policy. If
  any eligible reference in the note is missing, ambiguous, or malformed, the whole
  operation aborts before a single byte is written — no partial materialization, no
  guessed filename, no stub creation.
- Eligible references are plain prose occurrences of `obs:<ID>` outside frontmatter.
  The parser leaves untouched: existing wiki (`[[...]]`) and Markdown (`[text](url)`
  or reference-style `[text][label]`) links and embeds, autolinks (`<obs:...>`),
  inline code spans and fenced code blocks, backslash-escaped occurrences
  (`\obs:OAW-TSK-cli`), reference-style link-definition lines and labels, and
  references embedded inside a bare URI/query value or larger word/path form
  (`?ref=obs:OAW-TSK-cli`, `file/obs:OAW-TSK-cli.md`, `obs:OAW-TSK-cli.md`,
  `obs:OAW-TSK-cli#Heading`, `obs:OAW-TSK-cli^block`).
- The same engine runs at write time for `oaw task create`, task lifecycle notes
  (`task backlog|start|review|complete`, `task note`), `oaw project create`
  (`--goal`), `oaw note session`, `oaw note observe`, `oaw feedback create`, and
  `oaw retro create` (summary). Each of those commands aborts the whole write under
  the same strict-resolution rule before touching the vault.

## Semantic task relationships

Use first-class task relations for dependency, sequence, and provenance semantics:

```bash
oaw task relation add OAW-TSK-source blocked-by OAW-TSK-dependency \
  --note "Dependency must finish first."
oaw task relation remove OAW-TSK-source blocked-by OAW-TSK-dependency \
  --note "Dependency was redirected explicitly."
oaw task relation list OAW-TSK-source
oaw task relation list OAW-TSK-dependency --incoming --json
oaw task relation validate OAW-TSK-source
oaw task relation validate --json
```

- Canonical forward-only properties are `blocked-by`, `follows`, and `follow-up-to`.
  Values are flat YAML lists of durable `[[vault/path|ID]]` task links. Never persist
  inverse `blocks`, `precedes`, or `follow-ups` fields; OAW derives them.
- `blocked-by` is hard: only a target in `done` satisfies it. `superseded`, missing,
  malformed, non-task, duplicate, self-referential, or cyclic dependencies remain
  blocking until explicitly removed or redirected. `follows` and `follow-up-to` are
  validated but do not gate lifecycle transitions.
- Starting blocked work is allowed for triage, design, preparation, or partial work;
  surface the CLI's blocker output. `review` and `complete` refuse unresolved or invalid
  hard blockers. Never remove a relationship merely to make a lifecycle command pass.
- Relation mutations append provenance without changing lifecycle, preparedness, or run
  state. `list` is outgoing by default; `--incoming` derives inverse edges. `validate`
  checks the reachable graph for one task, or the whole vault when no task is supplied.
- Treat `validate` as authoritative for graph-wide invariants such as cycles and
  canonical link shape. The Base directly detects unresolved, non-task, self, and
  duplicate blockers and derives satisfaction for CLI-maintained relationships.
- Generic `## Related` links remain compatible and carry no dependency semantics.
