"""The versioned, supported-Obsidian compatibility profile, as data.

``oaw doctor`` (see :mod:`oaw.doctor`) checks a vault against exactly one
thing: the Obsidian version and settings this package has actually been
built and tested against. This module is the single source of truth for
that profile -- a plain-data description with no vault, filesystem, or CLI
concerns of its own -- so bumping the supported version, or changing which
settings are required versus informational, is a one-module, no-behavior-
surprise edit.

Obsidian's ``.obsidian/app.json`` omits a settings key entirely whenever it
sits at Obsidian's own built-in default; only a value that differs from the
default is ever written out. A doctor check that reads a missing key as
"unset" rather than "at its documented default" would misclassify most real
vaults, so every key this module (and :mod:`oaw.doctor`) inspects has its
Obsidian-side default recorded here in :data:`SETTING_DEFAULTS`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "INFORMATIONAL_SETTINGS",
    "RENAME_REWRITE_HAZARD",
    "REQUIRED_SETTINGS",
    "SETTING_DEFAULTS",
    "SUPPORTED_OBSIDIAN_VERSIONS",
    "VersionClassification",
    "classify_version",
    "resolve_setting",
]

#: Obsidian versions this package has actually been built and tested
#: against. A vault reporting a version outside this tuple is not
#: necessarily broken -- see :func:`classify_version` -- but it has not been
#: verified.
SUPPORTED_OBSIDIAN_VERSIONS: tuple[str, ...] = ("1.12.7",)

#: Settings ``oaw doctor`` treats as hard requirements: the mapped value is
#: the one and only value OAW considers safe. Any other value (including an
#: absence that resolves, via :data:`SETTING_DEFAULTS`, to something else)
#: is a FAIL, because OAW's editing layer assumes CommonMark-strict line
#: breaks throughout (see ``oaw.document.markdown``'s module docstring).
REQUIRED_SETTINGS: dict[str, bool] = {"strictLineBreaks": True}

#: Every setting key doctor.py inspects, mapped to Obsidian's own built-in
#: default -- the value in force whenever ``app.json`` omits the key.
SETTING_DEFAULTS: dict[str, object] = {
    "strictLineBreaks": False,
    "newLinkFormat": "shortest",
    "alwaysUpdateLinks": False,
}

#: Settings reported for visibility only: their value is always PASS on its
#: own (the write-path hazard these two settings can jointly create is
#: reported separately, by ``oaw.doctor``, using the same resolved values).
INFORMATIONAL_SETTINGS: tuple[str, ...] = ("newLinkFormat", "alwaysUpdateLinks")

#: Human-readable description of the one settings *combination* doctor.py
#: warns about explicitly, named so the WARN detail string and any test
#: asserting on it share one source of truth.
RENAME_REWRITE_HAZARD = (
    "newLinkFormat=shortest (the default) combined with alwaysUpdateLinks=true "
    "is a write-path hazard: Obsidian 1.12.7 rewrites canonical path-form "
    "wikilinks to shortest form on rename, which can silently change link "
    "targets an OAW-driven rename did not intend to touch."
)


class VersionClassification(StrEnum):
    """How one reported Obsidian version relates to :data:`SUPPORTED_OBSIDIAN_VERSIONS`."""

    SUPPORTED = "supported"
    NEWER_UNTESTED = "newer-untested"
    OLDER_UNTESTED = "older-untested"
    UNKNOWN = "unknown"


def classify_version(version: str | None) -> VersionClassification:
    """Classify ``version`` against :data:`SUPPORTED_OBSIDIAN_VERSIONS`.

    ``None`` or a string that does not parse as a dotted sequence of
    integers (``"1.12.7"``-shaped) is UNKNOWN -- doctor.py reports this as a
    WARN, never a FAIL, because the installed Obsidian version cannot be
    probed portably (see ``oaw.doctor`` module docstring).
    """
    if version is None:
        return VersionClassification.UNKNOWN
    parsed = _parse_version(version)
    if parsed is None:
        return VersionClassification.UNKNOWN
    if version in SUPPORTED_OBSIDIAN_VERSIONS:
        return VersionClassification.SUPPORTED

    supported_parsed = [
        p for p in (_parse_version(v) for v in SUPPORTED_OBSIDIAN_VERSIONS) if p is not None
    ]
    if not supported_parsed:  # pragma: no cover - SUPPORTED_OBSIDIAN_VERSIONS is never empty
        return VersionClassification.UNKNOWN

    if parsed > max(supported_parsed):
        return VersionClassification.NEWER_UNTESTED
    return VersionClassification.OLDER_UNTESTED


def resolve_setting(settings: dict[str, object], key: str) -> object:
    """Look up ``key`` in a parsed ``app.json``, falling back to its Obsidian default.

    ``settings`` is the raw ``dict`` decoded from ``app.json`` (or an empty
    ``dict`` when the file is missing/unreadable); a key absent from it
    resolves to :data:`SETTING_DEFAULTS`, mirroring Obsidian's own
    write-only-when-non-default behavior for that file.
    """
    if key in settings:
        return settings[key]
    return SETTING_DEFAULTS.get(key)


def _parse_version(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    if not parts:
        return None
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None
