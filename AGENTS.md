# Repository Guidelines

## Project Structure & Module Organization

This repository provides the `oaw` local CLI and its agent skill metadata:

- `bin/oaw` contains the executable Python CLI for resolving Obsidian IDs and updating task lifecycle state.
- `tests/test_oaw.py` contains the `unittest` coverage for resolver behavior, duplicate handling, and lifecycle writes.
- `skills/oaw/SKILL.md` documents the agent-facing workflow for using the CLI.
- `skills/oaw/agents/openai.yaml` contains OpenAI skill display metadata.
- `README.md` is the user-facing overview and install guide.

Keep small workflow changes close to `bin/oaw` and mirror behavior changes in tests and docs. For board-related changes, update the CLI, `tests/test_oaw.py`, `README.md`, and `skills/oaw/SKILL.md` together so agent-facing behavior does not drift from implementation.

## Build, Test, and Development Commands

- `uv run pytest` runs the full test suite.
- `uv run pytest tests/test_oaw.py` runs only the current CLI tests.
- `python bin/oaw --help` shows top-level CLI commands.
- `python bin/oaw resolve --json OAW-TSK-cli` exercises vault resolution.
- `OAW_VAULT=/tmp/example-vault python bin/oaw ...` points the CLI at a non-default vault for manual testing.

There is no build step or dependency installation; the project uses only the Python standard library.

## CLI Dogfooding

When changing `bin/oaw`, use the updated CLI from the active checkout or worktree for subsequent OAW operations: run `python bin/oaw ...` instead of the separately installed `oaw` until the change is integrated. This prevents an older installed version from hiding integration problems and continuously exercises argument parsing, output, resolution, and lifecycle behavior.

- Exercise changed or newly combined behavior against a temporary vault with `OAW_VAULT` before any real-vault write.
- When the operation is safe and relevant, use the checkout CLI for the current task's real-vault resolution and lifecycle bookkeeping too. Do not dogfood experimental or destructive writes against the real vault; prefer dry-run modes and temporary fixtures.
- Treat friction found while dogfooding as evidence: record the command and observed behavior in the related OAW task, or create a focused OAW task when no suitable note exists. Add a regression test and fix it immediately when the correction is small and in scope.
- Include the dogfooding command among the checks reported in the task note or pull request. Fall back to the installed `oaw` only when the checkout version is itself broken, and record that failure.

## Coding Style & Naming Conventions

Write Python 3 with 4-space indentation, useful type hints, and small functions with user-facing errors raised as `OawError`. Use `snake_case` for functions and variables, `PascalCase` for classes and dataclasses, and uppercase constants such as `DEFAULT_VAULT`.

Prefer `pathlib.Path`, UTF-8 file reads/writes, and `json.dumps` for machine-readable modes. Keep CLI messages concise and stable because tests and agents may rely on them.

## Testing Guidelines

Tests use `unittest` and temporary vault fixtures via `tempfile.TemporaryDirectory`. Name new tests `test_<behavior>` and verify return codes plus important stdout/stderr text. For lifecycle changes, assert task note and board contents, not only command success.

Run `uv run pytest` before submitting changes. Update tests whenever resolver matching, frontmatter parsing, board movement, session detection, or CLI arguments change.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, with optional scopes such as `docs(oaw): ...`. Keep commits focused and mention the affected area when useful.

Pull requests should include a behavior summary, the test command run, and any vault/schema assumptions. Link related task IDs such as `OAW-TSK-cli`, and include terminal snippets only when they clarify CLI behavior.

## Security & Configuration Tips

The default vault path is user-specific. Use `OAW_VAULT` for tests, demos, and automation so commands do not accidentally modify a real Obsidian vault. Lifecycle commands require a real session environment variable unless `--allow-missing-session-id` is intentionally accepted.

## Privacy & Portability

This project is used on multiple machines (personal and work), so hard-coded personal paths and identifiers are technical debt. Existing occurrences (the `DEFAULT_VAULT` constant, README examples) are known legacy debt; do not add new ones, and prefer removing them when touching nearby code or docs.

- Never introduce new hard-coded absolute paths, usernames, hostnames, or vault names in code. Route any new machine- or user-specific value through an environment variable (like `OAW_VAULT`), a CLI flag, or a `~`-relative default expanded with `Path.expanduser()`.
- In `README.md`, `skills/oaw/SKILL.md`, and other tracked docs, write examples with placeholders (`/path/to/vault`, `$OAW_VAULT`, `~/vaults/example`) instead of pasting real command output. Redact `/home/<user>/...` paths and real vault or project names from terminal snippets before committing them.
- Keep personal names and usernames out of prose in tracked files; write "the user's vault" rather than naming its owner.
- Machine-local configuration such as `.claude/settings.local.json` must stay untracked; it is listed in `.gitignore` and must not be committed even if it seems useful to share.
- Before committing, check the diff for absolute paths under `/home/` or other personal identifiers, and flag any that are intentional.
