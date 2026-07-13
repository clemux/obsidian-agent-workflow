from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from shutil import copytree

import pytest
from typer.testing import CliRunner

from oaw import cli, snapshot, typer_cli

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / ".codex-evidence" / "t1-command-inventory.txt"
SESSION_ENVIRONMENT = {
    "CODEX_THREAD_ID": "",
    "CLAUDE_SESSION_ID": "",
    "CLAUDE_CODE_SESSION_ID": "",
    "OPENCODE_SESSION_ID": "",
    "GEMINI_SESSION_ID": "",
}
SNAPSHOT_THREAD_ID = "019f48d7-39c2-7043-9c19-5a3565995898"


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> FixedDateTime:
        value = cls(2026, 7, 13, 12, 0, tzinfo=dt.timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)


@dataclass(frozen=True)
class ParityCase:
    path: str
    representative: tuple[str, ...]
    error_shape: tuple[str, ...]


PARITY_CASES = (
    ParityCase("oaw resolve", ("resolve", "--path", "PRT-TSK-cli"), ("resolve",)),
    ParityCase("oaw list", ("list", "--project", "Parity"), ("list",)),
    ParityCase(
        "oaw project",
        (
            "project",
            "create",
            "--name",
            "Group project",
            "--alias",
            "GRP",
            "--goal",
            "Goal",
            "--allow-missing-session-id",
        ),
        ("project", "unknown"),
    ),
    ParityCase(
        "oaw project create",
        (
            "project",
            "create",
            "--name",
            "Created",
            "--alias",
            "NEW",
            "--goal",
            "Goal",
            "--allow-missing-session-id",
        ),
        ("project", "create"),
    ),
    ParityCase(
        "oaw research",
        (
            "research",
            "scaffold",
            "--project",
            "Parity",
            "--track",
            "group-topic",
            "--title",
            "Group topic",
            "--date",
            "2026-07-13",
        ),
        ("research", "unknown"),
    ),
    ParityCase(
        "oaw research scaffold",
        (
            "research",
            "scaffold",
            "--project",
            "Parity",
            "--track",
            "topic",
            "--title",
            "Topic",
            "--date",
            "2026-07-13",
        ),
        ("research", "scaffold"),
    ),
    ParityCase(
        "oaw research start",
        (
            "research",
            "start",
            "--project",
            "Parity",
            "--track",
            "existing",
            "--source",
            "Test",
            "--url",
            "https://example.test/run",
        ),
        ("research", "start"),
    ),
    ParityCase(
        "oaw task",
        ("task", "start", "PRT-TSK-cli", "--note", "Group", "--allow-missing-session-id"),
        ("task", "unknown"),
    ),
    ParityCase(
        "oaw task backlog",
        ("task", "backlog", "PRT-TSK-cli", "--note", "Backlog", "--allow-missing-session-id"),
        ("task", "backlog"),
    ),
    ParityCase(
        "oaw task promote",
        ("task", "promote", "PRT-TSK-cli", "--note", "Promoted", "--allow-missing-session-id"),
        ("task", "promote"),
    ),
    ParityCase(
        "oaw task start",
        ("task", "start", "PRT-TSK-cli", "--note", "Started", "--allow-missing-session-id"),
        ("task", "start"),
    ),
    ParityCase(
        "oaw task complete",
        (
            "task",
            "complete",
            "PRT-TSK-cli",
            "--note",
            "Done",
            "--checks",
            "pytest",
            "--allow-missing-session-id",
        ),
        ("task", "complete"),
    ),
    ParityCase(
        "oaw task note",
        ("task", "note", "PRT-TSK-cli", "--note", "Noted", "--allow-missing-session-id"),
        ("task", "note"),
    ),
    ParityCase(
        "oaw task create",
        (
            "task",
            "create",
            "--project",
            "Parity",
            "--title",
            "Created task",
            "--status",
            "todo",
            "--priority",
            "2",
            "--effort",
            "M",
            "--allow-missing-session-id",
        ),
        ("task", "create", "--priority", "9"),
    ),
    ParityCase(
        "oaw note",
        ("note", "observe", "PRT-TSK-cli", "--title", "Group", "--body", "Body"),
        ("note", "unknown"),
    ),
    ParityCase(
        "oaw note session",
        ("note", "session", "PRT-TSK-cli", "--note", "Session", "--allow-missing-session-id"),
        ("note", "session"),
    ),
    ParityCase(
        "oaw note observe",
        ("note", "observe", "PRT-TSK-cli", "--title", "Observation", "--body", "Body"),
        ("note", "observe"),
    ),
    ParityCase(
        "oaw board",
        (
            "board",
            "add",
            "--column",
            "Next",
            "--link",
            "Projects/Parity/Tasks/CLI",
            "--title",
            "Group",
            "--why",
            "Parity",
            "--id",
            "PRT-TSK-group",
        ),
        ("board", "unknown"),
    ),
    ParityCase(
        "oaw board add",
        (
            "board",
            "add",
            "--column",
            "Next",
            "--link",
            "Projects/Parity/Tasks/CLI",
            "--title",
            "Added",
            "--why",
            "Parity",
            "--id",
            "PRT-TSK-added",
        ),
        ("board", "add"),
    ),
    ParityCase(
        "oaw board move",
        ("board", "move", "PRT-TSK-existing", "--column", "Done"),
        ("board", "move"),
    ),
    ParityCase("oaw board done", ("board", "done", "PRT-TSK-existing"), ("board", "done")),
    ParityCase(
        "oaw board ensure-backlog",
        ("board", "ensure-backlog", "--project", "Parity"),
        ("board", "ensure-backlog"),
    ),
    ParityCase(
        "oaw ingest",
        ("ingest", "safe-export", "--ingestion-root", "{vault}/incoming"),
        ("ingest", "unknown"),
    ),
    ParityCase(
        "oaw ingest safe-export",
        ("ingest", "safe-export", "--ingestion-root", "{vault}/incoming"),
        ("ingest", "safe-export", "--unknown"),
    ),
    ParityCase(
        "oaw link",
        ("link", "check", "PRT-TSK-cli", "PRT-TSK-linked"),
        ("link", "unknown"),
    ),
    ParityCase(
        "oaw link check", ("link", "check", "PRT-TSK-cli", "PRT-TSK-linked"), ("link", "check")
    ),
    ParityCase("oaw link list", ("link", "list", "PRT-TSK-cli"), ("link", "list")),
    ParityCase(
        "oaw link ensure", ("link", "ensure", "PRT-TSK-cli", "PRT-TSK-linked"), ("link", "ensure")
    ),
    ParityCase(
        "oaw link ensure-bidirectional",
        ("link", "ensure-bidirectional", "PRT-TSK-cli", "PRT-TSK-linked"),
        ("link", "ensure-bidirectional"),
    ),
    ParityCase("oaw link lint", ("link", "lint"), ("link", "lint", "--unknown")),
    ParityCase(
        "oaw export",
        ("export", "note", "PRT-TSK-export", "--output-root", "{vault}/group-exports"),
        ("export", "unknown"),
    ),
    ParityCase(
        "oaw export note",
        ("export", "note", "PRT-TSK-export", "--output-root", "{vault}/exports"),
        ("export", "note"),
    ),
    ParityCase(
        "oaw export validate",
        ("export", "validate", "{vault}/fixture-exports/PRT-TSK-export"),
        ("export", "validate"),
    ),
    ParityCase(
        "oaw session",
        (
            "session",
            "lookup",
            "missing-session",
            "--codex-root",
            "{vault}/codex",
            "--claude-root",
            "{vault}/claude",
        ),
        ("session", "unknown"),
    ),
    ParityCase(
        "oaw session lookup",
        (
            "session",
            "lookup",
            "missing-session",
            "--codex-root",
            "{vault}/codex",
            "--claude-root",
            "{vault}/claude",
        ),
        ("session", "lookup"),
    ),
    ParityCase(
        "oaw session snapshot",
        (
            "session",
            "snapshot",
            SNAPSHOT_THREAD_ID,
            "--codex-only",
            "--partial",
            "--slug",
            "parity",
            "--output-root",
            "{vault}/attachments",
            "--codex-root",
            "{vault}/codex",
            "--claude-root",
            "{vault}/claude",
            "--plugin-data-root",
            "{vault}/plugins",
        ),
        ("session", "snapshot"),
    ),
    ParityCase(
        "oaw retro",
        (
            "retro",
            "create",
            "--title",
            "Group retro",
            "--date",
            "2026-07-13",
            "--allow-missing-session-id",
        ),
        ("retro", "unknown"),
    ),
    ParityCase(
        "oaw retro create",
        (
            "retro",
            "create",
            "--title",
            "Parity retro",
            "--date",
            "2026-07-13",
            "--allow-missing-session-id",
        ),
        ("retro", "create"),
    ),
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_vault(vault: Path) -> None:
    write(
        vault / "Projects/Parity/Index.md",
        """---
type: project
project: parity
status: active
id: PRT-index
aliases:
  - PRT-index
---

# Parity
""",
    )
    write(
        vault / "Projects/Parity/Tasks/CLI.md",
        """---
type: task
project: parity
status: todo
id: PRT-TSK-cli
aliases:
  - PRT-TSK-cli
---

# CLI

## Related

[[Projects/Parity/Tasks/Linked|PRT-TSK-linked]]

## Agent sessions

""",
    )
    write(
        vault / "Projects/Parity/Tasks/Linked.md",
        """---
type: task
project: parity
status: todo
id: PRT-TSK-linked
aliases:
  - PRT-TSK-linked
---

# Linked
""",
    )
    write(
        vault / "Projects/Parity/Tasks/Export.md",
        """---
type: task
project: parity
status: todo
id: PRT-TSK-export
aliases:
  - PRT-TSK-export
export-scope: work
---

# Export
""",
    )
    write(
        vault / "Projects/Parity/Board.md",
        """---
type: board
project: parity
id: PRT-board
---

## Active

## Todo

- [ ] [[Tasks/CLI|CLI]] - PRT-TSK-cli

## Done
""",
    )
    write(
        vault / "Projects/Next steps.md",
        """---
type: board
id: NEXT-board
---

# Next steps

## Next

- [ ] [[Projects/Parity/Tasks/CLI|CLI]] - existing (PRT-TSK-existing)

## Done
""",
    )
    write(
        vault / "Templates/Small project index.md",
        """---
type: project
project: example
status: active
---

# {{title}}

## Goal

Template goal.

## Current state

Template state.
""",
    )
    write(
        vault / "Templates/Research packet.md",
        """---
type: research-prompt
project: {{project}}
track: {{track}}
title: {{title}}
created: {{date}}
---

# Prompt - {{title}}

## Running research sessions

## Local packet context

## Deep research prompt

```text
Research {{title}}.
```
""",
    )
    write(
        vault / "Projects/Parity/Research/existing/Prompt.md",
        """---
type: research-prompt
project: parity
track: existing
title: Existing
created: 2026-07-13
---

# Prompt - Existing

## Running research sessions

## Deep research prompt

```text
Research existing.
```
""",
    )
    write(
        vault / "incoming/Approved.md",
        """---
export-scope: personal
---

# Approved
""",
    )
    write(
        vault / "codex/2026/07/13" / f"rollout-2026-07-13T12-00-00-{SNAPSHOT_THREAD_ID}.jsonl",
        '{"timestamp":"2026-07-13T12:00:00.000Z","content":"parity"}\n',
    )
    (vault / "claude").mkdir(parents=True)
    (vault / "plugins").mkdir(parents=True)
    exported_note = """---
export-scope: work
---

# Export fixture
"""
    bundle = vault / "fixture-exports/PRT-TSK-export"
    write(bundle / "note.md", exported_note)
    write(
        bundle / "manifest.json",
        json.dumps(
            {
                "schema": "oaw-safe-export-v1",
                "target": "work",
                "exported_at": "2026-07-13T12:00:00+00:00",
                "source": {
                    "id": "PRT-TSK-export",
                    "path": "Projects/Parity/Tasks/Export.md",
                    "title": "Export",
                },
                "note": {
                    "path": "note.md",
                    "sha256": hashlib.sha256(exported_note.encode()).hexdigest(),
                    "size_bytes": len(exported_note.encode()),
                },
                "return_ingest": False,
                "artifacts": [],
            },
            indent=2,
        )
        + "\n",
    )


@dataclass(frozen=True)
class FrontendResult:
    returncode: int
    stdout: str
    stderr: str


def render_arguments(tokens: tuple[str, ...], vault: Path) -> list[str]:
    return [token.format(vault=vault) for token in tokens]


def run_argparse(
    arguments: list[str], vault: Path, monkeypatch: pytest.MonkeyPatch
) -> FrontendResult:
    stdout = StringIO()
    stderr = StringIO()
    with monkeypatch.context() as environment, redirect_stdout(stdout), redirect_stderr(stderr):
        environment.setenv("OAW_VAULT", str(vault))
        for name, value in SESSION_ENVIRONMENT.items():
            environment.setenv(name, value)
        try:
            returncode = cli.main(arguments)
        except SystemExit as exc:
            returncode = exc.code if isinstance(exc.code, int) else 1
    return FrontendResult(returncode, stdout.getvalue(), stderr.getvalue())


def run_typer(arguments: list[str], vault: Path) -> FrontendResult:
    result = CliRunner().invoke(
        typer_cli.app,
        arguments,
        prog_name="oaw",
        env={"OAW_VAULT": str(vault), **SESSION_ENVIRONMENT},
    )
    return FrontendResult(result.exit_code, result.stdout, result.stderr)


def exit_class(returncode: int) -> int:
    assert returncode in {0, 1, 2}
    return returncode


def normalized_file_bytes(path: Path, vault: Path) -> bytes:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    return text.replace(str(vault), "$VAULT").encode()


def filesystem_state(vault: Path) -> tuple[tuple[Path, ...], dict[Path, bytes]]:
    paths = sorted(vault.rglob("*"))
    directories = tuple(path.relative_to(vault) for path in paths if path.is_dir())
    files = {
        path.relative_to(vault): normalized_file_bytes(path, vault)
        for path in paths
        if path.is_file()
    }
    return directories, files


def normalized(value: str, vault: Path) -> str:
    return value.replace(str(vault), "$VAULT")


def assert_frontend_parity(
    tokens: tuple[str, ...],
    fixture: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_exit_class: int,
    expected_stderr_prefix: str | None = None,
) -> None:
    argparse_vault = work_root / "argparse"
    typer_vault = work_root / "typer"
    copytree(fixture, argparse_vault)
    copytree(fixture, typer_vault)

    argparse_result = run_argparse(
        render_arguments(tokens, argparse_vault), argparse_vault, monkeypatch
    )
    typer_result = run_typer(render_arguments(tokens, typer_vault), typer_vault)

    assert exit_class(argparse_result.returncode) == expected_exit_class
    assert exit_class(typer_result.returncode) == expected_exit_class
    assert normalized(argparse_result.stdout, argparse_vault) == normalized(
        typer_result.stdout, typer_vault
    )
    # Accepted delta (a): Click usage-error prose (including the usage block,
    # invalid-value wording, and mutual-exclusion option order) is deliberately
    # not compared. Accepted delta (b): no abbreviated options appear here.
    # Accepted delta (c): no help flag appears on an already-invalid command line.
    if argparse_result.returncode != 2:
        assert normalized(argparse_result.stderr, argparse_vault) == normalized(
            typer_result.stderr, typer_vault
        )
    if expected_stderr_prefix is not None:
        assert argparse_result.stderr.startswith(expected_stderr_prefix)
        assert typer_result.stderr.startswith(expected_stderr_prefix)
    assert filesystem_state(argparse_vault) == filesystem_state(typer_vault)


def test_parity_corpus_covers_every_t1_inventory_path() -> None:
    inventory = {
        line.strip() for line in INVENTORY.read_text(encoding="utf-8").splitlines() if line.strip()
    }
    cases = {case.path for case in PARITY_CASES}

    assert cases == inventory
    assert all(case.representative and case.error_shape for case in PARITY_CASES)


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("--status", "backlog"),
        ("--status", "todo"),
        ("--priority", "1"),
        ("--priority", "2"),
        ("--priority", "3"),
        ("--effort", "S"),
        ("--effort", "M"),
        ("--effort", "L"),
    ),
)
def test_task_create_accepted_value_sets_match(
    option: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot.dt, "datetime", FixedDateTime)
    fixture = tmp_path / "fixture"
    build_vault(fixture)
    tokens = (
        "task",
        "create",
        "--project",
        "Parity",
        "--title",
        f"Accepted {option} {value}",
        option,
        value,
        "--allow-missing-session-id",
    )

    assert_frontend_parity(
        tokens,
        fixture,
        tmp_path / "accepted-value",
        monkeypatch,
        expected_exit_class=0,
    )


def test_domain_oaw_error_stderr_exit_and_failure_state_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot.dt, "datetime", FixedDateTime)
    fixture = tmp_path / "fixture"
    build_vault(fixture)
    tokens = (
        "task",
        "start",
        "PRT-TSK-missing",
        "--note",
        "Must not write",
        "--allow-missing-session-id",
    )

    assert_frontend_parity(
        tokens,
        fixture,
        tmp_path / "domain-error",
        monkeypatch,
        expected_exit_class=1,
        expected_stderr_prefix=(
            "oaw: no note with frontmatter id or alias 'PRT-TSK-missing' under "
        ),
    )


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda case: case.path.removeprefix("oaw "))
def test_argparse_and_typer_parity_corpus(
    case: ParityCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot.dt, "datetime", FixedDateTime)
    fixture = tmp_path / "fixture"
    build_vault(fixture)

    assert_frontend_parity(
        case.representative,
        fixture,
        tmp_path / "representative",
        monkeypatch,
        expected_exit_class=0,
    )
    assert_frontend_parity(
        case.error_shape,
        fixture,
        tmp_path / "error",
        monkeypatch,
        expected_exit_class=2,
    )
