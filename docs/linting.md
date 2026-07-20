# Linting and typing policy

`pyproject.toml` is the executable source of truth for linting, formatting, and
typing. This document records why the project enables each rule family, where
exceptions are allowed, and which guarantees belong to static checks rather
than pytest.

Run the individual checks with:

```console
mise run lint
mise run format-check
mise run typecheck
```

`mise run check` runs all three as part of the canonical repository check.

## Ruff

Ruff targets Python 3.13, formats to a 100-column line length, and enables these
lint rule families:

| Rules | Purpose |
|---|---|
| `E`, `W` | Catch pycodestyle errors and warnings. |
| `F` | Catch undefined names, unused imports, and related Pyflakes errors. |
| `I` | Keep imports consistently ordered. |
| `UP` | Prefer syntax and APIs appropriate for the supported Python version. |
| `B` | Catch common bug-prone Python patterns. |
| `SIM` | Remove needlessly complex constructs when a simpler equivalent is clearer. |
| `TID251` | Enforce project-specific banned API and dependency rules. |

`E501` is ignored because the formatter owns line wrapping and some strings,
paths, and generated-looking contracts are clearer when left intact. The
100-column setting remains the formatter's target, not a second hand-maintained
line-length gate.

### Banned APIs

The OAW command frontend uses Typer. `argparse` is therefore banned by Ruff so a
frontend dependency regression fails the ordinary lint gate instead of relying
on a pytest test that parses source code.

Two standalone scripts intentionally use `argparse` and have narrow `TID251`
per-file exemptions:

- `scripts/check_cli_parity.py`, a development command with its own parser;
- `skills/oaw-research/scripts/oaw_research.py`, a standalone skill helper.

Keep exemptions file-specific. Do not exempt `src/oaw/`, whole directories, or
the complete rule. When another banned dependency is needed, add its rationale
here and prefer the smallest possible exception.

## Pyrefly

Pyrefly checks both `src/**/*.py` and `tests/**/*.py` as Python 3.13 code. Type
annotations are therefore enforced in production modules, shared test helpers,
fixtures, and tests; they are not documentation-only hints.

Pyrefly owns properties derivable from Python source types: valid attribute and
argument access, compatible assignments and returns, and sound narrowing. Do
not add runtime tests whose only purpose is to repeat those facts, such as
checking that a statically typed `Typer` object is an instance of `Typer`.

Static typing does not replace runtime contract tests. Keep tests for:

- parsed JSON, YAML, frontmatter, or filesystem content;
- CLI registration, parsing, exit codes, stdout, and stderr;
- serialization shapes and values exposed to agents or users;
- transaction, concurrency, symlink, and rollback behavior;
- generated artifacts and metadata files that Pyrefly does not interpret;
- architectural constraints not represented by types, unless Ruff or another
  static gate explicitly enforces them.

## Maintaining the policy

When adding or changing a lint or typing rule:

1. Update `pyproject.toml` and this document together.
2. Prefer a static gate over a custom source-inspection test when the static
   tool expresses the rule directly.
3. Remove tests made fully redundant by the new gate, but retain runtime tests
   for behavior the gate cannot observe.
4. Add the narrowest documented exception when a non-production tool has a
   legitimate need for a banned API.
5. Run `mise run check` before committing.
