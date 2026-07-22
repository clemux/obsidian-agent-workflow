# Repository Guidelines

## Project Structure & Module Organization

This repository provides the `oaw` local CLI and its agent skill metadata:

- `src/oaw/` contains the CLI implementation as domain modules (`resolver.py`, `notes.py`, `lifecycle.py`, `frontmatter.py`, `cli.py`, ...) for resolving Obsidian IDs and updating task lifecycle state. `src/oaw/document/` is a source-preserving Markdown/frontmatter parsing layer (envelope, YAML composition, Obsidian-syntax recognizers, markdown-it structure, protected regions, and a splice-based editing engine); `src/oaw/doctor.py` is the read-only `oaw doctor` engine that checks a vault against the Obsidian compatibility profile in `src/oaw/document/profile.py`.
- `bin/oaw` is a thin launcher that runs the checkout copy of `src/oaw/` without installing it.
- `tests/` holds the pytest suite: domain-level suites such as `tests/test_resolver.py`, `tests/test_notes.py`, and `tests/test_links.py`; per-domain CLI suites such as `tests/test_task_lifecycle_cli.py` and `tests/test_links_cli.py`; command-contract coverage in `tests/test_typer_cli.py`; and shared vault factories, runners, and snapshot/assertion helpers in `tests/support.py`, from which each test file composes its own minimal vault fixtures.
- `docs/architecture.md` records the package-layout rationale; `docs/linting.md` explains the maintained Ruff and Pyrefly policy; `docs/testing-mistakes.md` tracks reusable test-bloat mistakes and prevention ideas; `docs/claude-code.md` documents the Claude Code session-title hook shipped in `scripts/`.
- `skills/oaw/` documents the agent-facing workflow for using the CLI.
- `skills/obsidian-capture/` preserves side observations in the vault capture workflow.
- `skills/oaw-task-execution/` provides safe repository execution for OAW-owned tasks.
- `skills/oaw-task-review/` provides the interactive project-task status review workflow.
- `skills/oaw-research/` provides provider handoff and report-intake workflow plus tests.
- `skills/oaw-retro-backend/` adapts the shared `retro` skill's follow-up and note persistence to the vault.
- `skills/oaw-wrap-up/` provides urgent and standard operational session closure for OAW work.
- Each skill's `agents/openai.yaml` contains OpenAI display metadata.
- `README.md` is the user-facing overview and install guide.

Keep small workflow changes close to the owning module in `src/oaw/` and mirror behavior changes in tests and docs. Update the CLI, its tests, `README.md`, and `skills/oaw/SKILL.md` together so agent-facing behavior does not drift from implementation.

## Skill Ownership Boundary

This repository owns skills and adapters that require OAW commands, lifecycle state, note schemas,
vault relationships, or other OAW-specific behavior. Keep those integrations here beside the CLI
and schema they depend on.

When part of an OAW workflow is broadly reusable, extract only its backend-neutral core to the
shared `agent-skills` repository. The shared core must not mention OAW commands, vault IDs, note
schemas, personal paths, private project history, or custom dependencies that are not bundled with
that shared skill or supplied by an official platform. Keep the OAW adapter here and make the
dependency direction explicit: this repository may compose a shared core, but the shared core must
not depend on this repository.

The Typer frontend is the current CLI implementation. Native command-contract
coverage lives in `tests/test_typer_cli.py` and covers command-tree completeness,
exit classes, output routing, accepted values, conflicts, and no-write behavior.
Do not reintroduce a historical argparse comparison suite or filesystem golden.
`scripts/check_cli_parity.py` has a narrower purpose: it checks the installed
snapshot against the checkout's help surfaces and source bytes.

## Build, Test, and Development Commands

- `mise install` installs the exact repository-managed `uv`, `shellcheck`, and `prek` executables.
- `mise run check` runs the complete verification suite and is the canonical preflight and final check.
- `mise run test`, `mise run lint`, `mise run format-check`, `mise run typecheck`,
  `mise run publication-check`, and `mise run shellcheck` run individual diagnostic gates.
- `mise run hooks-install` installs the Prek-managed pre-commit and pre-push shims;
  `mise run hooks-check` runs every configured hook against all files.
- `uv run pytest` runs the full test suite.
- `uv run pytest tests/test_task_lifecycle_cli.py` (or any other `tests/test_*_cli.py` file) runs one per-domain CLI suite.
- `uv run pytest tests/test_typer_cli.py tests/test_cli_entry.py` runs focused
  native Typer contracts and checkout launcher coverage.
- `uv run python bin/oaw --help` shows top-level CLI commands.
- `uv run python bin/oaw resolve --json OAW-TSK-cli` exercises vault resolution.
- `OAW_VAULT=/tmp/example-vault uv run python bin/oaw ...` sets the required vault root for manual testing.

