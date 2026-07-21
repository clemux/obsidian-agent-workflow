import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from oaw import cli
from tests import support


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault: one project task (resolved by ID) and one agent task."""
    root = support.make_vault(tmp_path)
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )
    support.add_agent_task(
        root,
        "Resolve vault-wide Obsidian task IDs.md",
        "AGT-TSK-obsidian-task-ids",
        status="open",
        body="# Resolve vault-wide Obsidian task IDs\n\n## Problem\n\nText.\n",
    )
    return root


@pytest.fixture
def base_env(vault: Path) -> dict[str, str]:
    return support.cli_env(vault)


@pytest.fixture
def run_oaw(vault: Path):
    return support.make_runner(vault)


def test_bin_launcher_resolves_in_real_subprocess(vault: Path, base_env: dict[str, str]):
    result = support.run_oaw_subprocess(["resolve", "--path", "OAW-TSK-cli"], base_env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{vault / 'Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md'}\n"


def test_cli_main_accepts_argv_and_returns_status_code(vault: Path, monkeypatch):
    monkeypatch.setenv("OAW_VAULT", str(vault))
    stdout = StringIO()

    with redirect_stdout(stdout):
        returncode = cli.main(["resolve", "--path", "OAW-TSK-cli"])

    assert returncode == 0
    assert (
        stdout.getvalue() == f"{vault / 'Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md'}\n"
    )


def test_cli_main_translates_usage_exit_to_status_code():
    stderr = StringIO()

    with redirect_stderr(stderr):
        returncode = cli.main([])

    assert returncode == 2
    assert stderr.getvalue().startswith("Usage: oaw [OPTIONS] COMMAND [ARGS]...")
    assert "Error: Missing command." in stderr.getvalue()


def test_resolve_obs_prefix_to_json(run_oaw):
    proc = run_oaw("resolve", "--json", "obs:AGT-TSK-obsidian-task-ids")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["id"] == "AGT-TSK-obsidian-task-ids"
    assert data["matched_by"] == "id"
    assert "Agents/Tasks" in data["relative_path"]
