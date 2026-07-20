# Testing mistakes and reusable prevention ideas

This is a traceability log for test-suite bloat discovered while maintaining
OAW. It records what went wrong locally and the instruction or automated gate
that could prevent the same pattern across other projects.

This is not a list of rules that every project must adopt unchanged. A future
session should review these entries, group the recurring patterns, and promote
only broadly applicable guidance into shared project instructions or tooling.

## How to maintain this log

Add an entry when test cleanup reveals a reusable mistake rather than a
one-off typo. Include:

- the date and commit, task, or working batch that exposed it;
- the concrete symptom and why it was costly or misleading;
- what was removed, consolidated, or retained;
- a candidate cross-project instruction or static gate;
- exceptions that keep the guidance from becoming over-broad.

Do not log every deleted assertion. Prefer patterns that could recur in another
repository or survive unnoticed across several refactors.

## Findings from the 2026-07-20 test-bloat cleanup

### Retired behavior survived as non-interference tests

**Evidence:** commit `d0aced7` (`test: remove legacy kanban scaffolding`).

The production CLI no longer implemented Kanban-board coupling, but the suite
still created a complete retired `Board.md` fixture and repeatedly asserted
that commands did not update it or print `Board:`. A dedicated boardless
lifecycle test also protected the absence of already-removed behavior.

**Why it was bloat:** the retired feature continued to shape fixtures, names,
output assertions, contributor guidance, and performance-report entries. New
work paid maintenance cost for a behavior the product no longer had.

**Candidate cross-project instruction:** when a feature is removed, search for
its fixtures, negative assertions, test names, helper factories, docs, and
performance artifacts. Keep non-interference coverage only when the retired
artifact is still accepted input or a documented compatibility boundary.

### Migration scaffolding outlived the migration

**Evidence:** commit `b4054af` (`test: remove obsolete test harnesses`).

One suite still used `unittest.TestCase`, `self.assert*`, and manual temporary
directories after the repository had standardized on pytest, plain assertions,
and `tmp_path`. Historical labels also described current behavior as legacy.

**Why it was bloat:** two testing idioms and two resource-lifecycle patterns had
to be understood and maintained even though only one was canonical.

**Candidate cross-project instruction:** every testing-framework or fixture
migration needs an explicit completion search for old base classes, assertion
shims, temporary-directory APIs, compatibility helpers, and historical names.
Do not declare the migration complete while both idioms remain without a
documented reason.

### Runtime tests duplicated static guarantees

**Evidence:** commit `445fd37` (`test: replace static checks with lint rules`)
and [`docs/linting.md`](linting.md).

A custom AST parser and its own self-test enforced the absence of `argparse` in
the Typer frontend. Another assertion checked that a statically typed object
was a `Typer` instance, and a one-line test checked inherited
`Exception.__str__` behavior. Ruff's `TID251` banned-API rule now owns the
dependency constraint.

**Why it was bloat:** pytest reimplemented a static analysis feature and then
needed tests for that test-only implementation. Other assertions repeated facts
already derived from source types or guaranteed by an unchanged base class.

**Candidate cross-project instruction:** before adding a source-inspection or
type-shape test, check whether the configured linter or type checker can express
the rule. Prefer one static gate with narrow documented exceptions. Retain
runtime tests for parsed data, registration, serialization, I/O, and behavior
the static tool cannot observe.

### Shared test helpers became dead framework code

**Evidence:** commit `4585e25` (`test: remove redundant coverage`); unused
helpers in `tests/support.py` and `tests/test_captures.py`.

Several shared assertion and capture-creation helpers had no callers after test
architecture refactors.

**Why it was bloat:** shared helpers look authoritative and invite reuse, so
dead helpers impose more cognitive cost than an unused local function.

**Candidate cross-project instruction:** after consolidating or moving tests,
search every changed test-support module for zero- and one-call helpers. Delete
zero-call helpers. Inline one-call wrappers unless they preserve a meaningful
contract, diagnostic, or complex setup boundary.

### Tests pinned internal call counts instead of outcomes

**Evidence:** commit `4585e25`; resolver, link materialization, capture listing,
and lifecycle tests.

Tests monkeypatched private helpers and asserted one parse, one resolution, one
vault walk, or one body read. These checks encoded current optimization tactics
rather than public behavior.

**Why it was bloat:** a valid cache, index, batching strategy, or service split
could fail tests without changing user-visible behavior. Conversely, exact call
counts were not meaningful performance budgets.

**Candidate cross-project instruction:** do not assert private call or traversal
counts unless the count is itself a documented contract. Test outputs and side
effects for correctness. If performance matters, define a benchmark or explicit
budget with representative data instead of a mock-call oracle.

### Parser-only errors were repeated in domain suites

**Evidence:** commit `4585e25`; conflicts for safe export, link commands, run
listing, and note/body inputs.

The native Typer contract suite centrally covered mutually exclusive options,
but command-specific suites repeated the same parser branch with weaker error
assertions and extra vault setup.

**Why it was bloat:** one argument-declaration bug would fail several tests that
did not represent distinct domain behavior. The repeated tests obscured where
the parser contract was actually owned.

**Candidate cross-project instruction:** assign argument parsing, domain logic,
and process-boundary behavior to explicit test layers. Cover each parser rule
once in the command-contract layer. Add a domain-suite case only when execution
reaches distinct domain code or proves a separate side-effect invariant.

### Parametrized cases paid for every possible prerequisite

**Evidence:** the durable-prose capability table updated in commit `4585e25`.

Each of nine rows created an agent task, project template, and research template
even though a row needed at most one of them.

**Why it was bloat:** parametrization hid repeated filesystem writes and caused
every command to scan irrelevant fixture data.

**Candidate cross-project instruction:** make each parametrized case declare or
construct only its own prerequisites. Avoid union fixtures that build the
combined needs of every row unless shared setup is proven cheaper and remains
behaviorally neutral.

### Tests repeated invariants already enforced by the exercised code

**Evidence:** the catalog coverage cleanup in commit `4585e25`.

A catalog test separately compared live command paths with semantic metadata,
while every catalog render already raises on the same mismatch. Other tests and
the repository catalog check exercise the renderer.

**Why it was bloat:** the same invariant had multiple owners, making it unclear
which layer should change when the command surface evolved.

**Candidate cross-project instruction:** when production generation or
validation code explicitly enforces an invariant, test the validator's success
and failure behavior rather than reimplementing the invariant in another test.
Keep separate assertions only for additional metadata quality the validator
does not enforce.

### Compatibility coverage can resemble bloat without being obsolete

**Evidence:** exploration of historical capture locations, safe-export markers,
run-record fields, and root task layouts during the same cleanup.

Several old-looking tests traced to documented production compatibility paths.
They were retained because removing them would change accepted data or hide
older vault records.

**Why this matters:** a cleanup heuristic based only on words such as `legacy`,
`old`, or `compatibility` can delete real user-data support.

**Candidate cross-project instruction:** before deleting legacy-looking tests,
trace them through production code, current documentation, stored-data formats,
and migration status. Treat compatibility retirement as a behavior decision
with migration evidence, not as routine test cleanup.

## Candidate shared instruction themes

A future cross-project session should consider turning the findings above into
a short reusable policy built around these themes:

1. Give every contract one primary test layer.
2. Prefer outcomes over private call counts.
3. Prefer static gates over source-inspection tests.
4. Make parametrized setup proportional to each case.
5. Audit test scaffolding when migrations or features finish.
6. Require production/docs/data tracing before retiring compatibility tests.

The shared policy should remain concise. Keep the detailed rationale and local
examples here rather than copying this entire document into every project's
`AGENTS.md`.
