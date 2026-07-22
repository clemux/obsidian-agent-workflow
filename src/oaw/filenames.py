"""Portable filename and directory-component validation."""

from __future__ import annotations

import ntpath
import re
import unicodedata
from pathlib import Path

from .errors import OawError

RESERVED_FILENAME_CHARACTERS = frozenset('/\\:*?"<>|')


def portable_filename_component(value: str, label: str) -> str:
    """Return one NFC-normalized component or raise for a portability violation."""
    component = unicodedata.normalize("NFC", value)
    if not component:
        raise OawError(f"{label} must not be empty")
    if component != component.strip():
        raise OawError(f"{label} must not have surrounding whitespace or a trailing space")
    if component in {".", ".."}:
        raise OawError(f"{label} must not be '.' or '..'")
    if component.startswith("."):
        raise OawError(f"{label} must not start with a dot")
    if component.endswith("."):
        raise OawError(f"{label} must not end with a dot")
    if surrogate := next(
        (character for character in component if unicodedata.category(character) == "Cs"),
        None,
    ):
        raise OawError(f"{label} must not contain surrogate code point U+{ord(surrogate):04X}")
    if control := next(
        (character for character in component if unicodedata.category(character) == "Cc"),
        None,
    ):
        raise OawError(f"{label} must not contain control character U+{ord(control):04X}")
    if reserved := next(
        (character for character in component if character in RESERVED_FILENAME_CHARACTERS),
        None,
    ):
        raise OawError(f"{label} must not contain reserved filename character {reserved!r}")
    if ntpath.isreserved(component):
        raise OawError(f"{label} must not use a Windows reserved device name")
    return component


def portable_relative_path(value: str, label: str) -> Path:
    """Validate every component of a user-controlled relative output path."""
    if not value:
        raise OawError(f"{label} must not be empty")
    if Path(value).is_absolute():
        raise OawError(f"{label} must be a relative path")
    components = value.split("/")
    validated = [
        portable_filename_component(component, f"{label} component {index}")
        for index, component in enumerate(components, start=1)
    ]
    return Path(*validated)


def slugify_portable_fragment(value: str, *, fallback: str) -> str:
    """Return a conservative ASCII fragment for embedding in a named component.

    Callers add a fixed prefix or date before using the fragment as a complete
    filename or directory component, so a device-name-shaped fragment such as
    ``con`` cannot become a reserved component by itself.
    """
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or fallback
