#!/usr/bin/env python3
"""Compare every argparse help surface in the checkout and installed OAW CLIs."""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKOUT = ROOT / "bin" / "oaw"


def command_prefix(value: str, label: str) -> list[str]:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        resolved = candidate.resolve()
        if os.access(resolved, os.X_OK):
            return [str(resolved)]
        return [sys.executable, str(resolved)]
    executable = shutil.which(value)
    if executable:
        return [executable]
    raise SystemExit(f"{label} command not found: {value}")


def help_result(prefix: list[str], path: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*prefix, *path, "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def subcommands(help_text: str) -> list[str]:
    in_positionals = False
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped == "positional arguments:":
            in_positionals = True
            continue
        if in_positionals and stripped.endswith(":"):
            break
        if in_positionals and stripped.startswith("{") and "}" in stripped:
            choices = stripped[1 : stripped.index("}")]
            return [choice for choice in choices.split(",") if choice]
    return []


def display_path(path: tuple[str, ...]) -> str:
    return "oaw" + (" " + " ".join(path) if path else "") + " --help"


def compare_surfaces(checkout: list[str], installed: list[str]) -> tuple[int, list[str]]:
    queue: deque[tuple[str, ...]] = deque([()])
    seen: set[tuple[str, ...]] = set()
    failures: list[str] = []
    checked = 0
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        checkout_result = help_result(checkout, path)
        installed_result = help_result(installed, path)
        checked += 1
        checkout_output = checkout_result.stdout + checkout_result.stderr
        installed_output = installed_result.stdout + installed_result.stderr
        if (
            checkout_result.returncode != installed_result.returncode
            or checkout_output != installed_output
        ):
            diff = "".join(
                difflib.unified_diff(
                    installed_output.splitlines(keepends=True),
                    checkout_output.splitlines(keepends=True),
                    fromfile="installed",
                    tofile="checkout",
                )
            )
            failures.append(
                f"Mismatch: {display_path(path)} "
                f"(installed rc={installed_result.returncode}, "
                f"checkout rc={checkout_result.returncode})\n{diff}"
            )
        if checkout_result.returncode == 0:
            for child in subcommands(checkout_result.stdout):
                queue.append((*path, child))
    return checked, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="compare recursive --help output for checkout and installed oaw"
    )
    parser.add_argument("--checkout", default=str(DEFAULT_CHECKOUT))
    parser.add_argument("--installed", default="oaw")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checkout = command_prefix(args.checkout, "checkout")
    installed = command_prefix(args.installed, "installed")
    checked, failures = compare_surfaces(checkout, installed)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        print(f"Parity: failed ({len(failures)}/{checked} help surfaces differ)", file=sys.stderr)
        return 1
    print(f"Parity: ok ({checked} help surfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
