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
    frontmatter.py   # parse/serialize + round-trip YAML behind one interface
    resolver.py      # vault walk, id/alias matching, project aliases, NoteMatch
    links.py         # wikilink parse, durable links, link commands
    lifecycle.py     # task create/status transitions/notes
    boards.py        # ONE column engine for project boards + Next steps board
    sessions.py      # session env detection, session lookup
    snapshot.py      # session artifact snapshots
    exports.py       # safe outbound export + validate
    ingest.py        # safe-export ingestion
    retro.py         # retro create, note observe/session intake
    cli.py           # argparse assembly + dispatch, thin
bin/oaw              # shim for checkout dogfooding: imports src/oaw, calls cli.main
tests/
```

Reserved seams (do not build yet, per the agent-run planning constraints on
the task note): `runs.py` for the agent-run registry/lifecycle, with task
identity, run identity, and session provenance kept distinct, and
transaction/journal boundaries around registry + task + board mutations.

## Dependency rules

- `cli.py` imports command modules; command modules import shared layers
  (`resolver`, `notes`, `frontmatter`, `boards`, `sessions`); shared layers
  import only `errors` and the stdlib.
- No command module imports another command module. Cross-cutting behavior
  (e.g. lifecycle writes appending session traces) lives in the shared layer.
- Only `frontmatter.py` may import the YAML library. Everything else uses its
  interface, so the permitted round-trip dependency (`ruamel.yaml`) stays
  swappable and the current hand-rolled parser can be kept as the fallback
  during migration.
- `resolver.py` owns all vault scanning. Performance work (pre-filtering,
  frontmatter-only reads, a future stat-validated id index) lands there
  without touching command semantics. Write operations accept an existing
  `NoteMatch` so a lifecycle command resolves once, not twice.

## Packaging

- Replace the `force-include bin/oaw as oaw_cli.py` hack with the src layout
  and `[project.scripts] oaw = "oaw.cli:main"`.
- Runtime dependencies: none initially; `ruamel.yaml` added only at the
  frontmatter-swap step (guardrail: preserves comments, ordering, unknown
  keys, scalar types — verified by round-trip contract tests).
- The installed CLI remains a snapshot (`uv tool install --reinstall .` after
  merges). Installed-parity verification moves from single-file hash to
  package version + `oaw --version` (new flag) once the src layout lands.

## Tooling

All dev-only, via `[dependency-groups] dev` (uv-managed):

- **pytest** (+ `pytest-cov`): migrate from `unittest`. Test split below.
- **ruff**: lint + format (`[tool.ruff]`, line-length 100, rules `E,F,W,I,UP,B,SIM`;
  format replaces manual style policing in review).
- **pyrefly**: type checking (`[tool.pyrefly]`, `project-includes = ["src", "tests"]`,
  `python-version = "3.10"` to match `requires-python`). The codebase already
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
   round-trips, wikilink parsing, board column engine, slug/id derivation.
   Millisecond-fast; this is where most new coverage goes.
2. **Integration** — tmp-vault pytest fixtures exercising resolver, lifecycle,
   boards through function calls. A session-env-scrubbing autouse fixture
   fixes the harness-leak class of flake.
3. **CLI contract** — a small retained subprocess suite pinning exit codes and
   stable output lines that agents and skills rely on (`Updated:`/`Status:`/
   `Board:`, error phrasing). These are the golden tests that gate every
   extraction step.
4. **Perf smoke** (optional, marked, excluded by default): resolve on a
   generated 5k-note fixture with a generous ceiling, so scan regressions
   surface before hitting a real vault.

## Incremental extraction order

Each step: suite green, committed, behavior identical unless stated.

0. **Baseline**: src-layout packaging + `bin/oaw` shim; convert the existing
   suite to pytest mechanically (it keeps passing as subprocess tests); add
   ruff + pyrefly + CI. No behavior change.
1. **`errors` + `notes` + `frontmatter`** (pure helpers first): move
   `split_note`, `parse_frontmatter`, `set_frontmatter_scalar`,
   `append_frontmatter_list_value`; add unit tests. Hand-rolled parser kept.
2. **`resolver`**: move the scan/match code; then the performance ladder from
   `OAW-TSK-cli-performance` — pre-filter before parse, frontmatter-only
   reads, single-resolve writes. Gate: resolve < 150 ms on the perf fixture;
   lifecycle writes scan once.
3. **`boards`**: unify the two board implementations behind one column engine
   (contract tests pin both card formats first).
4. **`lifecycle`**: task create/transitions/notes on top of 1–3.
5. **`sessions` + `snapshot`**, then **`links`**, **`exports`/`ingest`**,
   **`retro`**: mostly mechanical moves once the shared layers exist.
6. **Frontmatter swap**: introduce `ruamel.yaml` behind the `frontmatter`
   interface with round-trip contract tests (comments, ordering, unknown
   keys, quoting preserved). Unlocks safe writes the hand parser refuses.
7. **Reserved**: `runs.py` seams per the agent-run constraints — separate
   proposal once task contracts are approved.

## Non-goals

- No Rust/compiled rewrite: startup is 61 ms; the data says Python is fine
  once scanning is fixed (see `OAW-TSK-cli-performance`).
- No folder-per-command tree, no abstraction that only renames functions.
- No behavior or CLI surface changes during extraction; new capabilities ride
  on later steps only.
