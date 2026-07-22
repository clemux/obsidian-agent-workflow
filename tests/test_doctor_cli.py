"""Per-domain CLI suite for ``oaw doctor`` (the Typer command, not the engine).

:mod:`tests.test_doctor` exercises :func:`oaw.doctor.run_doctor` directly; this
file only checks what the CLI layer adds on top: human/JSON output routing,
the ``--obsidian-version``/``OAW_OBSIDIAN_VERSION`` resolution the CLI owns,
the process exit code, the ``OAW_VAULT`` contract, and read-only behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import support
from tests.support import snapshot_tree_without_following_symlinks, write


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A vault whose ``.obsidian/app.json`` satisfies every required setting."""
    root = support.make_vault(tmp_path)
    write(
        root / ".obsidian/app.json",
        json.dumps({"strictLineBreaks": True}),
    )
    write(root / "Note.md", "## A Clean Note\n\nNothing wrong here.\n")
    return root


@pytest.fixture
def run_oaw(vault: Path):
    """Return an in-process runner bound to the compatible ``vault``."""
    return support.make_runner(vault)


# --- human output ---------------------------------------------------------------------


def test_doctor_human_output_lists_grouped_pass_checks(run_oaw):
    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert "Environment profile:" in proc.stdout
    assert "Parser integrity:" in proc.stdout
    assert "Vault compatibility:" in proc.stdout
    assert "PASS setting:strictLineBreaks" in proc.stdout
    assert "PASS obsidian-version" in proc.stdout


def test_doctor_human_output_reports_fail_for_incompatible_setting(run_oaw, vault):
    write(vault / ".obsidian/app.json", json.dumps({"strictLineBreaks": False}))

    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 1
    assert "FAIL setting:strictLineBreaks" in proc.stdout


# --- exit codes -------------------------------------------------------------------------


def test_doctor_exits_zero_for_a_fully_compatible_vault(run_oaw):
    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 0, proc.stderr


def test_doctor_exits_nonzero_when_any_check_fails(run_oaw, vault):
    write(vault / ".obsidian/app.json", json.dumps({"strictLineBreaks": False}))

    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 1


def test_doctor_warn_only_vault_still_exits_zero(run_oaw):
    # No --obsidian-version supplied at all: an unknown version is a WARN, and
    # WARN alone must never make the process exit non-zero.
    proc = run_oaw("doctor")

    assert proc.returncode == 0, proc.stderr
    assert "WARN obsidian-version" in proc.stdout


# --- --json ------------------------------------------------------------------------------


def test_doctor_json_matches_engine_payload_shape(run_oaw):
    proc = run_oaw("doctor", "--obsidian-version", "1.12.7", "--json")

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert set(payload) == {"exit_code", "environment", "parser", "vault", "vault_issues"}
    assert payload["exit_code"] == 0
    assert all(check["status"] == "pass" for check in payload["environment"])


def test_doctor_json_exit_code_matches_process_exit_code_on_fail(run_oaw, vault):
    write(vault / ".obsidian/app.json", json.dumps({"strictLineBreaks": False}))

    proc = run_oaw("doctor", "--obsidian-version", "1.12.7", "--json")

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["exit_code"] == 1


# --- OAW_VAULT contract ------------------------------------------------------------------


def test_doctor_requires_configured_vault(vault):
    env = support.cli_env(vault, OAW_VAULT="")

    proc = support.run_oaw_in_process(["doctor"], env)

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == "oaw: OAW_VAULT is required; set it to the Obsidian vault path\n"


# --- --obsidian-version / OAW_OBSIDIAN_VERSION resolution --------------------------------


def test_doctor_uses_obsidian_version_option(run_oaw):
    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert "PASS obsidian-version" in proc.stdout


def test_doctor_falls_back_to_obsidian_version_env_var(vault):
    env = support.cli_env(vault, OAW_OBSIDIAN_VERSION="1.12.7")

    proc = support.run_oaw_in_process(["doctor"], env)

    assert proc.returncode == 0, proc.stderr
    assert "PASS obsidian-version" in proc.stdout


def test_doctor_option_takes_precedence_over_env_var(vault):
    env = support.cli_env(vault, OAW_OBSIDIAN_VERSION="1.12.7")

    proc = support.run_oaw_in_process(["doctor", "--obsidian-version", "1.99.0"], env)

    assert "WARN obsidian-version" in proc.stdout
    assert "newer" in proc.stdout.lower()


def test_doctor_absent_version_and_env_var_warns_unknown(run_oaw):
    proc = run_oaw("doctor")

    assert proc.returncode == 0, proc.stderr
    assert "WARN obsidian-version" in proc.stdout
    assert "not supplied" in proc.stdout


# --- read-only behavior ------------------------------------------------------------------


def test_doctor_never_writes_the_vault(run_oaw, vault):
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 0, proc.stderr
    assert snapshot_tree_without_following_symlinks(vault) == before


def test_doctor_never_writes_the_vault_even_when_checks_fail(run_oaw, vault):
    write(vault / ".obsidian/app.json", json.dumps({"strictLineBreaks": False}))
    before = snapshot_tree_without_following_symlinks(vault)

    proc = run_oaw("doctor", "--obsidian-version", "1.12.7")

    assert proc.returncode == 1
    assert snapshot_tree_without_following_symlinks(vault) == before
