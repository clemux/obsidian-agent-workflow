#!/usr/bin/env python3
"""Compare every Typer help surface in the checkout and installed OAW CLIs."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from typer.main import get_command

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKOUT = ROOT / "bin" / "oaw"
PYTHON_INTERPRETER = re.compile(r"^(?:python|python3)(?:\d+(?:\.\d+)*)?$")
MAX_HELP_WORKERS = 8

sys.path.insert(0, str(ROOT / "src"))
from oaw import cli  # noqa: E402


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


def launcher_interpreter(launcher: Path) -> list[str]:
    try:
        first_line = launcher.open(encoding="utf-8").readline().rstrip("\r\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"cannot read installed oaw launcher {launcher}: {exc}") from exc
    if not first_line.startswith("#!"):
        raise SystemExit(f"installed oaw launcher has no interpreter: {launcher}")
    try:
        shebang = shlex.split(first_line[2:].strip())
    except ValueError as exc:
        raise SystemExit(
            f"installed oaw launcher has malformed shebang: {launcher}: {exc}"
        ) from exc
    if not shebang:
        raise SystemExit(f"installed oaw launcher has an empty shebang: {launcher}")

    command = Path(shebang[0])
    if command == Path("/usr/bin/env"):
        arguments = shebang[1:]
        if arguments[:1] == ["-S"]:
            arguments = arguments[1:]
        if not arguments:
            raise SystemExit(f"installed oaw launcher has malformed env shebang: {launcher}")
        interpreter_name = Path(arguments[0]).name
    elif command.is_absolute():
        interpreter_name = command.name
    else:
        raise SystemExit(
            f"installed oaw launcher has unsupported shebang: {launcher}: {first_line}"
        )
    if not PYTHON_INTERPRETER.fullmatch(interpreter_name):
        raise SystemExit(f"installed oaw launcher shebang is not Python: {launcher}: {first_line}")
    return shebang


def align_checkout_interpreter(checkout: list[str], installed: list[str]) -> list[str]:
    checkout_source = Path(checkout[-1]).resolve()
    if len(installed) > 1:
        interpreter = installed[:-1]
    else:
        interpreter = launcher_interpreter(Path(installed[0]).resolve())
    return [*interpreter, str(checkout_source)]


def help_result(prefix: list[str], path: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*prefix, *path, "--help"],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "COLUMNS": "80", "TERM": "dumb"},
    )


def checkout_command_paths() -> list[tuple[str, ...]]:
    """Walk the checkout Typer/Click tree without parsing rendered help text."""
    paths: list[tuple[str, ...]] = [()]

    def visit(command: Any, parent: tuple[str, ...]) -> None:
        for name, child in getattr(command, "commands", {}).items():
            path = (*parent, name)
            paths.append(path)
            visit(child, path)

    visit(get_command(cli.app), ())
    return paths


def display_path(path: tuple[str, ...]) -> str:
    return "oaw" + (" " + " ".join(path) if path else "") + " --help"


def source_path(prefix: list[str]) -> Path:
    executable = Path(prefix[-1] if len(prefix) > 1 else prefix[0]).resolve()
    try:
        launcher = executable.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"cannot read CLI source candidate {executable}: {exc}") from exc
    launcher_match = re.search(
        r"^from\s+(oaw_cli|oaw\.cli)\s+import\s+main(?:\s|#|$)",
        launcher,
        re.MULTILINE,
    )
    if launcher_match:
        module_name = launcher_match.group(1)
        interpreter = launcher_interpreter(executable)
        proc = subprocess.run(
            [
                *interpreter,
                "-c",
                (
                    "import importlib.util; "
                    f"spec = importlib.util.find_spec({module_name!r}); "
                    "print(spec.origin if spec and spec.origin else '')"
                ),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    filter(
                        None,
                        [
                            str(executable.parent.parent / "src"),
                            os.environ.get("PYTHONPATH", ""),
                        ],
                    )
                ),
            },
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise SystemExit(
                f"cannot locate installed {module_name} source via {executable}: "
                f"{proc.stderr.strip()}"
            )
        executable = Path(proc.stdout.strip()).resolve()
    return executable


def source_failure(checkout: list[str], installed: list[str]) -> str | None:
    checkout_source = source_path(checkout)
    installed_source = source_path(installed)
    try:
        checkout_bytes = checkout_source.read_bytes()
        installed_bytes = installed_source.read_bytes()
    except OSError as exc:
        raise SystemExit(f"cannot read CLI implementation source: {exc}") from exc
    if checkout_bytes == installed_bytes:
        return None
    checkout_hash = hashlib.sha256(checkout_bytes).hexdigest()
    installed_hash = hashlib.sha256(installed_bytes).hexdigest()
    return (
        "Source mismatch: installed artifact does not match checkout\n"
        f"  installed: {installed_source} ({installed_hash})\n"
        f"  checkout:  {checkout_source} ({checkout_hash})"
    )


def compare_surfaces(
    checkout: list[str],
    installed: list[str],
    paths: list[tuple[str, ...]] | None = None,
) -> tuple[int, list[str]]:
    """Byte-compare help output for every checkout Typer command path."""
    paths = checkout_command_paths() if paths is None else paths
    failures: list[str] = []
    checked = 0
    with ThreadPoolExecutor(max_workers=MAX_HELP_WORKERS) as executor:
        results = [
            (
                path,
                executor.submit(help_result, checkout, path),
                executor.submit(help_result, installed, path),
            )
            for path in paths
        ]
        for path, checkout_future, installed_future in results:
            checkout_result = checkout_future.result()
            installed_result = installed_future.result()
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
    checkout = align_checkout_interpreter(checkout, installed)
    checked, failures = compare_surfaces(checkout, installed)
    if mismatch := source_failure(checkout, installed):
        failures.insert(0, mismatch)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        print(
            f"Parity: failed ({len(failures)} checks; {checked} help surfaces scanned)",
            file=sys.stderr,
        )
        return 1
    print(f"Parity: ok ({checked} help surfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
