"""Shared, importable test infrastructure for the oaw CLI suite.

This module is a plain library of helpers and vault factories (NOT pytest
fixtures) so that fixture-based tests and non-fixture callers can reuse exactly
the same building blocks. Each test file composes its own minimal ``vault`` and
``run_oaw`` fixtures from these factories, paying only for the notes it uses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from oaw import cli

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "oaw"
FIXTURES = ROOT / "tests" / "fixtures"

# --------------------------------------------------------------------------- #
# Process emulation and filesystem snapshots
# --------------------------------------------------------------------------- #


def run_oaw_in_process(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI via cli.main in this process, emulating the subprocess contract.

    The merged mapping replaces os.environ wholesale for the duration of the call,
    matching subprocess.run(env=...). Environment swapping assumes tests within one
    xdist worker run on a single thread. An exception cli.main does not translate is
    a programmer error, not CLI behavior: it propagates and fails the test instead
    of being downgraded to a subprocess-style nonzero exit.
    """
    stdout = StringIO()
    stderr = StringIO()
    saved_environ = os.environ.copy()
    os.environ.clear()
    os.environ.update(env)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = cli.main(args)
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)
    return subprocess.CompletedProcess(
        args=["oaw", *args],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def cli_env(vault: Path, **overrides: str) -> dict[str, str]:
    """Build a CLI environment for ``vault``: ambient env + OAW_VAULT + test session.

    Uses an ``os.environ`` copy plus ``CODEX_THREAD_ID=test-thread`` so every
    minimal-vault test starts from the same session identity. Keyword overrides
    are applied last.
    """
    env = os.environ.copy()
    env["OAW_VAULT"] = str(vault)
    env["CODEX_THREAD_ID"] = "test-thread"
    env.update(overrides)
    return env


def make_runner(vault: Path):
    """Return ``run(*args, env=None)`` bound to ``vault`` via :func:`cli_env`.

    The per-call ``env`` mapping overlays the base environment.
    """

    def run(*args: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = cli_env(vault)
        if env:
            merged.update(env)
        return run_oaw_in_process([str(arg) for arg in args], merged)

    return run


def run_oaw_subprocess(
    args: Sequence[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the checkout CLI through ``bin/oaw`` in a real subprocess.

    Reserved for tests whose subject is the process boundary itself (launcher
    resolution, filesystem effects of a genuinely separate process); everything
    else uses :func:`run_oaw_in_process`.
    """
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_record_for(vault: Path, session_id: str) -> Path:
    """Return the unique run record whose exact agent-session line matches."""
    identity_line = f'agent_session_id: "{session_id}"'
    matches = [
        path
        for path in (vault / "Agents/Runs").glob("*.md")
        if identity_line in path.read_text(encoding="utf-8").splitlines()
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one run record for {session_id}, found {len(matches)}: {matches}"
        )
    return matches[0]


def snapshot_tree_without_following_symlinks(
    root: Path,
) -> dict[str, tuple[str, bytes | str | None]]:
    """Snapshot every entry under ``root`` without following symlinks.

    Directories, symlinks, and regular files are all recorded, so this is the
    single source of truth for exact tree reproduction and diffing (structure,
    symlink targets, and file bytes).
    """
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in sorted([*directories, *files]):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.readlink(path))
            elif path.is_dir():
                snapshot[relative] = ("directory", None)
            else:
                snapshot[relative] = ("file", path.read_bytes())
    return snapshot


# --------------------------------------------------------------------------- #
# Session environment contract and presets
# --------------------------------------------------------------------------- #

# Kept independent from production so removing or renaming a supported harness
# cannot silently change both the implementation and its test oracle.
EXPECTED_SESSION_IDENTITIES = (
    ("codex", "Codex", "CODEX_THREAD_ID"),
    ("claude-code", "Claude Code", "CLAUDE_SESSION_ID"),
    ("claude-code", "Claude Code", "CLAUDE_CODE_SESSION_ID"),
    ("opencode", "OpenCode", "OPENCODE_SESSION_ID"),
    ("gemini", "Gemini", "GEMINI_SESSION_ID"),
)

# One active Codex thread; every other supported harness variable is set to the
# empty string so its harness is treated as inactive.
SESSION_ENV: dict[str, str] = {
    env_name: ("test-thread" if env_name == "CODEX_THREAD_ID" else "")
    for _, _, env_name in EXPECTED_SESSION_IDENTITIES
}

# Unset every supported harness session variable so test outcomes do not depend
# on which agent harness (if any) happens to be running the suite. The ``None``
# values are a CliRunner convention (unset the variable); this mapping must NOT
# be passed to run_oaw_in_process, which requires str values for os.environ.
NO_SESSION_ENV: dict[str, str | None] = {
    env_name: None for _, _, env_name in EXPECTED_SESSION_IDENTITIES
}


# --------------------------------------------------------------------------- #
# Outcome assertion helpers
# --------------------------------------------------------------------------- #


def _outcome(result: object) -> tuple[int, str, str]:
    """Normalize a click Result or subprocess.CompletedProcess to (code, out, err)."""
    if hasattr(result, "returncode"):
        return (
            result.returncode,  # type: ignore[attr-defined]
            result.stdout,  # type: ignore[attr-defined]
            result.stderr,  # type: ignore[attr-defined]
        )
    return (
        result.exit_code,  # type: ignore[attr-defined]
        result.stdout,  # type: ignore[attr-defined]
        result.stderr,  # type: ignore[attr-defined]
    )


def _streams(stdout: str, stderr: str) -> str:
    return f"\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def assert_ok(result: object, *, allow_stderr: bool = False) -> None:
    """Assert a successful invocation: exit code 0 and (by default) empty stderr.

    Pass ``allow_stderr=True`` for commands whose success contract includes
    stderr warnings (for example ``capture list`` reporting malformed notes).
    """
    code, stdout, stderr = _outcome(result)
    if code != 0:
        raise AssertionError(f"expected exit 0, got {code}{_streams(stdout, stderr)}")
    if not allow_stderr and stderr != "":
        raise AssertionError(f"expected empty stderr on success{_streams(stdout, stderr)}")


# --------------------------------------------------------------------------- #
# Filesystem helper
# --------------------------------------------------------------------------- #


def write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Composable vault factories
#
# Each factory takes an explicit vault root and writes one reusable test shape.
# --------------------------------------------------------------------------- #


def make_vault(root: Path) -> Path:
    """Ensure ``root`` exists as a bare vault directory and return it."""
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_frontmatter(project: str, task_id: str, status: str, tags: Sequence[str]) -> str:
    lines = [
        "---",
        "type: task",
        f"project: {project}",
        f"status: {status}",
        f"id: {task_id}",
        "aliases:",
        f"  - {task_id}",
    ]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {tag}" for tag in tags)
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n"


def add_task(
    root: Path,
    folder: str,
    filename: str,
    task_id: str,
    *,
    project: str,
    status: str = "todo",
    tags: Sequence[str] = (),
    body: str,
) -> Path:
    """Write a project task note under ``Projects/<folder>/Tasks/<filename>``.

    ``project`` is the frontmatter ``project:`` value (which may differ from the
    display ``folder``). ``body`` is the complete Markdown following the
    frontmatter block, including the ``# Title`` heading.
    """
    path = root / "Projects" / folder / "Tasks" / filename
    write(path, _task_frontmatter(project, task_id, status, tags) + body)
    return path


def add_agent_task(
    root: Path,
    filename: str,
    task_id: str,
    *,
    status: str = "open",
    body: str,
) -> Path:
    """Write a vault-wide agent task note under ``Agents/Tasks/<filename>``.

    Agent tasks carry no ``project:`` frontmatter. ``body`` is the complete
    Markdown following the frontmatter, including the ``# Title`` heading.
    """
    frontmatter = (
        f"---\ntype: task\nstatus: {status}\nid: {task_id}\naliases:\n  - {task_id}\n---\n\n"
    )
    path = root / "Agents" / "Tasks" / filename
    write(path, frontmatter + body)
    return path


def add_project_index(
    root: Path,
    name: str,
    alias_id: str,
    *,
    title: str | None = None,
) -> Path:
    """Write a project ``Index.md`` under ``Projects/<name>/``.

    ``alias_id`` is the frontmatter id and sole alias; the ``# <title>`` heading
    defaults to ``name`` when ``title`` is not given.
    """
    heading = name if title is None else title
    content = f"---\ntype: project\nid: {alias_id}\naliases:\n  - {alias_id}\n---\n\n# {heading}\n"
    path = root / "Projects" / name / "Index.md"
    write(path, content)
    return path


_RESEARCH_TEMPLATE = """---
type: research-prompt
project: {{project}}
track: {{track}}
title: {{title}}
created: {{date}}
---

# Prompt - {{title}}

## Running research sessions

## Local packet context

- Project: {{project}}
- Track: {{track}}

## Deep research prompt

```text
Research {{title}} for a reader with no access to local notes or files.

Precise questions:
1. Replace this placeholder with the research questions.

Deliverable: Replace this placeholder with the expected output format.
```
"""


def add_research_template(root: Path) -> Path:
    """Write ``Templates/Research packet.md`` used by the research workflow."""
    path = root / "Templates" / "Research packet.md"
    write(path, _RESEARCH_TEMPLATE)
    return path


_PROJECT_TEMPLATE = """---
type: project
project: example-project
status: active
repo: /path/to/repo
tags:
  - projects
---

# {{title}}

## Goal

Write the smallest useful description of the project outcome.

## Current state

- Status:
- Repo:
- Next action:

## Shared project workspace

![[Templates/Project workspace.base#Work queue]]

## Agent notes

Start here, then read active task notes before acting.
"""


def add_project_template(root: Path) -> Path:
    """Write ``Templates/Small project index.md`` used by project creation."""
    path = root / "Templates" / "Small project index.md"
    write(path, _PROJECT_TEMPLATE)
    return path


def add_captures(root: Path) -> list[Path]:
    """Write the legacy active and archived capture notes under the project Inbox."""
    active = root / "Projects" / "Obsidian Agent Workflow" / "Inbox" / "Active capture.md"
    write(
        active,
        "---\n"
        "type: capture\n"
        "project: obsidian-agent-workflow\n"
        "status: active\n"
        "id: OAW-CAP-active\n"
        "aliases:\n"
        "  - OAW-CAP-active\n"
        "---\n"
        "\n"
        "# Active capture\n",
    )
    archived = root / "Projects" / "Obsidian Agent Workflow" / "Inbox" / "Archived capture.md"
    write(
        archived,
        "---\n"
        "type: capture\n"
        "project: obsidian-agent-workflow\n"
        "status: archived\n"
        "id: OAW-CAP-archived\n"
        "aliases:\n"
        "  - OAW-CAP-archived\n"
        "---\n"
        "\n"
        "# Archived capture\n",
    )
    return [active, archived]
