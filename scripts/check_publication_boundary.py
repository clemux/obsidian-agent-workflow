#!/usr/bin/env python3
"""Reject likely private machine data in tracked repository text."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

LOCAL_CONFIG_NAME = ".publication-boundary-local.json"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    rule: str


PERSONAL_HOME = re.compile(
    r"(?:/(?:home|Users)/(?!<(?:user|username)>/)(?!user/)(?!example/)"
    r"[A-Za-z0-9._-]+/)"
    r"|(?:\b[A-Za-z]:\\Users\\(?!<(?:user|username)>\\)(?!user\\)(?!example\\)"
    r"[^\\/:*?\"<>|\r\n]+\\)",
    re.IGNORECASE,
)
BASE_RULES = (Rule("personal-home", PERSONAL_HOME),)


class BoundaryError(ValueError):
    """Raised for malformed publication-boundary configuration."""


def repository_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return Path(result.stdout.strip())


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def tracked_text(root: Path, paths: Iterable[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for relative in paths:
        path = root / relative
        try:
            if path.is_symlink():
                files[relative] = os.readlink(path)
                continue
            raw = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in raw:
            continue
        try:
            files[relative] = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def build_local_rules(prefixes: list[str], markers: list[str]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    if prefixes:
        alternatives = "|".join(re.escape(value.strip()) for value in prefixes)
        rules.append(
            Rule(
                "private-reference-id",
                re.compile(rf"\b(?:{alternatives})-[A-Z0-9][A-Za-z0-9-]*\b"),
            )
        )
    if markers:
        alternatives = "|".join(re.escape(value.strip()) for value in markers)
        rules.append(
            Rule(
                "private-marker",
                re.compile(rf"(?<![A-Za-z0-9_])(?:{alternatives})(?![A-Za-z0-9_])"),
            )
        )
    return tuple(rules)


def load_local_rules(root: Path) -> tuple[Rule, ...]:
    path = root / LOCAL_CONFIG_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except json.JSONDecodeError as error:
        raise BoundaryError(f"invalid {LOCAL_CONFIG_NAME}: {error}") from error

    if not isinstance(data, dict) or data.get("version") != 1:
        raise BoundaryError(f"{LOCAL_CONFIG_NAME} must be an object with version 1")

    prefixes = data.get("private_reference_prefixes", [])
    markers = data.get("private_markers", [])
    for name, values in (
        ("private_reference_prefixes", prefixes),
        ("private_markers", markers),
    ):
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise BoundaryError(f"{LOCAL_CONFIG_NAME} {name} must be a string list")

    return build_local_rules(prefixes, markers)


def find_matches(files: dict[str, str], rules: Iterable[Rule]) -> list[Match]:
    matches: list[Match] = []
    for path, content in sorted(files.items()):
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule in rules:
                if rule.pattern.search(line):
                    matches.append(Match(path, line_number, rule.name))
    return matches


def main() -> int:
    try:
        root = repository_root()
        paths = tracked_paths(root)
        if LOCAL_CONFIG_NAME in paths:
            raise BoundaryError(f"{LOCAL_CONFIG_NAME} must remain untracked")
        files = tracked_text(root, paths)
        rules = BASE_RULES + load_local_rules(root)
        matches = find_matches(files, rules)
    except (BoundaryError, subprocess.CalledProcessError) as error:
        print(f"publication-boundary: configuration error: {error}", file=sys.stderr)
        return 2

    for match in matches:
        print(f"{match.path}:{match.line}: {match.rule}")
    if matches:
        print(
            "publication-boundary: failed; redact the match or adjust the local markers",
            file=sys.stderr,
        )
        return 1

    print(f"publication-boundary: passed ({len(files)} tracked text files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
