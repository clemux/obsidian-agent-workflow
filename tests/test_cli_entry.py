import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from oaw import cli
from tests import support


def test_bin_launcher_resolves_in_real_subprocess(legacy_vault: Path, base_env: dict[str, str]):
    result = support.run_oaw_subprocess(["resolve", "--path", "OAW-TSK-cli"], base_env)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout
        == f"{legacy_vault / 'Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md'}\n"
    )


def test_cli_main_accepts_argv_and_returns_status_code(legacy_vault: Path, monkeypatch):
    monkeypatch.setenv("OAW_VAULT", str(legacy_vault))
    stdout = StringIO()

    with redirect_stdout(stdout):
        returncode = cli.main(["resolve", "--path", "OAW-TSK-cli"])

    assert returncode == 0
    assert (
        stdout.getvalue()
        == f"{legacy_vault / 'Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md'}\n"
    )


def test_cli_main_translates_usage_exit_to_status_code():
    stderr = StringIO()

    with redirect_stderr(stderr):
        returncode = cli.main([])

    assert returncode == 2
    assert "the following arguments are required: command" in stderr.getvalue()


def test_no_command_is_usage_error_on_stderr(run_oaw):
    proc = run_oaw()

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "usage: oaw" in proc.stderr
    assert "the following arguments are required: command" in proc.stderr


def test_resolve_obs_prefix_to_json(run_oaw):
    proc = run_oaw("resolve", "--json", "obs:AGT-TSK-obsidian-task-ids")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["id"] == "AGT-TSK-obsidian-task-ids"
    assert data["matched_by"] == "id"
    assert "Agents/Tasks" in data["relative_path"]
