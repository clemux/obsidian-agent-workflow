# OAW package architecture proposal

Status: proposal for review — first deliverable of `OAW-TSK-modular-architecture`.
Written 2026-07-11 from the state-review session, using the friction and
performance observations recorded on the task note.

## Why now

The trigger conditions on the task note are met:

- `bin/oaw` is ~2800 lines; adding one subcommand (`task create`) required
  mapping 157 functions before writing a line, and every reviewed change pays
  that cost.
- Performance data (`OAW-TSK-cli-performance`) shows the binding constraint is
  structural: `resolve` spends ~270 ms of 390 ms hand-parsing frontmatter of
  every vault note, and lifecycle writes scan the vault twice. Fixing this
  cleanly wants a resolver boundary, not a patch inside one big file.
- Eleven overnight branches became unmergeable superseded drafts largely
  because every feature touches the same file; module boundaries are what let
  parallel agent work land.

## Package layout

`src/` layout, domain modules (not folder-per-command):

```
pyproject.toml
src/oaw/
    __init__.py      # version only
    errors.py        # OawError
    notes.py         # note model: split/read/write, atomic writes
    frontmatter.py   # parse/serialize, raw pre-filter, frontmatter-only reads;
                     # round-trip YAML behind one interface
    resolver.py      # vault walk, id/alias matching, project aliases, NoteMatch
    links.py         # wikilink parse, durable links, link commands
    relations.py     # semantic task-link parsing, graph validation, blocker state
    lifecycle.py     # task create/status transitions/notes
    sessions.py      # session env detection, session lookup
    snapshot.py      # session artifact snapshots
    exports.py       # safe outbound export + validate
    ingest.py        # safe-export ingestion
    retro.py         # retro create, note observe/session intake
    feedback.py      # durable agent-feedback creation
    tags.py          # strict creation-only tag serialization
    cli.py           # Typer composition root + dispatch, thin
bin/oaw              # shim for checkout dogfooding: imports src/oaw, calls cli.main
tests/
```

Reserved seams (do not build yet, per the agent-run planning constraints on
the task note). Two distinct modules, mirroring the two extraction seams the
constraints require, both behind the existing task and resolver contracts:

- `run_registry.py` — the agent-run registry: run identity and session
  provenance, kept distinct from task identity.
- `run_lifecycle.py` — run lifecycle transitions and their policy rules.

Shared contract for both: a `TaskRef` value — the canonical frontmatter id
plus the vault-relative Tasks path it resolves to. Every run references a
`TaskRef`; a run never creates a second task note. Lifecycle interfaces must
support the three execution modes (human, agent, hybrid: missing execution
becomes agent only at agent start; explicit human execution refuses agent
lifecycle writes) and multiple concurrently active provider/session runs per
task. Registry + task mutations sit inside transaction/journal boundaries
with rollback or a recoverable journal; partial writes are
rejected or recovered, never silently kept.

## Dependency rules

Explicit acyclic layer graph — imports flow strictly downward:

```
cli.py
  → command domains: lifecycle, links, snapshot, exports, ingest, retro
      → task graph service: relations
      → shared services: resolver, sessions
          → note model: notes, frontmatter
          → leaf utilities: session_metrics
              → errors (+ stdlib)
```

`session_metrics` is a leaf: it imports only stdlib and is imported by
`sessions` (rollout metrics for `session --verbose`). It sits beside the note
model rather than under it — nothing in the note-model layer depends on it.

`cli.py` additionally dispatches straight to shared-service command
entrypoints for the stable `resolve`, `list`, and `session`
subcommands — still a strictly downward edge, skipping the command-domain
layer. Those modules are dual-role: shared service for the command domains,
and direct command implementation for their own subcommands. Rename-only
adapter modules above them would violate the non-goals.

CLI usage and error text is Typer-native and derived from the command tree;
do not reintroduce hand-maintained per-command message or usage tables.

- `cli.py` sits at the top and may import any lower layer; in practice it
  imports the command domains plus the shared-service command entrypoints
  named above. Command domains import lower services and the note model;
  they never import peer command domains. Shared services never import command domains
  or `cli.py`. Cross-cutting behavior (e.g. lifecycle writes appending session
  traces) lives in a shared layer.
- `lifecycle.py` imports the lower semantic graph service in `relations.py` to
  enforce hard blockers during review and completion. `relations.py` depends on
  resolver, link, frontmatter, and note services and never imports lifecycle.
- Shared services (`resolver`, `sessions`) may import `notes` and
  `frontmatter` — `resolver` in particular consumes frontmatter parsing and
  pre-filtering. The note model imports only `errors` and the stdlib.
- The YAML-library exception is confined to `frontmatter.py`: it alone may
  import the YAML library. Everything else uses its interface, so the
  permitted round-trip dependency (`ruamel.yaml`) stays swappable and the
  current hand-rolled parser can remain as the fallback.
- `resolver.py` owns vault traversal, id/alias matching, and any future
  stat-validated id index. `frontmatter.py` owns the cheap raw-frontmatter
  pre-filter and frontmatter-only parsing operations (per the performance
  findings, that is where they belong); resolver calls them during the walk.
  Write operations accept an existing `NoteMatch` so a lifecycle command
  resolves once, not twice.

