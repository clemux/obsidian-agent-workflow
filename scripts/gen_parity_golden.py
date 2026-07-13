#!/usr/bin/env python3
"""Regenerate CLI parity goldens from the pre-cutover argparse git ref 3ddc859."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_typer_parity import (  # noqa: E402
    ACCEPTED_VALUE_CASES,
    DOMAIN_ERROR_TOKENS,
    PARITY_CASES,
    SESSION_ENVIRONMENT,
    FrontendResult,
    accepted_value_key,
    accepted_value_tokens,
    build_vault,
    recorded_result,
    render_arguments,
)

ARGPARSE_GIT_REF = "3ddc859"
DEFAULT_ARGPARSE_CHECKOUT = Path.home() / ".claude/jobs/29a6bdee/tmp/oaw-argparse"
DEFAULT_OUTPUT = ROOT / "tests/fixtures/cli_parity_golden.json"

# Execute the checkout's real bin/oaw launcher after freezing the datetime module.
# The old source is stdlib-only, so this runs under the bare Python interpreter.
ARGPARSE_SHIM = r"""
import datetime as dt
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
arguments = sys.argv[2:]

class FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 13)

class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 13, 12, 0, tzinfo=dt.timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)

dt.date = FixedDate
dt.datetime = FixedDateTime
launcher = root / "bin/oaw"
sys.argv = [str(launcher), *arguments]
runpy.run_path(str(launcher), run_name="__main__")
"""


def argparse_result(
    python: str,
    checkout: Path,
    arguments: list[str],
    vault: Path,
) -> FrontendResult:
    environment = {**os.environ, "OAW_VAULT": str(vault), **SESSION_ENVIRONMENT}
    process = subprocess.run(
        [python, "-c", ARGPARSE_SHIM, str(checkout), *arguments],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    return FrontendResult(process.returncode, process.stdout, process.stderr)


def generate_invocation(
    python: str,
    checkout: Path,
    tokens: tuple[str, ...],
) -> dict[str, object]:
    with TemporaryDirectory(prefix="oaw-parity-") as temporary_directory:
        root = Path(temporary_directory)
        fixture = root / "fixture"
        vault = root / "vault"
        build_vault(fixture)
        copytree(fixture, vault)
        result = argparse_result(
            python,
            checkout,
            render_arguments(tokens, vault),
            vault,
        )
        return recorded_result(result, vault)


def source_commit(checkout: Path, expected_ref: str) -> str:
    launcher = checkout / "bin/oaw"
    if not launcher.is_file():
        raise SystemExit(f"argparse launcher not found: {launcher}")
    process = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(f"cannot resolve argparse checkout HEAD: {process.stderr.strip()}")
    commit = process.stdout.strip()
    if not commit.startswith(expected_ref):
        raise SystemExit(f"argparse checkout is at {commit}, expected git ref {expected_ref}")
    return commit


def generate(python: str, checkout: Path, expected_ref: str) -> dict[str, Any]:
    commit = source_commit(checkout, expected_ref)
    cases: dict[str, object] = {}
    for case in PARITY_CASES:
        cases[case.path] = {
            "representative": generate_invocation(python, checkout, case.representative),
            "error_shape": generate_invocation(python, checkout, case.error_shape),
        }

    accepted_values = {
        accepted_value_key(option, value): generate_invocation(
            python,
            checkout,
            accepted_value_tokens(option, value),
        )
        for option, value in ACCEPTED_VALUE_CASES
    }
    return {
        "schema": 1,
        "source": {
            "frontend": "argparse",
            "git_commit": commit,
            "git_ref": expected_ref,
        },
        "cases": cases,
        "supplemental": {
            "accepted_values": accepted_values,
            "domain_error": generate_invocation(
                python,
                checkout,
                DOMAIN_ERROR_TOKENS,
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "regenerate deterministic CLI parity goldens from the pre-cutover "
            f"argparse checkout at git ref {ARGPARSE_GIT_REF}"
        )
    )
    parser.add_argument(
        "argparse_checkout",
        nargs="?",
        type=Path,
        default=DEFAULT_ARGPARSE_CHECKOUT,
        help="checkout of the pre-cutover argparse git ref",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--python", default="python", help="bare Python for the stdlib-only CLI")
    parser.add_argument("--git-ref", default=ARGPARSE_GIT_REF)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    checkout = arguments.argparse_checkout.expanduser().resolve()
    golden = generate(arguments.python, checkout, arguments.git_ref)
    output = arguments.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(PARITY_CASES)} parity cases to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
