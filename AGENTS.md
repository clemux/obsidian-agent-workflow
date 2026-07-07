# Repository Guidelines

## Project Structure & Module Organization

This repository provides the `oaw` local CLI and its agent skill metadata:

- `bin/oaw` contains the executable Python CLI for resolving Obsidian IDs and updating task lifecycle state.
- `tests/test_oaw.py` contains the `unittest` coverage for resolver behavior, duplicate handling, and lifecycle writes.
- `skills/oaw/SKILL.md` documents the agent-facing workflow for using the CLI.
- `skills/oaw/agents/openai.yaml` contains OpenAI skill display metadata.
- `README.md` is the user-facing overview and install guide.

Keep small workflow changes close to `bin/oaw` and mirror behavior changes in tests and docs.

## Build, Test, and Development Commands

- `python -m unittest` runs the full test suite.
- `python -m unittest tests.test_oaw` runs only the current CLI tests.
- `python bin/oaw --help` shows top-level CLI commands.
- `python bin/oaw resolve --json OAW-TSK-cli` exercises vault resolution.
- `OAW_VAULT=/tmp/example-vault python bin/oaw ...` points the CLI at a non-default vault for manual testing.

There is no build step or dependency installation; the project uses only the Python standard library.

## Coding Style & Naming Conventions

Write Python 3 with 4-space indentation, useful type hints, and small functions with user-facing errors raised as `OawError`. Use `snake_case` for functions and variables, `PascalCase` for classes and dataclasses, and uppercase constants such as `DEFAULT_VAULT`.

Prefer `pathlib.Path`, UTF-8 file reads/writes, and `json.dumps` for machine-readable modes. Keep CLI messages concise and stable because tests and agents may rely on them.

## Testing Guidelines

Tests use `unittest` and temporary vault fixtures via `tempfile.TemporaryDirectory`. Name new tests `test_<behavior>` and verify return codes plus important stdout/stderr text. For lifecycle changes, assert task note and board contents, not only command success.

Run `python -m unittest` before submitting changes. Update tests whenever resolver matching, frontmatter parsing, board movement, session detection, or CLI arguments change.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, with optional scopes such as `docs(oaw): ...`. Keep commits focused and mention the affected area when useful.

Pull requests should include a behavior summary, the test command run, and any vault/schema assumptions. Link related task IDs such as `OAW-TSK-cli`, and include terminal snippets only when they clarify CLI behavior.

## Security & Configuration Tips

The default vault path is user-specific. Use `OAW_VAULT` for tests, demos, and automation so commands do not accidentally modify a real Obsidian vault. Lifecycle commands require a real session environment variable unless `--allow-missing-session-id` is intentionally accepted.
