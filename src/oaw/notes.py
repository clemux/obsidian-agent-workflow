"""Pure note-boundary helpers, note reads, and atomic note writes."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TextIO

from oaw.document import editing as _editing
from oaw.document import parse_note_source

from .errors import OawError


@dataclass(frozen=True)
class FileSnapshot:
    """Bytes and filesystem identity captured for an optimistic write."""

    data: bytes
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int


def capture_file_snapshot(path: Path) -> FileSnapshot:
    """Capture one regular, non-symlink file for a preconditioned transaction."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OawError(f"could not inspect file for transaction: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OawError(f"transaction source must be a regular, non-symlink file: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OawError(f"could not read file for transaction: {path}") from exc
    return FileSnapshot(
        data=data,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IMODE(metadata.st_mode),
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
    )


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
    """Apply preconditioned file changes, restoring all originals on failure.

    Each individual publication is filesystem-atomic. The group is protected by
    optimistic preconditions and best-effort rollback, but is deliberately not a
    crash-recoverable journal.
    """

    def __init__(self) -> None:
        self.changes: dict[Path, str] = {}
        self.expected: dict[Path, str | bytes | FileSnapshot] = {}
        self.creates: dict[Path, str] = {}
        self.deletes: dict[Path, str | bytes | FileSnapshot] = {}
        self.move: tuple[Path, Path, str, str | bytes | FileSnapshot] | None = None

    def stage(
        self,
        path: Path,
        text: str,
        expected: str | bytes | FileSnapshot | None = None,
    ) -> None:
        self.changes[path] = text
        if expected is not None:
            self.expected[path] = expected

    def stage_create(self, path: Path, text: str) -> None:
        """Create ``path`` without replacing a racing destination."""
        self.creates[path] = text

    def stage_delete(self, path: Path, expected: str | bytes | FileSnapshot) -> None:
        """Delete ``path`` only while it still matches ``expected``."""
        self.deletes[path] = expected

    def stage_move(
        self,
        source: Path,
        destination: Path,
        text: str,
        expected: str | bytes | FileSnapshot,
    ) -> None:
        """Publish changed content at ``destination`` and delete ``source`` last."""
        if self.move is not None:
            raise OawError("a vault transaction supports at most one move")
        self.move = (source, destination, text, expected)

    @staticmethod
    def _matches(path: Path, expected: str | bytes | FileSnapshot) -> bool:
        try:
            if isinstance(expected, FileSnapshot):
                metadata = path.lstat()
                return (
                    stat.S_ISREG(metadata.st_mode)
                    and not stat.S_ISLNK(metadata.st_mode)
                    and metadata.st_dev == expected.device
                    and metadata.st_ino == expected.inode
                    and stat.S_IMODE(metadata.st_mode) == expected.mode
                    and metadata.st_size == expected.size
                    and metadata.st_mtime_ns == expected.mtime_ns
                    and path.read_bytes() == expected.data
                )
            if isinstance(expected, bytes):
                return path.read_bytes() == expected
            return path.read_text(encoding="utf-8") == expected
        except (OSError, UnicodeError):
            return False

    @classmethod
    def _verify(cls, path: Path, expected: str | bytes | FileSnapshot) -> None:
        if not cls._matches(path, expected):
            raise OawError(f"note changed on disk since it was read: {path}")

    @staticmethod
    def _same_entry(left: Path, right: Path) -> bool:
        try:
            return os.path.samefile(left, right)
        except OSError:
            return False

    @staticmethod
    def _write_temp(path: Path, text: str, mode: int | None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(text)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
            return Path(handle.name)

    @staticmethod
    def _restore(path: Path, data: bytes, mode: int | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mode is not None:
            path.chmod(mode)

    def commit(
        self,
        replace: Callable[[str, str], None] = os.replace,
        *,
        postcondition: Callable[[], None] | None = None,
    ) -> None:
        occupied = set(self.changes) | set(self.creates) | set(self.deletes)
        if self.move is not None:
            source, destination, _, _ = self.move
            if source in occupied or destination in occupied:
                raise OawError("move paths overlap another staged transaction operation")

        for path, expected in self.expected.items():
            self._verify(path, expected)
        for path in self.creates:
            if path.exists() or path.is_symlink():
                raise OawError(f"transaction destination already exists: {path}")
        for path, expected in self.deletes.items():
            self._verify(path, expected)
        if self.move is not None:
            source, destination, _, expected = self.move
            self._verify(source, expected)
            if (destination.exists() or destination.is_symlink()) and not self._same_entry(
                source, destination
            ):
                raise OawError(f"transaction destination already exists: {destination}")

        originals: dict[Path, bytes | None] = {}
        original_modes: dict[Path, int | None] = {}
        for path in [*self.changes, *self.creates, *self.deletes]:
            originals[path] = path.read_bytes() if path.exists() else None
            original_modes[path] = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        if self.move is not None:
            source, destination, _, expected = self.move
            source_snapshot = expected if isinstance(expected, FileSnapshot) else None
            originals[source] = (
                source_snapshot.data if source_snapshot is not None else source.read_bytes()
            )
            original_modes[source] = (
                source_snapshot.mode
                if source_snapshot is not None
                else stat.S_IMODE(source.stat().st_mode)
            )
            if not self._same_entry(source, destination):
                originals[destination] = None
                original_modes[destination] = None

        temps: list[Path] = []
        replacement_temps: dict[Path, Path] = {}
        create_temps: dict[Path, Path] = {}
        move_temp: Path | None = None
        written: list[Path] = []
        created: list[Path] = []
        deleted: list[Path] = []
        move_backup: Path | None = None
        move_source_deleted = False
        try:
            for path, text in self.changes.items():
                temp = self._write_temp(path, text, original_modes[path])
                replacement_temps[path] = temp
                temps.append(temp)
            for path, text in self.creates.items():
                temp = self._write_temp(path, text, None)
                create_temps[path] = temp
                temps.append(temp)
            if self.move is not None:
                source, destination, text, _ = self.move
                move_temp = self._write_temp(destination, text, original_modes[source])
                temps.append(move_temp)

                case_only = (
                    source.parent == destination.parent
                    and source.name != destination.name
                    and source.name.casefold() == destination.name.casefold()
                )
                if case_only:
                    self._verify(source, self.move[3])
                    with tempfile.NamedTemporaryFile(dir=source.parent, delete=False) as handle:
                        move_backup = Path(handle.name)
                    replace(str(source), str(move_backup))
                    move_source_deleted = True
                    os.link(str(move_temp), str(destination))
                    created.append(destination)
                    move_temp.unlink()
                else:
                    if destination.exists() or destination.is_symlink():
                        raise OawError(f"transaction destination already exists: {destination}")
                    os.link(str(move_temp), str(destination))
                    created.append(destination)
                    move_temp.unlink()

            for path, temp in replacement_temps.items():
                expected = self.expected.get(path)
                if expected is not None:
                    self._verify(path, expected)
                replace(str(temp), str(path))
                written.append(path)

            for path, temp in create_temps.items():
                if path.exists() or path.is_symlink():
                    raise OawError(f"transaction destination already exists: {path}")
                os.link(str(temp), str(path))
                created.append(path)
                temp.unlink()

            for path, expected in self.deletes.items():
                self._verify(path, expected)
                path.unlink()
                deleted.append(path)

            if self.move is not None and not move_source_deleted:
                source, _, _, expected = self.move
                self._verify(source, expected)
                source.unlink()
                deleted.append(source)
                move_source_deleted = True

            if postcondition is not None:
                postcondition()

            touched_dirs = {path.parent for path in [*self.changes, *self.creates, *self.deletes]}
            if self.move is not None:
                touched_dirs.update({self.move[0].parent, self.move[1].parent})
            for directory in touched_dirs:
                fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            if move_backup is not None:
                move_backup.unlink(missing_ok=True)
                move_backup = None
        except Exception as exc:
            for path in reversed(written):
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    self._restore(path, original, original_modes[path])
            for path in reversed(deleted):
                original = originals[path]
                assert original is not None
                self._restore(path, original, original_modes[path])
            for path in reversed(created):
                path.unlink(missing_ok=True)
            if move_backup is not None and self.move is not None:
                source = self.move[0]
                source.unlink(missing_ok=True)
                replace(str(move_backup), str(source))
                move_backup = None
            raise OawError(f"transaction failed and was rolled back: {exc}") from exc
        finally:
            for temp in temps:
                temp.unlink(missing_ok=True)
            if move_backup is not None:
                move_backup.unlink(missing_ok=True)


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
    """Append ``block`` to the section under ``section``, creating it if absent.

    Thin wrapper over :func:`oaw.document.editing.append_block_to_section`: parses
    ``text`` into a :class:`~oaw.document.model.NoteDocument`, delegates the
    splice, and returns the resulting source. See that function's docstring for
    the exact semantics (including CRLF preservation and protected-region
    refusals), which now differ slightly from this helper's historical
    whole-document ``"\\n".join(...)`` rejoin.
    """
    document = parse_note_source(text)
    return _editing.append_block_to_section(document, section, block).source