## Packaging

- Replace the `force-include bin/oaw as oaw_cli.py` hack with the src layout
  and `[project.scripts] oaw = "oaw.cli:main"`.
- Runtime dependencies: none initially; `ruamel.yaml` added only at the
  frontmatter-swap step (guardrail: preserves comments, ordering, unknown
  keys, scalar types — verified by round-trip contract tests).
- The installed CLI remains a snapshot (`uv tool install --reinstall .` after
  merges). Installed-parity verification generalizes the current single-file
  hash to the whole package: compare a manifest of content hashes for every
  module under `src/oaw/` against the corresponding installed package modules
  (`oaw parity-check` or an equivalent dev script). Package version +
  `oaw --version` (new flag) is an additional cheap signal, not the
  verification itself — versions rarely change per commit, so version
  equality cannot prove the installed snapshot matches the checkout.

## Tooling

All dev-only, via `[dependency-groups] dev` (uv-managed):

- **pytest** (+ `pytest-cov`): migrate from `unittest`. Test split below.
- **ruff**: lint + format (`[tool.ruff]`, line-length 100; format replaces manual
  style policing in review). The maintained rule rationale and exceptions live
  in [`docs/linting.md`](linting.md).
- **pyrefly**: type checking (`[tool.pyrefly]`, `project-includes = ["src", "tests"]`,
  `python-version = "3.13"` to match `requires-python`). The codebase already
  carries useful hints; the checker makes them load-bearing.
- **pyinstrument** (already added): performance regression profiling.
- CI (GitHub Actions): `ruff check` + `ruff format --check` + `pyrefly check`
  + `pytest` on push. Keeps overnight/delegated branches honest before review.

## Test split

Today: 72 subprocess-only end-to-end tests, ~19 s, not unit-testable because
the script is not importable, and the environment leaks (a real
`CLAUDE_SESSION_ID` broke a no-session test during this session).

Target:

1. **Unit** — import modules directly, no subprocess, no vault: frontmatter
   round-trips, wikilink parsing, slug/id derivation.
   Millisecond-fast; this is where most new coverage goes.
2. **Integration** — tmp-vault pytest fixtures exercising resolver and lifecycle
   through function calls. A session-env-scrubbing autouse fixture
   fixes the harness-leak class of flake.
3. **CLI contract** — a small retained subprocess suite pinning exit codes and
   stable output lines that agents and skills rely on (`Updated:`/`Status:`,
   error phrasing). These focused native tests gate every
   extraction step.
4. **Perf smoke** (retired): an opt-in marked test resolved against a
   generated 5k-note fixture while the resolver extraction was underway. It
   was removed once the extraction ladder finished — its thresholds were
   machine-dependent and it pinned otherwise-dead resolver variants.
5. **Reserved contract suites** (land with the reserved run seams, specified
   now so the seams stay honest): run-registry, run-lifecycle, and
   transaction/journal contract tests, independent of the CLI frontend — canonical
   `TaskRef` scope, stale runs, multiple concurrent provider/session runs,
   execution-mode policy (human/agent/hybrid), and rejection or recovery of
   partial registry/task writes.

## Incremental extraction order

Each step: suite green, committed, behavior identical unless stated.

0. **Baseline**: src-layout packaging + `bin/oaw` shim; convert the existing
   suite to pytest mechanically (it keeps passing as subprocess tests); add
   ruff + pyrefly + CI. No behavior change.
1. **`errors` + `notes` + `frontmatter`** (pure helpers first): move
   `split_note`, `parse_frontmatter`, `set_frontmatter_scalar`,
   `append_frontmatter_list_value`; add unit tests. Hand-rolled parser kept.
2. **`resolver`**: move the scan/match code; then the performance ladder from
   `OAW-TSK-cli-performance` — frontmatter-layer pre-filter before parse,
   frontmatter-only reads, single-resolve writes. (Completed; the opt-in
   ladder test and the unoptimized baseline variants it compared against
   were removed after the extraction finished.) The real-vault figures
   (387 ms resolve over 6,189 notes, ~270 ms in frontmatter parsing) remain
   observational data. Lifecycle writes must scan once.
3. **`lifecycle`**: task create/transitions/notes on top of 1–2.
4. **`sessions` + `snapshot`**, then **`links`**, **`exports`/`ingest`**,
   **`retro`**: mostly mechanical moves once the shared layers exist.
5. **Frontmatter swap**: introduce `ruamel.yaml` behind the `frontmatter`
   interface with round-trip contract tests (comments, ordering, unknown
   keys, quoting preserved). Unlocks safe writes the hand parser refuses.
6. **Reserved**: `run_registry.py` and `run_lifecycle.py` seams per the
   agent-run constraints (see "Reserved seams" above) — separate proposal
   once task contracts are approved.

## Non-goals

- No Rust/compiled rewrite: startup is 61 ms; the data says Python is fine
  once scanning is fixed (see `OAW-TSK-cli-performance`).
- No folder-per-command tree, no abstraction that only renames functions.
- No behavior or CLI surface changes during extraction; new capabilities ride
  on later steps only.