When changing Ruff or Pyrefly configuration, update `docs/linting.md` in the
same change with the rule's rationale and any narrowly scoped exceptions.

The CLI has three runtime dependencies, `typer`, `markdown-it-py`, and `pyyaml`, so the
checkout must be run inside the project environment: use `uv run python bin/oaw ...`,
not bare `python bin/oaw ...` (which fails with `ModuleNotFoundError: No module named
'typer'`). The installed `oaw` command carries its own dependencies and needs no prefix.

## CLI Dogfooding

When changing the CLI, use the updated version from the active checkout or worktree for subsequent OAW operations: run `uv run python bin/oaw ...` instead of the separately installed `oaw` until the change is integrated. This prevents an older installed version from hiding integration problems and continuously exercises argument parsing, output, resolution, and lifecycle behavior.

When repository work interacts with the user's real Obsidian vault or runs any
`obsidian` CLI command, load and follow the `obsidian-personal` skill first. It
contains required machine-specific targeting, verified command behavior, and
known CLI limitations; the OAW skill is not a substitute for it.

- Exercise changed or newly combined behavior against a temporary vault with `OAW_VAULT` before any real-vault write.
- When the operation is safe and relevant, use the checkout CLI for the current task's real-vault resolution and lifecycle bookkeeping too. Do not dogfood experimental or destructive writes against the real vault; prefer dry-run modes and temporary fixtures.
- Treat friction found while dogfooding as evidence: record the command and observed behavior in the related OAW task, or create a focused OAW task when no suitable note exists. Add a regression test and fix it immediately when the correction is small and in scope.
- Include the dogfooding command among the checks reported in the task note or pull request. Fall back to the installed `oaw` only when the checkout version is itself broken, and record that failure.

## Coding Style & Naming Conventions

Write Python 3 with 4-space indentation, useful type hints, and small functions with user-facing errors raised as `OawError`. Use `snake_case` for functions and variables, `PascalCase` for classes and dataclasses, and uppercase constants such as `SESSION_ENV`.

Prefer `pathlib.Path`, UTF-8 file reads/writes, and `json.dumps` for machine-readable modes. Keep CLI messages concise and stable because tests and agents may rely on them.

## Testing Guidelines

Tests are pytest-style, using plain `assert` statements, `tmp_path`, `monkeypatch`, and `pytest.mark.parametrize`. Name new tests `test_<behavior>` and verify return codes plus important stdout/stderr text. For lifecycle changes, assert task-note and agent-run contents, not only command success.

When test cleanup reveals a reusable anti-pattern, record it in
`docs/testing-mistakes.md` with evidence, rationale, and a candidate
cross-project prevention rule.

The package requires Python 3.13 or newer. Run `mise run check` before submitting
changes. Use the individual Mise tasks or their underlying `uv run` commands for
focused diagnosis. Update tests whenever resolver matching, frontmatter parsing,
lifecycle behavior, session detection, or CLI arguments change.

## Commit & Pull Request Guidelines

Commit messages must follow Conventional Commits 1.0.0:
`<type>[optional scope][!]: <description>`. Use established types and scopes from
recent history, such as `docs(oaw): ...`, and omit the scope rather than inventing
one. Mark breaking changes with `!` or a `BREAKING CHANGE:` footer. Keep commits
focused and use concise imperative descriptions.

Pull requests should include a behavior summary, the test command run, and any vault/schema assumptions. Link related task IDs such as `OAW-TSK-cli`, and include terminal snippets only when they clarify CLI behavior.

## Security & Configuration Tips

`OAW_VAULT` is required for every command that accesses the vault. Use a temporary value for tests, demos, and automation so commands do not accidentally modify a real Obsidian vault. Lifecycle commands require a real session environment variable unless `--allow-missing-session-id` is intentionally accepted.

## Privacy & Portability

This project is used on multiple machines (personal and work), so hard-coded personal paths and identifiers are technical debt. Do not add new occurrences, and prefer removing legacy examples when touching nearby code or docs.

- Never introduce new hard-coded absolute paths, usernames, hostnames, or vault names in code. Route any new machine- or user-specific value through an environment variable such as `OAW_VAULT` or an explicit CLI flag.
- In `README.md`, `skills/oaw/SKILL.md`, and other tracked docs, write examples with placeholders (`/path/to/vault`, `$OAW_VAULT`, `~/vaults/example`) instead of pasting real command output. Redact `/home/<user>/...` paths and real vault or project names from terminal snippets before committing them.
- Keep personal names and usernames out of prose in tracked files; write "the user's vault" rather than naming its owner.
- Machine-local configuration such as `.claude/settings.local.json` must stay untracked; it is listed in `.gitignore` and must not be committed even if it seems useful to share.
- Before committing, check the diff for absolute paths under `/home/` or other personal identifiers, and flag any that are intentional.
