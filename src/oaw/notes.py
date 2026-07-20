"""Pure note-boundary helpers, note reads, and atomic note writes."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO, TextIO

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


def read_markdown_source(
    inline: str | None,
    body_file: str | None,
    stdin: TextIO,
    *,
    inline_option: str,
    file_option: str,
    label: str,
    empty_error: str,
    file_label: str | None = None,
) -> str:
    """Read one non-empty Markdown value without changing its contents.

    ``body_file == "-"`` deliberately reads standard input.  Callers that expose
    this helper through the CLI perform source-conflict checks first so invalid
    invocations neither consume stdin nor touch the vault.
    """
    if inline is not None and body_file is not None:
        raise OawError(f"{label} accepts exactly one of {inline_option} or {file_option}")
    if inline is None and body_file is None:
        raise OawError(f"{label} requires exactly one of {inline_option} or {file_option}")
    if body_file is not None:
        try:
            if body_file == "-":
                raw = stdin.read()
            else:
                with Path(body_file).open(encoding="utf-8", newline="") as handle:
                    raw = handle.read()
        except (OSError, UnicodeError) as exc:
            raise OawError(f"could not read {file_label or label} file: {body_file}") from exc
    else:
        assert inline is not None
        raw = inline
    if not raw.strip():
        raise OawError(empty_error)
    return raw


class VaultTransaction:
    """Atomically replace a group of files, restoring all originals on failure."""

    def __init__(self) -> None:
        self.changes: dict[Path, str] = {}
        self.expected: dict[Path, str | bytes] = {}

    def stage(self, path: Path, text: str, expected: str | bytes | None = None) -> None:
        self.changes[path] = text
        if expected is not None:
            self.expected[path] = expected

    def commit(self, replace: Callable[[str, str], None] = os.replace) -> None:
        for path, expected in self.expected.items():
            if not path.exists():
                current: str | bytes | None = None
            elif isinstance(expected, bytes):
                current = path.read_bytes()
            else:
                current = path.read_text(encoding="utf-8")
            if current != expected:
                raise OawError(f"note changed on disk since it was read: {path}")
        originals = {path: path.read_bytes() if path.exists() else None for path in self.changes}
        original_modes = {
            path: stat.S_IMODE(path.stat().st_mode) if path.exists() else None
            for path in self.changes
        }
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
                    original_mode = original_modes[path]
                    if original_mode is not None:
                        os.fchmod(handle.fileno(), original_mode)
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


def write_new_note_atomic(
    path: Path,
    text: str,
    *,
    link: Callable[[str, str], None] = os.link,
    write: Callable[[IO[str], str], None] | None = None,
    flush: Callable[[IO[str]], None] | None = None,
    fsync: Callable[[int], None] = os.fsync,
    mkdir: Callable[[Path], None] | None = None,
) -> None:
    """Atomically create one new note without ever replacing an existing path.

    The temporary file is linked into place, rather than replaced, so the
    filesystem rejects a racing creator with ``FileExistsError``. Any directory
    made solely for a failed creation is removed when still empty.
    """
    missing_directories: list[Path] = []
    directory = path.parent
    while not directory.exists():
        missing_directories.append(directory)
        directory = directory.parent
    temp: Path | None = None
    published = False
    created_directories: list[Path] = []
    try:
        for directory in reversed(missing_directories):
            try:
                if mkdir is None:
                    directory.mkdir()
                else:
                    mkdir(directory)
            except FileExistsError:
                if not directory.is_dir():
                    raise
            else:
                created_directories.append(directory)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temp = Path(handle.name)
            if write is None:
                handle.write(text)
            else:
                write(handle, text)
            if flush is None:
                handle.flush()
            else:
                flush(handle)
            fsync(handle.fileno())
        link(str(temp), str(path))
        published = True
        temp.unlink()
        temp = None
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        if temp is not None:
            temp.unlink(missing_ok=True)
        if published:
            path.unlink(missing_ok=True)
        for directory in reversed(created_directories):
            with suppress(OSError):
                directory.rmdir()
        raise


def heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+\S", line)
    return len(match.group(1)) if match else None


def fence_delimiter(line: str) -> str | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
    if match is None:
        return None
    delimiter = match.group(1)
    if delimiter[0] == "`" and "`" in line[match.end() :]:
        return None
    return delimiter


def fence_closes(opening: str, candidate_line: str) -> bool:
    """Return whether a whole line closes the fence opened by ``opening``.

    A fence only closes on a line that (aside from up to three leading
    spaces) consists solely of fence characters of the same kind and at
    least as long as the opening fence — matching-but-shorter fences, or
    fence-looking lines with trailing text, do not close it.
    """
    match = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", candidate_line.rstrip("\r\n"))
    if match is None:
        return False
    candidate = match.group(1)
    return opening[0] == candidate[0] and len(candidate) >= len(opening)


def normalize_heading(section: str) -> str:
    value = section.strip()
    if not value:
        raise OawError("section heading must not be empty")
    if value.startswith("#"):
        if not heading_level(value):
            raise OawError("section heading must look like a Markdown heading")
        return value
    return f"## {value}"


def locate_section(text: str, section: str) -> tuple[list[str], int, int] | None:
    """Find a fence-aware, heading-exact section boundary.

    Returns ``(lines, heading_index, section_end_index)`` where ``lines`` is
    ``text.splitlines()``, ``heading_index`` is the index of the matching
    heading line, and ``section_end_index`` is the index of the next
    heading at or above the same level (or ``len(lines)`` if none follows).
    Returns ``None`` when the heading is not found outside a fenced code
    block. Heading lines inside fenced code blocks, and lines that merely
    look like the heading after stripping leading whitespace (e.g. indented
    code), never match; trailing whitespace on an otherwise exact heading
    line is tolerated.
    """
    heading = normalize_heading(section)
    target_level = heading_level(heading)
    if target_level is None:
        raise OawError("section heading must look like a Markdown heading")
    lines = text.splitlines()
    target_idx: int | None = None
    active_fence: str | None = None
    for idx, line in enumerate(lines):
        delimiter = fence_delimiter(line)
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif fence_closes(active_fence, line):
                active_fence = None
            continue
        if active_fence is None and line.rstrip() == heading:
            target_idx = idx
            break
    if target_idx is None:
        return None

    section_end = len(lines)
    active_fence = None
    for idx in range(target_idx + 1, len(lines)):
        delimiter = fence_delimiter(lines[idx])
        if delimiter:
            if active_fence is None:
                active_fence = delimiter
            elif fence_closes(active_fence, lines[idx]):
                active_fence = None
            continue
        if active_fence is not None:
            continue
        level = heading_level(lines[idx])
        if level is not None and level <= target_level:
            section_end = idx
            break
    return lines, target_idx, section_end


def append_markdown_block_to_section(text: str, section: str, block: str) -> str:
    heading = normalize_heading(section)
    block = block.strip()
    if not block:
        raise OawError("block content must not be empty")
    located = locate_section(text, section)
    if located is None:
        prefix = "" if text.endswith("\n") else "\n"
        return f"{text}{prefix}\n{heading}\n\n{block}\n"
    lines, _target_idx, section_end = located

    before = lines[:section_end]
    after = lines[section_end:]
    while before and before[-1] == "":
        before.pop()
    new_lines = [*before, "", block, ""]
    if after:
        new_lines.extend(after)
    return "\n".join(new_lines).rstrip() + "\n"
