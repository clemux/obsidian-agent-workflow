"""Pure note-boundary helpers, note reads, and atomic note writes."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from .errors import OawError


def split_note(text: str) -> tuple[str, str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            before = "".join(lines[: idx + 1])
            fm = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return before, fm, body
    return "", "", text


def read_note(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    _, fm, body = split_note(text)
    return text, fm, body


class VaultTransaction:
    """Atomically replace a group of files, restoring all originals on failure."""

    def __init__(self) -> None:
        self.changes: dict[Path, str] = {}

    def stage(self, path: Path, text: str) -> None:
        self.changes[path] = text

    def commit(self, replace: Callable[[str, str], None] = os.replace) -> None:
        originals = {path: path.read_bytes() if path.exists() else None for path in self.changes}
        written: list[Path] = []
        temps: list[Path] = []
        try:
            for path, text in self.changes.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=path.parent, delete=False
                ) as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temp = Path(handle.name)
                temps.append(temp)
                replace(str(temp), str(path))
                written.append(path)
            touched_dirs = {path.parent for path in self.changes}
            for directory in touched_dirs:
                fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception as exc:
            for path in reversed(written):
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise OawError(f"transaction failed and was rolled back: {exc}") from exc
        finally:
            for temp in temps:
                temp.unlink(missing_ok=True)


def heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+\S", line)
    return len(match.group(1)) if match else None


def fence_delimiter(line: str) -> str | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
    return match.group(1)[0] if match else None


def normalize_heading(section: str) -> str:
    value = section.strip()
    if not value:
        raise OawError("section heading must not be empty")
    if value.startswith("#"):
        if not heading_level(value):
            raise OawError("section heading must look like a Markdown heading")
        return value
    return f"## {value}"


def append_markdown_block_to_section(text: str, section: str, block: str) -> str:
    heading = normalize_heading(section)
    block = block.strip()
    if not block:
        raise OawError("block content must not be empty")
    lines = text.splitlines()
    target_idx: int | None = None
    target_level = heading_level(heading)
    if target_level is None:
        raise OawError("section heading must look like a Markdown heading")
    active_fence: str | None = None
    for idx, line in enumerate(lines):
        delimiter = fence_delimiter(line)
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif active_fence == delimiter:
                active_fence = None
            continue
        if active_fence is None and line.strip() == heading:
            target_idx = idx
            break
    if target_idx is None:
        prefix = "" if text.endswith("\n") else "\n"
        return f"{text}{prefix}\n{heading}\n\n{block}\n"

    insert_at = len(lines)
    active_fence = None
    for idx in range(target_idx + 1, len(lines)):
        delimiter = fence_delimiter(lines[idx])
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif active_fence == delimiter:
                active_fence = None
            continue
        if active_fence is not None:
            continue
        level = heading_level(lines[idx])
        if level is not None and level <= target_level:
            insert_at = idx
            break

    before = lines[:insert_at]
    after = lines[insert_at:]
    while before and before[-1] == "":
        before.pop()
    new_lines = [*before, "", block, ""]
    if after:
        new_lines.extend(after)
    return "\n".join(new_lines).rstrip() + "\n"
