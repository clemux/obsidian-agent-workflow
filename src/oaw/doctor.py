"""Read-only compatibility diagnostics for one Obsidian vault: ``oaw doctor``.

This module is a pure engine: :func:`run_doctor` never prints and never
writes anything, it only reads the vault and the packaged compatibility
profile and returns an immutable :class:`DoctorReport`. A CLI frontend is
responsible for formatting that report and choosing the process exit code
from :attr:`DoctorReport.exit_code`.

Three check groups, run in this order:

1. **Environment profile** -- is the vault directory present, is the
   installed Obsidian version one this package has been tested against (see
   :mod:`oaw.document.profile`), and do the vault's own
   ``.obsidian/app.json`` settings satisfy the required/informational
   settings the profile declares.
2. **Parser integrity** -- a small, packaged self-check corpus (see
   :mod:`oaw.document.profile_fixtures`, read back at runtime via
   ``importlib.resources`` so it works from an installed wheel) is parsed
   with :func:`oaw.document.model.parse_note_source` and, where a fixture
   declares them, replayed through :mod:`oaw.document.editing`; any mismatch
   against the fixture's recorded expectation is this package's own
   markdown-it-py/PyYAML/Obsidian-recognizer behavior drifting from what it
   was built against.
3. **Vault compatibility** -- every ``*.md`` file under the vault (skipping
   dotfiles/dot-directories such as ``.obsidian`` and ``.trash``) is read as
   bytes, decoded, and parsed; undecodable bytes, an ``ERROR``-severity
   :class:`~oaw.document.types.Diagnostic`, a WARNING-severity diagnostic
   naming a write-safety condition (an unclosed frontmatter/fence/HTML-block/
   ``%%``-comment/math-block construct silently extends to end of region,
   which can make a later edit land somewhere unintended), or a wrong-shaped
   OAW-owned field (:func:`oaw.document.validate_owned_fields`, e.g. ``id:``
   written as a list or ``destinations:`` written as a scalar) on any one
   note becomes a WARN naming that note, never a FAIL -- a problem confined
   to one note does not disable OAW for the whole vault. The installed
   Obsidian version cannot be probed portably from here, so it is always
   supplied by the caller (``--obsidian-version`` or the
   ``OAW_OBSIDIAN_VERSION`` environment variable, both resolved by the CLI
   before calling :func:`run_doctor`); its absence is reported as an
   unknown-version WARN, not a FAIL.

Every check is PASS, WARN, or FAIL. :attr:`DoctorReport.exit_code` is
non-zero if and only if at least one check anywhere is FAIL -- doctor is
read-only and advisory otherwise, so WARN never fails the process.
"""

from __future__ import annotations

import importlib.resources
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from oaw.document import editing, validate_owned_fields
from oaw.document.model import parse_note_source
from oaw.document.profile import (
    INFORMATIONAL_SETTINGS,
    RENAME_REWRITE_HAZARD,
    REQUIRED_SETTINGS,
    SUPPORTED_OBSIDIAN_VERSIONS,
    VersionClassification,
    classify_version,
    resolve_setting,
)
from oaw.document.types import Severity
from oaw.errors import OawError

__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "NoteIssue",
    "Status",
    "run_doctor",
]

_APP_JSON_RELATIVE_PATH = Path(".obsidian") / "app.json"
_MAX_VAULT_ISSUES_IN_DETAIL = 20

#: WARNING-severity diagnostic codes that name a write-safety condition: an
#: unclosed protected construct silently extends to end-of-region/document,
#: which can make a later OAW edit land somewhere unintended. These are
#: surfaced as NoteIssues alongside ERROR diagnostics even though their own
#: severity is WARNING; every other WARNING-severity diagnostic keeps scanning
#: clean.
_WRITE_SAFETY_WARNING_CODES = frozenset(
    {
        "envelope.unclosed-frontmatter",
        "markdown.unclosed-fence",
        "markdown.unclosed-html-block",
        "obsidian.unclosed-comment",
        "obsidian.unclosed-math",
    }
)


class Status(StrEnum):
    """One check's outcome. Only FAIL affects :attr:`DoctorReport.exit_code`."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class DoctorCheck:
    """One named PASS/WARN/FAIL observation with a human-readable detail."""

    name: str
    status: Status
    detail: str

    def to_payload(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status.value, "detail": self.detail}


@dataclass(frozen=True)
class NoteIssue:
    """One problem on one vault note: undecodable bytes, an ``ERROR``-severity
    diagnostic, a write-safety WARNING diagnostic (see
    :data:`_WRITE_SAFETY_WARNING_CODES`), or a wrong-shaped OAW-owned field.

    Always surfaces as a WARN, never a FAIL: per the task design, a
    problematic individual note is reported but does not disable OAW
    globally.
    """

    path: str
    code: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class DoctorReport:
    """The full three-group result of one :func:`run_doctor` run."""

    environment: tuple[DoctorCheck, ...]
    parser: tuple[DoctorCheck, ...]
    vault: tuple[DoctorCheck, ...]
    vault_issues: tuple[NoteIssue, ...] = ()

    @property
    def exit_code(self) -> int:
        """Non-zero iff any check, in any group, is FAIL."""
        all_checks = (*self.environment, *self.parser, *self.vault)
        return 1 if any(check.status is Status.FAIL for check in all_checks) else 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "environment": [c.to_payload() for c in self.environment],
            "parser": [c.to_payload() for c in self.parser],
            "vault": [c.to_payload() for c in self.vault],
            "vault_issues": [issue.to_payload() for issue in self.vault_issues],
        }


def run_doctor(vault: Path, *, obsidian_version: str | None = None) -> DoctorReport:
    """Run every doctor check group against ``vault`` and return the report.

    ``obsidian_version`` is the already-resolved installed Obsidian version
    (from ``--obsidian-version`` or ``OAW_OBSIDIAN_VERSION``); ``None`` when
    neither was supplied, which is reported as an unknown-version WARN
    rather than treated as an error. This function never raises for
    ordinary vault problems (a missing vault directory, unreadable
    ``app.json``, undecodable notes) -- those are reported as checks.
    """
    environment = tuple(_check_environment(vault, obsidian_version))
    parser = tuple(_check_parser_integrity())
    vault_checks, vault_issues = _check_vault_compatibility(vault)
    return DoctorReport(
        environment=environment,
        parser=parser,
        vault=tuple(vault_checks),
        vault_issues=tuple(vault_issues),
    )


# --- group 1: environment profile ------------------------------------------------


def _check_environment(vault: Path, obsidian_version: str | None) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    if vault.is_dir():
        checks.append(DoctorCheck("vault-path", Status.PASS, f"vault directory found at {vault}"))
    else:
        checks.append(DoctorCheck("vault-path", Status.FAIL, f"vault directory not found: {vault}"))

    checks.append(_check_obsidian_version(obsidian_version))

    settings, app_json_detail = _read_app_json(vault / _APP_JSON_RELATIVE_PATH)
    if app_json_detail is not None:
        checks.append(DoctorCheck("app-json", Status.WARN, app_json_detail))
    else:
        checks.append(
            DoctorCheck("app-json", Status.PASS, f"read settings from {_APP_JSON_RELATIVE_PATH}")
        )

    for key, required_value in REQUIRED_SETTINGS.items():
        actual = resolve_setting(settings, key)
        if actual == required_value:
            checks.append(
                DoctorCheck(f"setting:{key}", Status.PASS, f"{key}={actual!r} (required)")
            )
        else:
            checks.append(
                DoctorCheck(
                    f"setting:{key}",
                    Status.FAIL,
                    f"{key}={actual!r}, but OAW requires {key}={required_value!r}",
                )
            )

    for key in INFORMATIONAL_SETTINGS:
        actual = resolve_setting(settings, key)
        checks.append(DoctorCheck(f"setting:{key}", Status.PASS, f"{key}={actual!r}"))

    checks.append(_check_rename_rewrite_hazard(settings))

    return checks


def _check_obsidian_version(obsidian_version: str | None) -> DoctorCheck:
    supported = ", ".join(SUPPORTED_OBSIDIAN_VERSIONS)
    classification = classify_version(obsidian_version)

    if classification is VersionClassification.SUPPORTED:
        return DoctorCheck(
            "obsidian-version",
            Status.PASS,
            f"Obsidian {obsidian_version} is a supported, tested version",
        )
    if classification is VersionClassification.NEWER_UNTESTED:
        return DoctorCheck(
            "obsidian-version",
            Status.WARN,
            f"Obsidian {obsidian_version} is newer than the tested profile ({supported}); "
            "not yet verified",
        )
    if classification is VersionClassification.OLDER_UNTESTED:
        return DoctorCheck(
            "obsidian-version",
            Status.WARN,
            f"Obsidian {obsidian_version} is older than the tested profile ({supported}); "
            "not verified",
        )
    if obsidian_version is None:
        detail = (
            "installed Obsidian version was not supplied; pass --obsidian-version or set "
            f"OAW_OBSIDIAN_VERSION to check against the supported profile ({supported})"
        )
    else:
        detail = f"could not parse reported Obsidian version {obsidian_version!r} as X.Y.Z"
    return DoctorCheck("obsidian-version", Status.WARN, detail)


def _check_rename_rewrite_hazard(settings: dict[str, object]) -> DoctorCheck:
    new_link_format = resolve_setting(settings, "newLinkFormat")
    always_update_links = resolve_setting(settings, "alwaysUpdateLinks")
    if new_link_format == "shortest" and always_update_links is True:
        return DoctorCheck("rename-rewrite-hazard", Status.WARN, RENAME_REWRITE_HAZARD)
    return DoctorCheck(
        "rename-rewrite-hazard",
        Status.PASS,
        f"newLinkFormat={new_link_format!r}, alwaysUpdateLinks={always_update_links!r}: "
        "not the rename-rewrite hazard combination",
    )


def _read_app_json(path: Path) -> tuple[dict[str, object], str | None]:
    """Return ``(settings, warn_detail)``; ``settings`` is ``{}`` on any problem."""
    if not path.is_file():
        return {}, f"no {path} found; Obsidian's built-in defaults apply for every setting"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"could not read {path}: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"could not parse {path} as JSON: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path} did not contain a JSON object"
    return data, None


# --- group 2: parser integrity ----------------------------------------------------


def _check_parser_integrity() -> list[DoctorCheck]:
    fixtures = _iter_packaged_fixtures()
    if not fixtures:
        return [
            DoctorCheck("packaged-fixtures", Status.FAIL, "no packaged profile fixtures were found")
        ]
    return [_check_one_fixture(name, source, expect) for name, source, expect in fixtures]


def _iter_packaged_fixtures() -> list[tuple[str, str, dict[str, Any]]]:
    root = importlib.resources.files("oaw.document.profile_fixtures")
    fixtures: list[tuple[str, str, dict[str, Any]]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        source_entry = entry / "source.md"
        expect_entry = entry / "expect.json"
        if not (source_entry.is_file() and expect_entry.is_file()):
            continue
        source = source_entry.read_bytes().decode("utf-8")
        expect = json.loads(expect_entry.read_text(encoding="utf-8"))
        fixtures.append((entry.name, source, expect))
    return fixtures


def _check_one_fixture(name: str, source: str, expect: dict[str, Any]) -> DoctorCheck:
    document = parse_note_source(source)

    problem = _check_parser_expectations(document, expect.get("parser") or {})
    if problem is not None:
        return DoctorCheck(name, Status.FAIL, f"fixture {name!r}: {problem}")

    for case in expect.get("editing") or []:
        problem = _check_editing_case(document, case)
        if problem is not None:
            return DoctorCheck(name, Status.FAIL, f"fixture {name!r}: {problem}")

    return DoctorCheck(name, Status.PASS, "matches packaged expectation")


def _check_parser_expectations(document: Any, parser_expect: dict[str, Any]) -> str | None:
    if "headings" in parser_expect:
        problem = _subset_problem(parser_expect["headings"], _actual_headings(document), "heading")
        if problem is not None:
            return problem
    if "protected" in parser_expect:
        problem = _subset_problem(
            parser_expect["protected"], _actual_protected(document), "protected region"
        )
        if problem is not None:
            return problem
    if "obsidian_spans" in parser_expect:
        problem = _subset_problem(
            parser_expect["obsidian_spans"], _actual_obsidian_spans(document), "obsidian span"
        )
        if problem is not None:
            return problem
    if "diagnostics" in parser_expect:
        actual_codes = {d.code for d in document.diagnostics}
        missing = sorted(set(parser_expect["diagnostics"]) - actual_codes)
        if missing:
            return f"expected diagnostics {missing} not present (found {sorted(actual_codes)})"
    if "newline" in parser_expect:
        mapping = {"lf": "\n", "crlf": "\r\n", "none": "\n"}
        expected_newline = mapping[parser_expect["newline"]]
        if document.newline != expected_newline:
            return f"expected newline {parser_expect['newline']!r}, got {document.newline!r}"
    return None


def _actual_headings(document: Any) -> list[dict[str, Any]]:
    return [
        {
            "level": heading.level,
            "text": heading.text,
            "line": document.index.offset_to_line(heading.span.start),
        }
        for heading in document.markdown.headings
    ]


def _actual_protected(document: Any) -> list[dict[str, Any]]:
    entries = []
    for region in document.protected_regions:
        start_line = document.index.offset_to_line(region.span.start)
        end_offset = max(region.span.start, region.span.end - 1)
        end_line = document.index.offset_to_line(end_offset)
        entries.append(
            {"kind": region.kind, "lines": [start_line, end_line], "closed": region.closed}
        )
    return entries


def _actual_obsidian_spans(document: Any) -> list[dict[str, Any]]:
    entries = []
    for span in document.obsidian_spans:
        entry: dict[str, Any] = {
            "kind": span.kind.value,
            "line": document.index.offset_to_line(span.span.start),
        }
        if span.target is not None:
            entry["target"] = span.target
        entries.append(entry)
    return entries


def _subset_problem(
    expected_entries: list[dict[str, Any]], actual_entries: list[dict[str, Any]], label: str
) -> str | None:
    for expected in expected_entries:
        found = any(
            all(actual.get(key) == value for key, value in expected.items())
            for actual in actual_entries
        )
        if not found:
            return f"expected {label} {expected} not found in {actual_entries}"
    return None


def _run_editing_op(document: Any, case: dict[str, Any]) -> Any:
    op = case["op"]
    if op == "append_block_to_section":
        return editing.append_block_to_section(document, case["heading"], case["block"])
    if op == "set_frontmatter_scalar":
        return editing.set_frontmatter_scalar(document, case["key"], case["value"])
    if op == "append_frontmatter_list_item":
        return editing.append_frontmatter_list_item(document, case["key"], case["value"])
    if op == "remove_frontmatter_list_item":
        return editing.remove_frontmatter_list_item(document, case["key"], case["value"])
    raise OawError(
        f"unknown packaged fixture editing op: {op!r}"
    )  # pragma: no cover - schema-guarded


def _check_editing_case(document: Any, case: dict[str, Any]) -> str | None:
    op = case["op"]
    if case["expect"] == "error":
        try:
            _run_editing_op(document, case)
        except OawError:
            return None
        return f"editing op {op!r} was expected to raise OawError but succeeded"

    try:
        result = _run_editing_op(document, case)
    except OawError as exc:
        return f"editing op {op!r} raised unexpectedly: {exc}"
    result_contains = case.get("result_contains")
    if result_contains is not None and result_contains not in result.source:
        return f"editing op {op!r} result did not contain {result_contains!r}"
    return None


# --- group 3: vault compatibility --------------------------------------------------


def _check_vault_compatibility(vault: Path) -> tuple[list[DoctorCheck], list[NoteIssue]]:
    if not vault.is_dir():
        return (
            [
                DoctorCheck(
                    "vault-scan",
                    Status.WARN,
                    f"vault directory not found: {vault}; skipping markdown scan",
                )
            ],
            [],
        )

    issues: list[NoteIssue] = []
    scanned = 0
    for path in _iter_markdown_files(vault, issues):
        scanned += 1
        issues.extend(_scan_one_note(vault, path))

    if not issues:
        return (
            [
                DoctorCheck(
                    "vault-scan",
                    Status.PASS,
                    f"scanned {scanned} markdown note(s), no issues found",
                )
            ],
            issues,
        )

    shown = [f"{issue.path}: {issue.code}" for issue in issues[:_MAX_VAULT_ISSUES_IN_DETAIL]]
    detail = f"scanned {scanned} markdown note(s), {len(issues)} issue(s) found: " + "; ".join(
        shown
    )
    if len(issues) > _MAX_VAULT_ISSUES_IN_DETAIL:
        detail += (
            f" (+{len(issues) - _MAX_VAULT_ISSUES_IN_DETAIL} more; see --json for the full list)"
        )
    return [DoctorCheck("vault-scan", Status.WARN, detail)], issues


def _scan_one_note(vault: Path, path: Path) -> list[NoteIssue]:
    rel = path.relative_to(vault).as_posix()

    # Checked before any read attempt so a symlinked note is never followed --
    # not even to discover that it happens to be unreadable or dangling.
    if path.is_symlink():
        return [NoteIssue(path=rel, code="vault.symlinked-note", message="symlinked note skipped")]

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [
            NoteIssue(path=rel, code="vault.unreadable-note", message=f"could not read note: {exc}")
        ]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [NoteIssue(path=rel, code="vault.invalid-utf8", message=f"not valid UTF-8: {exc}")]

    document = parse_note_source(text)
    issues = [
        NoteIssue(path=rel, code=diagnostic.code, message=diagnostic.message)
        for diagnostic in document.diagnostics
        if diagnostic.severity is Severity.ERROR or diagnostic.code in _WRITE_SAFETY_WARNING_CODES
    ]
    if document.frontmatter is not None:
        issues.extend(
            NoteIssue(path=rel, code=diagnostic.code, message=diagnostic.message)
            for diagnostic in validate_owned_fields(document.frontmatter)
        )
    return issues


def _iter_markdown_files(vault: Path, issues: list[NoteIssue]) -> Iterator[Path]:
    """Yield every ``*.md`` file under ``vault``, skipping dotfile directories.

    Pruning any directory whose name starts with ``.`` covers both
    ``.obsidian`` and ``.trash`` (and any other hidden directory) without
    naming them individually. A directory ``os.walk`` cannot list (e.g.
    permission denied) is recorded into ``issues`` as a WARN instead of
    raising and aborting the whole scan.
    """

    def _on_walk_error(error: OSError) -> None:
        offending = Path(error.filename) if error.filename else vault
        try:
            rel = offending.relative_to(vault).as_posix()
        except ValueError:
            rel = str(offending)
        issues.append(
            NoteIssue(
                path=rel, code="vault.walk-error", message=f"could not list directory: {error}"
            )
        )

    for root, dirnames, filenames in os.walk(vault, onerror=_on_walk_error):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if filename.endswith(".md"):
                yield Path(root) / filename
